"""Request-independent provisioning for isolated clean demo sessions."""

from dataclasses import dataclass
from datetime import timedelta
import uuid

from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from members.models import (
    DEFAULT_ORGANIZATION_NAME,
    DemoSession,
    Organization,
    OrganizationMembership,
)

from .models import (
    Certification,
    Course,
    Instructor,
    InstructorScheduleAvailability,
    InstructorScheduleParticipation,
    Locations,
    Schools,
    TheSched,
)
from .demo_capacity import (
    finish_demo_provisioning,
    internal_client_key,
    reserve_demo_provisioning_capacity,
    validate_provisioning_admission,
)


DEFAULT_CLEAN_DEMO_SESSION_LIFETIME = timedelta(hours=1)
MAX_CLEAN_DEMO_SESSION_LIFETIME = timedelta(hours=24)
IDENTITY_COLLISION_RETRY_LIMIT = 3


class CleanDemoProvisioningError(Exception):
    pass


class CleanDemoIdentityCollisionError(CleanDemoProvisioningError):
    pass


class CleanDemoLifetimeError(CleanDemoProvisioningError):
    pass


@dataclass(frozen=True)
class CleanDemoProvisioningResult:
    demo_session: DemoSession
    user: object
    organization: Organization
    membership: OrganizationMembership
    completed: bool
    expires_at: object


def _enforce_capacity_policy(*, requested_mode, admission=None):
    """Return one durable admission for a direct or HTTP provisioning caller."""
    admission = admission or reserve_demo_provisioning_capacity(
        requested_mode=requested_mode,
        client_key=internal_client_key(),
    )
    validate_provisioning_admission(
        admission,
        requested_mode=requested_mode,
    )
    return admission


def _identity_suffix(identity_factory):
    try:
        return uuid.UUID(str(identity_factory())).hex
    except (AttributeError, TypeError, ValueError) as error:
        raise CleanDemoProvisioningError(
            'The identity generator must return a UUID-compatible value.'
        ) from error


def _create_temporary_user(identity_factory):
    UserModel = get_user_model()
    for _attempt in range(IDENTITY_COLLISION_RETRY_LIMIT):
        username = f'demo_{_identity_suffix(identity_factory)}'
        try:
            with transaction.atomic():
                user = UserModel(
                    username=username,
                    is_active=True,
                    is_staff=False,
                    is_superuser=False,
                )
                user.set_unusable_password()
                user.save()
                return user
        except IntegrityError:
            continue
    raise CleanDemoIdentityCollisionError(
        'Could not generate a unique temporary demo username within the retry limit.'
    )


def _create_temporary_organization(identity_factory):
    for _attempt in range(IDENTITY_COLLISION_RETRY_LIMIT):
        name = f'Demo Session {_identity_suffix(identity_factory)}'
        configured_canonical = getattr(
            settings,
            'DEMO_ORGANIZATION_IDENTIFIER',
            '',
        ).strip()
        if name in {DEFAULT_ORGANIZATION_NAME, configured_canonical}:
            continue
        try:
            with transaction.atomic():
                return Organization.objects.create(
                    name=name,
                    purpose=Organization.Purpose.TEMPORARY_DEMO,
                )
        except IntegrityError:
            continue
    raise CleanDemoIdentityCollisionError(
        'Could not generate a unique temporary demo organization name within '
        'the retry limit.'
    )


def _create_membership(user, organization):
    return OrganizationMembership.objects.create(
        user=user,
        organization=organization,
    )


def _build_demo_session(
    user,
    organization,
    *,
    now,
    expires_at,
    mode=DemoSession.Mode.CLEAN,
    status=DemoSession.Status.ACTIVE,
    scenario_version='',
):
    return DemoSession(
        user=user,
        organization=organization,
        mode=mode,
        status=status,
        scenario_version=scenario_version,
        last_activity_at=now,
        expires_at=expires_at,
        cleanup_attempt_count=0,
        last_cleanup_error='',
    )


def _verify_temporary_ownership_unit(
    demo_session,
    user,
    organization,
    membership,
    *,
    expected_mode,
    expected_status,
    require_empty,
):
    demo_session.refresh_from_db()
    user.refresh_from_db()
    organization.refresh_from_db()
    membership.refresh_from_db()
    demo_session.full_clean()

    if organization.purpose != Organization.Purpose.TEMPORARY_DEMO:
        raise CleanDemoProvisioningError(
            'Final verification found a non-temporary organization.'
        )
    if user.is_staff or user.is_superuser or not user.is_active:
        raise CleanDemoProvisioningError(
            'Final verification found an invalid temporary user.'
        )
    if user.has_usable_password():
        raise CleanDemoProvisioningError(
            'Final verification found usable temporary credentials.'
        )
    memberships = OrganizationMembership.objects.filter(user=user)
    if memberships.count() != 1 or memberships.get().pk != membership.pk:
        raise CleanDemoProvisioningError(
            'Final verification found inconsistent membership ownership.'
        )
    if membership.organization_id != organization.pk:
        raise CleanDemoProvisioningError(
            'Final verification found a mismatched membership organization.'
        )
    if (
        demo_session.user_id != user.pk
        or demo_session.organization_id != organization.pk
        or demo_session.mode != expected_mode
        or demo_session.status != expected_status
    ):
        raise CleanDemoProvisioningError(
            'Final verification found inconsistent demo-session ownership.'
        )

    if not require_empty:
        return

    operational_queries = (
        Locations.objects.filter(organization=organization),
        Course.objects.filter(organization=organization),
        Certification.objects.filter(organization=organization),
        Schools.schools_list.filter(organization=organization),
        Instructor.objects.filter(organization=organization),
        TheSched.objects.filter(organization=organization),
        InstructorScheduleParticipation.objects.filter(
            organization=organization
        ),
        InstructorScheduleAvailability.objects.filter(
            organization=organization
        ),
    )
    if any(query.exists() for query in operational_queries):
        raise CleanDemoProvisioningError(
            'Final verification found operational records in the clean organization.'
        )


def _verify_clean_ownership_unit(
    demo_session,
    user,
    organization,
    membership,
):
    _verify_temporary_ownership_unit(
        demo_session,
        user,
        organization,
        membership,
        expected_mode=DemoSession.Mode.CLEAN,
        expected_status=DemoSession.Status.ACTIVE,
        require_empty=True,
    )


def _validated_session_times(lifetime, clock):
    if not isinstance(lifetime, timedelta):
        raise CleanDemoLifetimeError('Session lifetime must be a timedelta.')
    if lifetime <= timedelta(0):
        raise CleanDemoLifetimeError('Session lifetime must be positive.')
    if lifetime > MAX_CLEAN_DEMO_SESSION_LIFETIME:
        raise CleanDemoLifetimeError(
            'Session lifetime exceeds the maximum clean demo lifetime.'
        )

    now = clock()
    if timezone.is_naive(now):
        raise CleanDemoLifetimeError('The trusted service clock must be timezone-aware.')
    return now, now + lifetime


def _create_temporary_ownership(
    *,
    identity_factory,
    now,
    expires_at,
    mode,
    status,
    scenario_version,
):
    user = _create_temporary_user(identity_factory)
    organization = _create_temporary_organization(identity_factory)
    membership = _create_membership(user, organization)
    demo_session = _build_demo_session(
        user,
        organization,
        now=now,
        expires_at=expires_at,
        mode=mode,
        status=status,
        scenario_version=scenario_version,
    )
    demo_session.full_clean()
    demo_session.save()
    return user, organization, membership, demo_session


def provision_clean_demo_session(
    *,
    lifetime=DEFAULT_CLEAN_DEMO_SESSION_LIFETIME,
    clock=timezone.now,
    identity_factory=uuid.uuid4,
    admission=None,
):
    """Atomically create one isolated active clean-mode ownership unit."""
    now, expires_at = _validated_session_times(lifetime, clock)

    admission = _enforce_capacity_policy(
        requested_mode=DemoSession.Mode.CLEAN,
        admission=admission,
    )
    try:
        with transaction.atomic():
            user, organization, membership, demo_session = _create_temporary_ownership(
                identity_factory=identity_factory,
                now=now,
                expires_at=expires_at,
                mode=DemoSession.Mode.CLEAN,
                status=DemoSession.Status.ACTIVE,
                scenario_version='',
            )
            _verify_clean_ownership_unit(
                demo_session,
                user,
                organization,
                membership,
            )
            result = CleanDemoProvisioningResult(
                demo_session=demo_session,
                user=user,
                organization=organization,
                membership=membership,
                completed=True,
                expires_at=expires_at,
            )
    except Exception as error:
        finish_demo_provisioning(
            admission,
            failure_category=error.__class__.__name__,
        )
        raise
    finish_demo_provisioning(admission, demo_session=demo_session)
    return result
