"""Request-time validation for persisted temporary demo ownership."""

from dataclasses import dataclass

from django.utils import timezone

from members.models import DemoSession, Organization, OrganizationMembership

from .prepared_scenarios import PREPARED_SCENARIOS


ACCESS_NOT_TEMPORARY = 'not_temporary'
ACCESS_VALID = 'valid'
ACCESS_EXPIRED = 'expired'
ACCESS_INVALID = 'invalid'


@dataclass(frozen=True)
class TemporaryDemoAccess:
    category: str
    demo_session: DemoSession | None = None
    membership: OrganizationMembership | None = None
    organization: Organization | None = None
    reason: str = ''

    @property
    def allowed(self):
        return self.category in {ACCESS_NOT_TEMPORARY, ACCESS_VALID}


def validate_temporary_demo_access(user, *, now=None, expected=None):
    """Classify one authenticated user using only persisted ownership rows."""
    if not user.is_authenticated:
        return TemporaryDemoAccess(ACCESS_NOT_TEMPORARY)

    now = now or timezone.now()
    membership = (
        OrganizationMembership.objects.filter(user=user)
        .select_related(
            'organization',
            'user__demo_session__organization',
        )
        .first()
    )
    membership_count = 1 if membership is not None else 0
    if membership is not None:
        try:
            demo_session = membership.user.demo_session
        except DemoSession.DoesNotExist:
            demo_session = None
    else:
        demo_session = (
            DemoSession.objects.filter(user=user)
            .select_related('organization')
            .first()
        )
    session_count = 1 if demo_session is not None else 0
    organization = membership.organization if membership else None

    temporary_related = (
        demo_session is not None
        or (
            organization is not None
            and organization.purpose == Organization.Purpose.TEMPORARY_DEMO
        )
    )
    if not temporary_related:
        return TemporaryDemoAccess(ACCESS_NOT_TEMPORARY)

    if user.is_staff or user.is_superuser:
        return TemporaryDemoAccess(
            ACCESS_INVALID,
            demo_session,
            membership,
            organization,
            'privileged temporary user',
        )
    if membership_count != 1 or membership is None:
        return TemporaryDemoAccess(
            ACCESS_INVALID,
            demo_session,
            reason='missing or ambiguous membership',
        )
    if organization.purpose != Organization.Purpose.TEMPORARY_DEMO:
        return TemporaryDemoAccess(
            ACCESS_INVALID,
            demo_session,
            membership,
            organization,
            'organization is not temporary',
        )
    if session_count != 1 or demo_session is None:
        return TemporaryDemoAccess(
            ACCESS_INVALID,
            membership=membership,
            organization=organization,
            reason='missing or ambiguous demo session',
        )
    if (
        demo_session.user_id != user.pk
        or demo_session.organization_id != organization.pk
    ):
        return TemporaryDemoAccess(
            ACCESS_INVALID,
            demo_session,
            membership,
            organization,
            'session ownership mismatch',
        )
    if demo_session.mode not in {
        DemoSession.Mode.CLEAN,
        DemoSession.Mode.PREPARED,
    }:
        return TemporaryDemoAccess(
            ACCESS_INVALID,
            demo_session,
            membership,
            organization,
            'unsupported mode',
        )
    if (
        demo_session.mode == DemoSession.Mode.PREPARED
        and demo_session.scenario_version not in PREPARED_SCENARIOS
    ):
        return TemporaryDemoAccess(
            ACCESS_INVALID,
            demo_session,
            membership,
            organization,
            'unsupported prepared scenario version',
        )
    if demo_session.status != DemoSession.Status.ACTIVE:
        return TemporaryDemoAccess(
            ACCESS_INVALID,
            demo_session,
            membership,
            organization,
            'inactive lifecycle status',
        )
    try:
        expired = not demo_session.expires_at or demo_session.expires_at <= now
    except TypeError:
        expired = None
    if expired is None:
        return TemporaryDemoAccess(
            ACCESS_INVALID,
            demo_session,
            membership,
            organization,
            'invalid expiration timestamp',
        )
    if expired:
        return TemporaryDemoAccess(
            ACCESS_EXPIRED,
            demo_session,
            membership,
            organization,
            'absolute expiration reached',
        )
    if expected is not None and (
        user.pk != expected.user.pk
        or membership.pk != expected.membership.pk
        or organization.pk != expected.organization.pk
        or demo_session.pk != expected.demo_session.pk
    ):
        return TemporaryDemoAccess(
            ACCESS_INVALID,
            demo_session,
            membership,
            organization,
            'provisioning result mismatch',
        )
    return TemporaryDemoAccess(
        ACCESS_VALID,
        demo_session,
        membership,
        organization,
    )
