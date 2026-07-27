"""Bounded, scheduler-independent maintenance for temporary demo state."""

from dataclasses import dataclass, field
from datetime import timedelta
import uuid

from django.conf import settings
from django.contrib.sessions.models import Session
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from members.models import (
    DemoCapacityCoordinator,
    DemoCapacityReservation,
    DemoOperationLease,
    DemoPreparedResetAttempt,
    DemoProvisioningAttempt,
    DemoSession,
    Organization,
    DEFAULT_ORGANIZATION_NAME,
)

from scheduler_project.settings_validation import (
    validate_demo_maintenance_settings,
)

from .demo_session_cleanup import (
    CleanupBatchResult,
    CleanupPlan,
    cleanup_expired_demo_sessions,
    plan_demo_session_cleanup,
)


MAX_CLEANUP_LIMIT = 100
MAX_ATTEMPT_LIMIT = 5000
MAX_AUXILIARY_LIMIT = 1000


class DemoMaintenanceAlreadyRunning(Exception):
    pass


@dataclass(frozen=True)
class PrunePlan:
    eligible: int
    selected: int
    backlog: int
    cutoff: object
    limit: int


@dataclass(frozen=True)
class PruneResult:
    plan: PrunePlan
    deleted: int = 0
    error_category: str = ''


@dataclass(frozen=True)
class FrameworkSessionResult:
    eligible: int
    deleted: int = 0
    error_category: str = ''


@dataclass(frozen=True)
class MaintenancePlan:
    created_at: object
    cleanup_cutoff: object
    retention_cutoff: object
    cleanup_limit: int
    attempt_limit: int
    auxiliary_limit: int
    cleanup: CleanupPlan
    cleanup_eligible: int
    cleanup_selected: int
    cleanup_backlog: int
    reservations: PrunePlan
    operation_leases: PrunePlan
    provisioning_attempts: PrunePlan
    reset_attempts: PrunePlan
    framework_sessions: FrameworkSessionResult
    active_maintenance_lease: bool


@dataclass
class DemoMaintenanceResult:
    started_at: object
    finished_at: object | None
    confirmed: bool
    lease_status: str
    plan: MaintenancePlan
    cleanup: CleanupBatchResult | None = None
    reservations: PruneResult | None = None
    operation_leases: PruneResult | None = None
    provisioning_attempts: PruneResult | None = None
    reset_attempts: PruneResult | None = None
    framework_sessions: FrameworkSessionResult | None = None
    errors: list[str] = field(default_factory=list)
    completion_category: str = 'planned'


def _settings_values():
    capacity = {
        name: getattr(settings, name)
        for name in (
            'DEMO_GLOBAL_START_WINDOW_SECONDS',
            'DEMO_CLIENT_START_WINDOW_SECONDS',
            'DEMO_PREPARED_RESET_WINDOW_SECONDS',
        )
    }
    maintenance = {
        name: getattr(settings, name)
        for name in (
            'DEMO_ATTEMPT_RETENTION_DAYS',
            'DEMO_MAINTENANCE_LEASE_SECONDS',
            'DEMO_MAINTENANCE_CLEANUP_LIMIT',
            'DEMO_MAINTENANCE_ATTEMPT_LIMIT',
            'DEMO_MAINTENANCE_AUXILIARY_LIMIT',
        )
    }
    return validate_demo_maintenance_settings(maintenance, capacity)


def _prune_plan(queryset, *, cutoff, limit):
    eligible = queryset.count()
    selected = min(eligible, limit)
    return PrunePlan(
        eligible=eligible,
        selected=selected,
        backlog=max(eligible - selected, 0),
        cutoff=cutoff,
        limit=limit,
    )


def plan_demo_maintenance(
    *,
    now=None,
    cleanup_cutoff=None,
    cleanup_limit=None,
    attempt_limit=None,
    auxiliary_limit=None,
):
    values = _settings_values()
    now = now or timezone.now()
    cleanup_cutoff = cleanup_cutoff or now
    if timezone.is_naive(now) or timezone.is_naive(cleanup_cutoff):
        raise ValueError('Maintenance timestamps must be timezone-aware.')
    if cleanup_limit is None:
        cleanup_limit = values['DEMO_MAINTENANCE_CLEANUP_LIMIT']
    if attempt_limit is None:
        attempt_limit = values['DEMO_MAINTENANCE_ATTEMPT_LIMIT']
    if auxiliary_limit is None:
        auxiliary_limit = values['DEMO_MAINTENANCE_AUXILIARY_LIMIT']
    if not 1 <= cleanup_limit <= MAX_CLEANUP_LIMIT:
        raise ValueError(f'Cleanup limit must be between 1 and {MAX_CLEANUP_LIMIT}.')
    if not 1 <= attempt_limit <= MAX_ATTEMPT_LIMIT:
        raise ValueError(f'Attempt limit must be between 1 and {MAX_ATTEMPT_LIMIT}.')
    if not 1 <= auxiliary_limit <= MAX_AUXILIARY_LIMIT:
        raise ValueError(
            f'Auxiliary limit must be between 1 and {MAX_AUXILIARY_LIMIT}.'
        )

    retention_cutoff = now - timedelta(
        days=values['DEMO_ATTEMPT_RETENTION_DAYS']
    )
    cleanup = plan_demo_session_cleanup(
        cutoff=cleanup_cutoff,
        limit=cleanup_limit,
    )
    cleanup_eligible_total = DemoSession.objects.filter(
        expires_at__lte=cleanup_cutoff,
        status__in=(
            DemoSession.Status.ACTIVE,
            DemoSession.Status.EXPIRING,
            DemoSession.Status.FAILED,
            DemoSession.Status.DELETING,
        ),
        organization__purpose=Organization.Purpose.TEMPORARY_DEMO,
        user__is_staff=False,
        user__is_superuser=False,
        user__organization_membership__organization=F('organization'),
    ).exclude(
        organization__name=DEFAULT_ORGANIZATION_NAME,
    ).count()
    cleanup_selected = sum(
        item.category == 'eligible' for item in cleanup.items
    )
    reservation_query = DemoCapacityReservation.objects.filter(
        expires_at__lte=now
    )
    lease_query = DemoOperationLease.objects.filter(expires_at__lte=now)
    provisioning_query = DemoProvisioningAttempt.objects.filter(
        created_at__lt=retention_cutoff
    )
    reset_query = DemoPreparedResetAttempt.objects.filter(
        created_at__lt=retention_cutoff
    )
    return MaintenancePlan(
        created_at=now,
        cleanup_cutoff=cleanup_cutoff,
        retention_cutoff=retention_cutoff,
        cleanup_limit=cleanup_limit,
        attempt_limit=attempt_limit,
        auxiliary_limit=auxiliary_limit,
        cleanup=cleanup,
        cleanup_eligible=cleanup_eligible_total,
        cleanup_selected=cleanup_selected,
        cleanup_backlog=max(cleanup_eligible_total - cleanup_selected, 0),
        reservations=_prune_plan(
            reservation_query,
            cutoff=now,
            limit=auxiliary_limit,
        ),
        operation_leases=_prune_plan(
            lease_query,
            cutoff=now,
            limit=auxiliary_limit,
        ),
        provisioning_attempts=_prune_plan(
            provisioning_query,
            cutoff=retention_cutoff,
            limit=attempt_limit,
        ),
        reset_attempts=_prune_plan(
            reset_query,
            cutoff=retention_cutoff,
            limit=attempt_limit,
        ),
        framework_sessions=FrameworkSessionResult(
            eligible=Session.objects.filter(expire_date__lte=now).count(),
        ),
        active_maintenance_lease=DemoOperationLease.objects.filter(
            operation=DemoOperationLease.MAINTENANCE,
            expires_at__gt=now,
        ).exists(),
    )


def acquire_maintenance_lease(*, now=None):
    now = now or timezone.now()
    values = _settings_values()
    with transaction.atomic():
        DemoCapacityCoordinator.objects.get_or_create(identifier='demo-capacity')
        DemoCapacityCoordinator.objects.select_for_update().get(
            identifier='demo-capacity'
        )
        DemoOperationLease.objects.filter(
            operation=DemoOperationLease.MAINTENANCE,
            expires_at__lte=now,
        ).delete()
        if DemoOperationLease.objects.filter(
            operation=DemoOperationLease.MAINTENANCE,
            expires_at__gt=now,
        ).exists():
            raise DemoMaintenanceAlreadyRunning(
                'A confirmed demo maintenance run is already active.'
            )
        lease = DemoOperationLease.objects.create(
            operation=DemoOperationLease.MAINTENANCE,
            token=uuid.uuid4(),
            acquired_at=now,
            expires_at=(
                now
                + timedelta(
                    seconds=values['DEMO_MAINTENANCE_LEASE_SECONDS']
                )
            ),
        )
    return lease.token


def release_maintenance_lease(token):
    return DemoOperationLease.objects.filter(
        operation=DemoOperationLease.MAINTENANCE,
        token=token,
    ).delete()[0] == 1


def _prune_queryset(model, filters, *, ordering, plan):
    try:
        with transaction.atomic():
            identifiers = list(
                model.objects.filter(**filters)
                .order_by(*ordering)
                .values_list('pk', flat=True)[: plan.limit]
            )
            deleted = model.objects.filter(pk__in=identifiers).delete()[0]
        return PruneResult(plan=plan, deleted=deleted)
    except Exception as error:
        return PruneResult(
            plan=plan,
            error_category=error.__class__.__name__,
        )


def prune_expired_reservations(plan):
    return _prune_queryset(
        DemoCapacityReservation,
        {'expires_at__lte': plan.cutoff},
        ordering=('expires_at', 'pk'),
        plan=plan,
    )


def prune_expired_operation_leases(plan):
    return _prune_queryset(
        DemoOperationLease,
        {'expires_at__lte': plan.cutoff},
        ordering=('expires_at', 'pk'),
        plan=plan,
    )


def prune_provisioning_attempts(plan):
    return _prune_queryset(
        DemoProvisioningAttempt,
        {'created_at__lt': plan.cutoff},
        ordering=('created_at', 'pk'),
        plan=plan,
    )


def prune_reset_attempts(plan):
    return _prune_queryset(
        DemoPreparedResetAttempt,
        {'created_at__lt': plan.cutoff},
        ordering=('created_at', 'pk'),
        plan=plan,
    )


def clear_expired_framework_sessions(plan):
    try:
        Session.get_session_store_class().clear_expired()
        remaining = Session.objects.filter(expire_date__lte=plan.created_at).count()
        return FrameworkSessionResult(
            eligible=plan.framework_sessions.eligible,
            deleted=max(plan.framework_sessions.eligible - remaining, 0),
        )
    except Exception as error:
        return FrameworkSessionResult(
            eligible=plan.framework_sessions.eligible,
            error_category=error.__class__.__name__,
        )


def run_demo_maintenance(*, plan, confirmed):
    result = DemoMaintenanceResult(
        started_at=timezone.now(),
        finished_at=None,
        confirmed=confirmed,
        lease_status='not_acquired',
        plan=plan,
    )
    if not confirmed:
        result.finished_at = timezone.now()
        return result

    lease_token = acquire_maintenance_lease(now=plan.created_at)
    result.lease_status = 'acquired'
    try:
        try:
            result.cleanup = cleanup_expired_demo_sessions(plan=plan.cleanup)
            if result.cleanup.failed:
                result.errors.append('demo_cleanup_failed')
        except Exception as error:
            result.errors.append(f'demo_cleanup:{error.__class__.__name__}')

        categories = (
            ('reservations', prune_expired_reservations, plan.reservations),
            (
                'operation_leases',
                prune_expired_operation_leases,
                plan.operation_leases,
            ),
            (
                'provisioning_attempts',
                prune_provisioning_attempts,
                plan.provisioning_attempts,
            ),
            ('reset_attempts', prune_reset_attempts, plan.reset_attempts),
        )
        for name, operation, category_plan in categories:
            try:
                category_result = operation(category_plan)
            except Exception as error:
                category_result = PruneResult(
                    plan=category_plan,
                    error_category=error.__class__.__name__,
                )
            setattr(result, name, category_result)
            if category_result.error_category:
                result.errors.append(
                    f'{name}:{category_result.error_category}'
                )

        try:
            result.framework_sessions = clear_expired_framework_sessions(plan)
        except Exception as error:
            result.framework_sessions = FrameworkSessionResult(
                eligible=plan.framework_sessions.eligible,
                error_category=error.__class__.__name__,
            )
        if result.framework_sessions.error_category:
            result.errors.append(
                'framework_sessions:'
                f'{result.framework_sessions.error_category}'
            )
    finally:
        release_maintenance_lease(lease_token)
        result.lease_status = 'released'
        result.finished_at = timezone.now()

    result.completion_category = (
        'partially_completed' if result.errors else 'completed'
    )
    return result
