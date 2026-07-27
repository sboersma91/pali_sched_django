"""Lifecycle transition for an authenticated temporary visitor's explicit exit."""

from dataclasses import dataclass
import logging

from django.db import transaction
from django.utils import timezone

from members.models import (
    DEFAULT_ORGANIZATION_NAME,
    DemoSession,
    Organization,
    OrganizationMembership,
)

from .prepared_scenarios import PREPARED_SCENARIOS


logger = logging.getLogger(__name__)


class TemporaryDemoExitUnavailable(Exception):
    """The supplied ownership unit cannot be safely exited."""


@dataclass(frozen=True)
class TemporaryDemoExitResult:
    demo_session: DemoSession
    user: object
    organization: Organization
    membership: OrganizationMembership
    original_status: str
    final_status: str
    transitioned: bool
    already_exiting: bool
    expiration_shortened: bool
    completion_status: str


def _validate_exit_ownership(session, membership):
    user = session.user
    organization = session.organization
    if (
        membership.user_id != user.pk
        or membership.organization_id != organization.pk
        or organization.purpose != Organization.Purpose.TEMPORARY_DEMO
        or organization.name == DEFAULT_ORGANIZATION_NAME
        or user.is_staff
        or user.is_superuser
        or session.mode not in {
            DemoSession.Mode.CLEAN,
            DemoSession.Mode.PREPARED,
        }
        or (
            session.mode == DemoSession.Mode.CLEAN
            and session.scenario_version != ''
        )
        or (
            session.mode == DemoSession.Mode.PREPARED
            and session.scenario_version not in PREPARED_SCENARIOS
        )
    ):
        raise TemporaryDemoExitUnavailable(
            'Temporary demo ownership is unavailable.'
        )


def exit_temporary_demo_session(*, demo_session, clock=timezone.now):
    """Mark one verified active demo expiring without deleting its data."""
    now = clock()
    if timezone.is_naive(now):
        raise TemporaryDemoExitUnavailable(
            'Temporary demo ownership is unavailable.'
        )

    with transaction.atomic():
        try:
            locked = (
                DemoSession.objects.select_for_update()
                .select_related('user', 'organization')
                .get(pk=demo_session.pk)
            )
        except DemoSession.DoesNotExist as error:
            raise TemporaryDemoExitUnavailable(
                'Temporary demo ownership is unavailable.'
            ) from error

        memberships = OrganizationMembership.objects.select_for_update().filter(
            user=locked.user,
            organization=locked.organization,
        )
        if memberships.count() != 1:
            raise TemporaryDemoExitUnavailable(
                'Temporary demo ownership is unavailable.'
            )
        membership = memberships.get()
        _validate_exit_ownership(locked, membership)

        original_status = locked.status
        transitioned = original_status == DemoSession.Status.ACTIVE
        already_exiting = original_status == DemoSession.Status.EXPIRING
        expiration_shortened = False
        completion_status = 'completed'

        if transitioned:
            new_expiration = min(locked.expires_at, now)
            expiration_shortened = new_expiration < locked.expires_at
            DemoSession.objects.filter(
                pk=locked.pk,
                status=DemoSession.Status.ACTIVE,
            ).update(
                status=DemoSession.Status.EXPIRING,
                expires_at=new_expiration,
            )
        elif not already_exiting:
            completion_status = 'unavailable'

        locked.refresh_from_db()
        if transitioned and locked.status not in {
            DemoSession.Status.EXPIRING,
            DemoSession.Status.DELETING,
        }:
            raise TemporaryDemoExitUnavailable(
                'Temporary demo ownership is unavailable.'
            )

    logger.info(
        (
            'Temporary demo exit session=%s user=%s organization=%s mode=%s '
            'original_status=%s final_status=%s expiration_shortened=%s '
            'outcome=%s'
        ),
        locked.identifier,
        locked.user_id,
        locked.organization_id,
        locked.mode,
        original_status,
        locked.status,
        expiration_shortened,
        completion_status,
    )
    return TemporaryDemoExitResult(
        demo_session=locked,
        user=locked.user,
        organization=locked.organization,
        membership=membership,
        original_status=original_status,
        final_status=locked.status,
        transitioned=transitioned,
        already_exiting=already_exiting,
        expiration_shortened=expiration_shortened,
        completion_status=completion_status,
    )
