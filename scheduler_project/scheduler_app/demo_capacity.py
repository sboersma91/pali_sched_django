"""Durable admission, throttling, and prepared-operation coordination."""

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import hmac
from ipaddress import ip_address
import logging
import uuid

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from members.models import (
    DemoCapacityCoordinator,
    DemoCapacityReservation,
    DemoOperationLease,
    DemoPreparedResetAttempt,
    DemoProvisioningAttempt,
    DemoSession,
    Organization,
)

from scheduler_project.settings_validation import validate_demo_capacity_settings


logger = logging.getLogger(__name__)


class DemoAdmissionError(Exception):
    category = 'admission_denied'
    status_code = 503
    visitor_message = 'The temporary demo is unavailable. Please try again later.'
    retry_after = None


class DemoThrottleDenied(DemoAdmissionError):
    category = 'throttled'
    status_code = 429
    visitor_message = (
        'Too many demo start attempts were received. Please try again later.'
    )


class DemoCapacityDenied(DemoAdmissionError):
    category = 'capacity'
    visitor_message = (
        'All temporary demo spaces are currently in use. Please try again later.'
    )


class DemoPreparedOperationBusy(DemoAdmissionError):
    category = 'prepared_busy'
    visitor_message = (
        'The prepared demo is currently busy. Please try again shortly.'
    )


class DemoResetThrottleDenied(DemoThrottleDenied):
    visitor_message = (
        'Too many prepared reset attempts were received. Please try again later.'
    )


@dataclass(frozen=True)
class DemoProvisioningAdmission:
    reservation_token: uuid.UUID
    attempt_id: int
    mode: str
    lease_token: uuid.UUID | None = None


@dataclass(frozen=True)
class PreparedOperationAdmission:
    lease_token: uuid.UUID


def _capacity_values():
    names = (
        'DEMO_MAX_ACTIVE_SESSIONS',
        'DEMO_MAX_ACTIVE_PREPARED_SESSIONS',
        'DEMO_MAX_ACTIVE_CLEAN_SESSIONS',
        'DEMO_GLOBAL_START_LIMIT',
        'DEMO_GLOBAL_START_WINDOW_SECONDS',
        'DEMO_CLIENT_START_LIMIT',
        'DEMO_CLIENT_START_WINDOW_SECONDS',
        'DEMO_MAX_CONCURRENT_PREPARED_OPERATIONS',
        'DEMO_PREPARED_RESET_LIMIT',
        'DEMO_PREPARED_RESET_WINDOW_SECONDS',
        'DEMO_CAPACITY_RESERVATION_SECONDS',
        'DEMO_PREPARED_OPERATION_LEASE_SECONDS',
    )
    values = {}
    for name in names:
        try:
            values[name] = getattr(settings, name)
        except AttributeError as error:
            raise ImproperlyConfigured(f'{name} must be configured.') from error
    return validate_demo_capacity_settings(values)


def _coordinator_lock():
    DemoCapacityCoordinator.objects.get_or_create(identifier='demo-capacity')
    return DemoCapacityCoordinator.objects.select_for_update().get(
        identifier='demo-capacity'
    )


def _normalize_address(raw):
    try:
        return ip_address((raw or '').strip()).compressed
    except ValueError:
        return 'unknown-client'


def client_key_from_request(request):
    raw = request.META.get('REMOTE_ADDR', '')
    if getattr(settings, 'SECURE_PROXY_SSL_HEADER', None):
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if forwarded:
            raw = forwarded.split(',')[0].strip()
    normalized = _normalize_address(raw)
    return hmac.new(
        settings.SECRET_KEY.encode(),
        normalized.encode(),
        hashlib.sha256,
    ).hexdigest()


def internal_client_key():
    return hmac.new(
        settings.SECRET_KEY.encode(),
        b'trusted-direct-provisioning',
        hashlib.sha256,
    ).hexdigest()


def _active_sessions(now):
    return DemoSession.objects.filter(
        organization__purpose=Organization.Purpose.TEMPORARY_DEMO,
        status__in=(DemoSession.Status.PROVISIONING, DemoSession.Status.ACTIVE),
        expires_at__gt=now,
    )


def _acquire_prepared_lease(now, values):
    DemoOperationLease.objects.filter(
        operation=DemoOperationLease.PREPARED,
        expires_at__lte=now,
    ).delete()
    if DemoOperationLease.objects.filter(
        operation=DemoOperationLease.PREPARED,
        expires_at__gt=now,
    ).count() >= values['DEMO_MAX_CONCURRENT_PREPARED_OPERATIONS']:
        raise DemoPreparedOperationBusy()
    lease = DemoOperationLease.objects.create(
        operation=DemoOperationLease.PREPARED,
        acquired_at=now,
        expires_at=(
            now
            + timedelta(
                seconds=values['DEMO_PREPARED_OPERATION_LEASE_SECONDS']
            )
        ),
    )
    return lease.token


def reserve_demo_provisioning_capacity(*, requested_mode, client_key, clock=timezone.now):
    if requested_mode not in {DemoSession.Mode.CLEAN, DemoSession.Mode.PREPARED}:
        raise DemoCapacityDenied()
    now = clock()
    if timezone.is_naive(now):
        raise DemoCapacityDenied()
    values = _capacity_values()

    denial = None
    admission = None
    with transaction.atomic():
        _coordinator_lock()
        DemoCapacityReservation.objects.filter(expires_at__lte=now).delete()
        global_cutoff = now - timedelta(
            seconds=values['DEMO_GLOBAL_START_WINDOW_SECONDS']
        )
        client_cutoff = now - timedelta(
            seconds=values['DEMO_CLIENT_START_WINDOW_SECONDS']
        )
        if DemoProvisioningAttempt.objects.filter(
            created_at__gte=global_cutoff
        ).count() >= values['DEMO_GLOBAL_START_LIMIT']:
            logger.warning(
                'Demo provisioning admission outcome=throttled scope=global'
            )
            error = DemoThrottleDenied()
            error.retry_after = values['DEMO_GLOBAL_START_WINDOW_SECONDS']
            raise error
        if DemoProvisioningAttempt.objects.filter(
            client_key=client_key,
            created_at__gte=client_cutoff,
        ).count() >= values['DEMO_CLIENT_START_LIMIT']:
            logger.warning(
                'Demo provisioning admission outcome=throttled scope=client'
            )
            error = DemoThrottleDenied()
            error.retry_after = values['DEMO_CLIENT_START_WINDOW_SECONDS']
            raise error

        active = _active_sessions(now)
        reservations = DemoCapacityReservation.objects.filter(expires_at__gt=now)
        total_count = active.count() + reservations.count()
        mode_count = (
            active.filter(mode=requested_mode).count()
            + reservations.filter(mode=requested_mode).count()
        )
        mode_limit = values[
            (
                'DEMO_MAX_ACTIVE_PREPARED_SESSIONS'
                if requested_mode == DemoSession.Mode.PREPARED
                else 'DEMO_MAX_ACTIVE_CLEAN_SESSIONS'
            )
        ]
        if (
            total_count >= values['DEMO_MAX_ACTIVE_SESSIONS']
            or mode_count >= mode_limit
        ):
            DemoProvisioningAttempt.objects.create(
                mode=requested_mode,
                outcome=DemoProvisioningAttempt.Outcome.CAPACITY_DENIED,
                client_key=client_key,
                failure_category='active_capacity',
            )
            denial = DemoCapacityDenied()
        else:
            attempt = DemoProvisioningAttempt.objects.create(
                mode=requested_mode,
                outcome=DemoProvisioningAttempt.Outcome.ADMITTED,
                client_key=client_key,
            )
            lease_token = None
            try:
                if requested_mode == DemoSession.Mode.PREPARED:
                    lease_token = _acquire_prepared_lease(now, values)
            except DemoPreparedOperationBusy:
                attempt.outcome = DemoProvisioningAttempt.Outcome.PREPARED_BUSY
                attempt.failure_category = 'prepared_operation_busy'
                attempt.save(update_fields=('outcome', 'failure_category'))
                denial = DemoPreparedOperationBusy()
            if denial is None:
                reservation = DemoCapacityReservation.objects.create(
                    mode=requested_mode,
                    client_key=client_key,
                    expires_at=(
                        now
                        + timedelta(
                            seconds=values['DEMO_CAPACITY_RESERVATION_SECONDS']
                        )
                    ),
                )
                admission = DemoProvisioningAdmission(
                    reservation.token,
                    attempt.pk,
                    requested_mode,
                    lease_token,
                )
    if denial is not None:
        logger.warning(
            'Demo provisioning admission mode=%s outcome=%s',
            requested_mode,
            denial.category,
        )
        raise denial
    logger.info(
        'Demo provisioning admission mode=%s outcome=admitted',
        requested_mode,
    )
    return admission


def validate_provisioning_admission(admission, *, requested_mode, clock=timezone.now):
    now = clock()
    reservation = DemoCapacityReservation.objects.filter(
        token=admission.reservation_token,
        mode=requested_mode,
        expires_at__gt=now,
    ).first()
    if reservation is None:
        raise DemoCapacityDenied()
    if requested_mode == DemoSession.Mode.PREPARED and not (
        admission.lease_token
        and DemoOperationLease.objects.filter(
            token=admission.lease_token,
            operation=DemoOperationLease.PREPARED,
            expires_at__gt=now,
        ).exists()
    ):
        raise DemoPreparedOperationBusy()
    return reservation


def finish_demo_provisioning(admission, *, demo_session=None, failure_category=''):
    outcome = (
        DemoProvisioningAttempt.Outcome.SUCCEEDED
        if demo_session is not None
        else DemoProvisioningAttempt.Outcome.FAILED
    )
    DemoProvisioningAttempt.objects.filter(pk=admission.attempt_id).update(
        outcome=outcome,
        demo_session=demo_session,
        failure_category=failure_category[:80],
    )
    DemoCapacityReservation.objects.filter(
        token=admission.reservation_token
    ).delete()
    if admission.lease_token:
        release_prepared_operation(admission.lease_token)
    logger.info(
        'Demo provisioning completion mode=%s outcome=%s',
        admission.mode,
        outcome,
    )


def release_demo_provisioning(admission, *, failure_category='released'):
    if not DemoCapacityReservation.objects.filter(
        token=admission.reservation_token
    ).exists():
        return False
    finish_demo_provisioning(
        admission,
        failure_category=failure_category,
    )
    return True


def acquire_prepared_reset_operation(demo_session, *, clock=timezone.now):
    now = clock()
    values = _capacity_values()
    cutoff = now - timedelta(
        seconds=values['DEMO_PREPARED_RESET_WINDOW_SECONDS']
    )
    with transaction.atomic():
        _coordinator_lock()
        if DemoPreparedResetAttempt.objects.filter(
            demo_session=demo_session,
            created_at__gte=cutoff,
        ).count() >= values['DEMO_PREPARED_RESET_LIMIT']:
            logger.warning(
                'Prepared demo reset admission outcome=throttled'
            )
            error = DemoResetThrottleDenied()
            error.retry_after = values['DEMO_PREPARED_RESET_WINDOW_SECONDS']
            raise error
        token = _acquire_prepared_lease(now, values)
        DemoPreparedResetAttempt.objects.create(demo_session=demo_session)
        logger.info('Prepared demo reset admission outcome=admitted')
        return PreparedOperationAdmission(token)


def release_prepared_operation(token):
    return DemoOperationLease.objects.filter(token=token).delete()[0] == 1
