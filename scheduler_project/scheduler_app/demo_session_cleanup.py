"""Guarded planning and deletion for expired temporary demo ownership."""

from dataclasses import dataclass, field
import logging

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from members.models import (
    DEFAULT_ORGANIZATION_NAME,
    DemoSession,
    Organization,
    OrganizationMembership,
)

from .models import (
    ActivityCertificationRequirement,
    Certification,
    Course,
    Instructor,
    InstructorCertification,
    InstructorLeadershipRole,
    InstructorScheduleAvailability,
    InstructorScheduleParticipation,
    LeadershipRole,
    Locations,
    Schools,
    TheSched,
)


DEFAULT_CLEANUP_LIMIT = 25
MAX_CLEANUP_LIMIT = 100
logger = logging.getLogger(__name__)
ELIGIBLE_CLEANUP_STATUSES = {
    DemoSession.Status.ACTIVE,
    DemoSession.Status.EXPIRING,
    DemoSession.Status.FAILED,
    DemoSession.Status.DELETING,
}


class DemoCleanupError(Exception):
    pass


class DemoCleanupTargetNotFound(DemoCleanupError):
    pass


@dataclass(frozen=True)
class CleanupPlanItem:
    session_id: str
    mode: str
    status: str
    expires_at: object
    user_id: int
    username: str
    organization_id: int
    organization_name: str
    category: str
    reason: str
    record_counts: dict


@dataclass
class CleanupPlan:
    cutoff: object
    limit: int
    items: list[CleanupPlanItem] = field(default_factory=list)
    more_remaining: bool = False


@dataclass(frozen=True)
class CleanupOutcome:
    session_id: str
    category: str
    reason: str = ''
    record_counts: dict = field(default_factory=dict)


@dataclass
class CleanupBatchResult:
    plan: CleanupPlan
    deleted: list[CleanupOutcome] = field(default_factory=list)
    skipped: list[CleanupOutcome] = field(default_factory=list)
    blocked: list[CleanupOutcome] = field(default_factory=list)
    failed: list[CleanupOutcome] = field(default_factory=list)


def _record_counts(organization):
    schedules = TheSched.objects.filter(organization=organization)
    instructors = Instructor.objects.filter(organization=organization)
    courses = Course.objects.filter(organization=organization)
    return {
        'availability': InstructorScheduleAvailability.objects.filter(
            organization=organization
        ).count(),
        'participation': InstructorScheduleParticipation.objects.filter(
            organization=organization
        ).count(),
        'schedules': schedules.count(),
        'schools': Schools.schools_list.filter(organization=organization).count(),
        'instructor_certifications': InstructorCertification.objects.filter(
            instructor__organization=organization
        ).count(),
        'instructor_leadership_roles': InstructorLeadershipRole.objects.filter(
            instructor__organization=organization
        ).count(),
        'instructors': instructors.count(),
        'activity_requirements': ActivityCertificationRequirement.objects.filter(
            course__organization=organization
        ).count(),
        'courses': courses.count(),
        'certifications': Certification.objects.filter(
            organization=organization
        ).count(),
        'leadership_roles': LeadershipRole.objects.filter(
            organization=organization
        ).count(),
        'locations': Locations.objects.filter(organization=organization).count(),
        'memberships': OrganizationMembership.objects.filter(
            organization=organization
        ).count(),
        'demo_sessions': DemoSession.objects.filter(
            organization=organization
        ).count(),
        'organizations': 1,
        'users': 1,
    }


def _eligibility(session, cutoff):
    organization = session.organization
    user = session.user
    counts = _record_counts(organization)
    base = {
        'session_id': str(session.identifier),
        'mode': session.mode,
        'status': session.status,
        'expires_at': session.expires_at,
        'user_id': user.pk,
        'username': user.get_username(),
        'organization_id': organization.pk,
        'organization_name': organization.name,
        'record_counts': counts,
    }
    if (
        organization.name == DEFAULT_ORGANIZATION_NAME
        or organization.purpose != Organization.Purpose.TEMPORARY_DEMO
    ):
        return CleanupPlanItem(
            **base,
            category='blocked',
            reason='organization is not an eligible temporary demo organization',
        )
    if user.is_staff or user.is_superuser:
        return CleanupPlanItem(
            **base,
            category='blocked',
            reason='temporary ownership user is privileged',
        )
    memberships = list(
        OrganizationMembership.objects.filter(user=user).select_related(
            'organization'
        )[:2]
    )
    if (
        len(memberships) != 1
        or memberships[0].organization_id != organization.pk
        or counts['memberships'] != 1
        or counts['demo_sessions'] != 1
        or DemoSession.objects.filter(user=user).count() != 1
    ):
        return CleanupPlanItem(
            **base,
            category='blocked',
            reason='durable ownership relationships do not match exactly',
        )
    if not session.expires_at or session.expires_at > cutoff:
        return CleanupPlanItem(
            **base,
            category='skipped',
            reason='session is not expired at the cleanup cutoff',
        )
    if session.status not in ELIGIBLE_CLEANUP_STATUSES:
        return CleanupPlanItem(
            **base,
            category='blocked',
            reason='session status is not eligible for cleanup',
        )
    return CleanupPlanItem(
        **base,
        category='eligible',
        reason=(
            'expired temporary ownership is eligible'
            if session.status != DemoSession.Status.DELETING
            else 'retryable deleting session is eligible'
        ),
    )


def plan_demo_session_cleanup(*, cutoff=None, limit=DEFAULT_CLEANUP_LIMIT, session_id=None):
    cutoff = cutoff or timezone.now()
    if timezone.is_naive(cutoff):
        raise DemoCleanupError('Cleanup cutoff must be timezone-aware.')
    if limit <= 0 or limit > MAX_CLEANUP_LIMIT:
        raise DemoCleanupError(
            f'Cleanup limit must be between 1 and {MAX_CLEANUP_LIMIT}.'
        )

    queryset = DemoSession.objects.select_related(
        'user',
        'organization',
    ).order_by('expires_at', 'pk')
    if session_id is not None:
        queryset = queryset.filter(identifier=session_id)
        sessions = list(queryset[:1])
        if not sessions:
            raise DemoCleanupTargetNotFound('The targeted DemoSession was not found.')
        more_remaining = False
    else:
        sessions = list(queryset[: limit + 1])
        more_remaining = len(sessions) > limit
        sessions = sessions[:limit]

    return CleanupPlan(
        cutoff=cutoff,
        limit=limit,
        items=[_eligibility(session, cutoff) for session in sessions],
        more_remaining=more_remaining,
    )


def _delete_schedule_dependents(organization):
    InstructorScheduleAvailability.objects.filter(
        organization=organization
    ).delete()
    InstructorScheduleParticipation.objects.filter(
        organization=organization
    ).delete()
    schedules = TheSched.objects.filter(organization=organization)
    for schedule in schedules:
        schedule.schools.clear()
    schedules.delete()


def _delete_schools(organization):
    schools = Schools.schools_list.filter(organization=organization)
    for school in schools:
        school.subject.clear()
    schools.delete()


def _delete_instructors(organization):
    InstructorCertification.objects.filter(
        instructor__organization=organization
    ).delete()
    InstructorLeadershipRole.objects.filter(
        instructor__organization=organization
    ).delete()
    Instructor.objects.filter(organization=organization).delete()


def _delete_courses(organization):
    ActivityCertificationRequirement.objects.filter(
        course__organization=organization
    ).delete()
    courses = Course.objects.filter(organization=organization)
    for course in courses:
        course.primary_locs.clear()
    courses.delete()


def _delete_reference_records(organization):
    Certification.objects.filter(organization=organization).delete()
    LeadershipRole.objects.filter(organization=organization).delete()
    Locations.objects.filter(organization=organization).delete()


def _delete_ownership(session, membership, organization, user):
    membership.delete()
    session.delete()
    organization.delete()
    user.delete()


def cleanup_demo_session(session_id, *, cutoff):
    """Delete one locked, revalidated ownership unit in dependency order."""
    with transaction.atomic():
        try:
            session = (
                DemoSession.objects.select_for_update()
                .select_related('user', 'organization')
                .get(identifier=session_id)
            )
        except DemoSession.DoesNotExist:
            return CleanupOutcome(str(session_id), 'skipped', 'session no longer exists')

        organization = Organization.objects.select_for_update().get(
            pk=session.organization_id
        )
        membership = (
            OrganizationMembership.objects.select_for_update()
            .filter(user=session.user, organization=organization)
            .first()
        )
        locked_plan = _eligibility(session, cutoff)
        if locked_plan.category != 'eligible':
            return CleanupOutcome(
                str(session.identifier),
                locked_plan.category,
                locked_plan.reason,
                locked_plan.record_counts,
            )
        if membership is None:
            return CleanupOutcome(
                str(session.identifier),
                'blocked',
                'matching membership disappeared after locking',
                locked_plan.record_counts,
            )

        session.status = DemoSession.Status.DELETING
        session.cleanup_attempt_count += 1
        session.last_cleanup_error = ''
        session.save(
            update_fields=(
                'status',
                'cleanup_attempt_count',
                'last_cleanup_error',
            )
        )
        session.refresh_from_db()

        _delete_schedule_dependents(organization)
        _delete_schools(organization)
        _delete_instructors(organization)
        _delete_courses(organization)
        _delete_reference_records(organization)
        if any(
            (
                organization.schedules.exists(),
                organization.schools.exists(),
                organization.instructors.exists(),
                organization.courses.exists(),
                organization.certifications.exists(),
                organization.leadership_roles.exists(),
                organization.locations.exists(),
            )
        ):
            raise DemoCleanupError(
                'Owned operational records remain before organization deletion.'
            )
        counts = locked_plan.record_counts
        user = session.user
        _delete_ownership(session, membership, organization, user)
        return CleanupOutcome(str(session_id), 'deleted', record_counts=counts)


def _record_failure(session_id, error):
    summary = f'{error.__class__.__name__}: cleanup transaction rolled back'
    DemoSession.objects.filter(identifier=session_id).update(
        cleanup_attempt_count=F('cleanup_attempt_count') + 1,
        last_cleanup_error=summary[:500],
    )
    return summary


def cleanup_expired_demo_sessions(*, plan):
    result = CleanupBatchResult(plan=plan)
    for item in plan.items:
        if item.category == 'skipped':
            result.skipped.append(
                CleanupOutcome(item.session_id, 'skipped', item.reason, item.record_counts)
            )
            continue
        if item.category == 'blocked':
            result.blocked.append(
                CleanupOutcome(item.session_id, 'blocked', item.reason, item.record_counts)
            )
            continue
        try:
            outcome = cleanup_demo_session(item.session_id, cutoff=plan.cutoff)
        except Exception as error:
            summary = _record_failure(item.session_id, error)
            logger.error(
                'Temporary demo cleanup outcome=failed session=%s user=%s '
                'organization=%s mode=%s expires_at=%s failure_category=%s counts=%s',
                item.session_id,
                item.user_id,
                item.organization_id,
                item.mode,
                item.expires_at,
                error.__class__.__name__,
                item.record_counts,
            )
            result.failed.append(
                CleanupOutcome(item.session_id, 'failed', summary, item.record_counts)
            )
            continue
        getattr(result, outcome.category).append(outcome)
        logger.info(
            'Temporary demo cleanup outcome=%s session=%s user=%s organization=%s '
            'mode=%s expires_at=%s counts=%s',
            outcome.category,
            item.session_id,
            item.user_id,
            item.organization_id,
            item.mode,
            item.expires_at,
            outcome.record_counts,
        )
    return result
