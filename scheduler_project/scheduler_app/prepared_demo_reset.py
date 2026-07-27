"""Transactional restoration of one exclusively owned prepared demo session.

This service has no request or authentication behavior. It preserves the
temporary ownership unit while returning all organization-owned operational
data to the approved prepared baseline.
"""

from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from members.models import (
    DEFAULT_ORGANIZATION_NAME,
    DemoSession,
    Organization,
    OrganizationMembership,
)

from .demo_scaffolding import (
    DEMO_SCENARIO,
    PREPARED_DEMO_SCENARIO_VERSION,
    DemoApplyResult,
    PlanItem,
    _apply_activity_requirement,
    _apply_availability,
    _apply_certifications,
    _apply_courses,
    _apply_instructor_certifications,
    _apply_instructors,
    _apply_locations,
    _apply_participation,
    _apply_schedule,
    _apply_schools,
    _stored_generation_issues,
    _validate_applied_reference_data,
    _validate_demo_starting_state,
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
from .prepared_scenarios import build_working_scenario
from .working_demo_scenario import (
    WORKING_DEMO_SCENARIO,
    WORKING_DEMO_SCENARIO_VERSION,
)


class PreparedDemoResetError(Exception):
    """Raised when reset authorization or final acceptance fails."""

    def __init__(self, message, *, plan=None):
        super().__init__(message)
        self.plan = plan


@dataclass
class PreparedDemoResetPlan:
    create: list[PlanItem] = field(default_factory=list)
    update: list[PlanItem] = field(default_factory=list)
    reconcile: list[PlanItem] = field(default_factory=list)
    restore: list[PlanItem] = field(default_factory=list)
    delete: list[PlanItem] = field(default_factory=list)
    unchanged: list[PlanItem] = field(default_factory=list)
    warnings: list[PlanItem] = field(default_factory=list)
    blockers: list[PlanItem] = field(default_factory=list)

    def categories(self):
        return (
            ('create', self.create),
            ('update', self.update),
            ('reconcile', self.reconcile),
            ('restore', self.restore),
            ('delete', self.delete),
            ('unchanged', self.unchanged),
            ('warnings', self.warnings),
            ('blockers', self.blockers),
        )


@dataclass(frozen=True)
class PreparedDemoResetResult:
    demo_session: DemoSession
    user: object
    organization: Organization
    membership: OrganizationMembership
    schedule: TheSched
    plan: PreparedDemoResetPlan
    applied: DemoApplyResult
    deleted: tuple[PlanItem, ...]
    final_validation: dict
    already_canonical: bool
    completed: bool


def _item(record_type, identity, expected, reason, current=None):
    return PlanItem(record_type, identity, expected, reason, current)


def _block(plan, message, *, record_type='ownership'):
    plan.blockers.append(_item(record_type, 'prepared demo session', None, message))
    raise PreparedDemoResetError(message, plan=plan)


def _validate_ownership(session, now, plan):
    organization = session.organization
    user = session.user
    if session.mode != DemoSession.Mode.PREPARED:
        _block(plan, 'Prepared reset requires a prepared-mode DemoSession.')
    if session.status != DemoSession.Status.ACTIVE:
        _block(plan, 'Prepared reset requires an active DemoSession.')
    if session.scenario_version not in {
        PREPARED_DEMO_SCENARIO_VERSION, WORKING_DEMO_SCENARIO_VERSION,
    }:
        _block(plan, 'Prepared reset requires a registered scenario version.')
    if not session.expires_at or session.expires_at <= now:
        _block(plan, 'Prepared reset requires an unexpired DemoSession.')
    if user.is_staff or user.is_superuser:
        _block(plan, 'Prepared reset cannot target a privileged user.')
    if (
        organization.name == DEFAULT_ORGANIZATION_NAME
        or organization.purpose != Organization.Purpose.TEMPORARY_DEMO
    ):
        _block(plan, 'Prepared reset requires a non-default temporary demo organization.')

    memberships = list(
        OrganizationMembership.objects.filter(user=user).select_related(
            'organization'
        )[:2]
    )
    if (
        len(memberships) != 1
        or memberships[0].organization_id != organization.pk
        or OrganizationMembership.objects.filter(organization=organization).count() != 1
        or DemoSession.objects.filter(user=user).count() != 1
        or DemoSession.objects.filter(organization=organization).count() != 1
    ):
        _block(plan, 'Prepared reset ownership relationships do not match exactly.')

    schedule_name = (
        DEMO_SCENARIO['schedule']['name']
        if session.scenario_version == PREPARED_DEMO_SCENARIO_VERSION
        else WORKING_DEMO_SCENARIO['schedules'][0][0]
    )
    schedules = list(
        TheSched.objects.filter(
            organization=organization,
            sched_name=schedule_name,
        )[:2]
    )
    if len(schedules) != 1:
        _block(
            plan,
            'Prepared reset requires exactly one canonical prepared schedule.',
            record_type='schedule',
        )
    return memberships[0], schedules[0]


def _replace_with_working_scenario(organization):
    """Remove only target-owned operational data and recreate working-v1."""
    InstructorScheduleAvailability.objects.filter(organization=organization).delete()
    InstructorScheduleParticipation.objects.filter(organization=organization).delete()
    for schedule in TheSched.objects.filter(organization=organization):
        schedule.schools.clear()
    TheSched.objects.filter(organization=organization).delete()
    InstructorCertification.objects.filter(instructor__organization=organization).delete()
    InstructorLeadershipRole.objects.filter(instructor__organization=organization).delete()
    Instructor.objects.filter(organization=organization).delete()
    for school in Schools._default_manager.filter(organization=organization):
        school.subject.clear()
    Schools._default_manager.filter(organization=organization).delete()
    ActivityCertificationRequirement.objects.filter(course__organization=organization).delete()
    for course in Course.objects.filter(organization=organization):
        course.primary_locs.clear()
    Course.objects.filter(organization=organization).delete()
    Certification.objects.filter(organization=organization).delete()
    LeadershipRole.objects.filter(organization=organization).delete()
    Locations.objects.filter(organization=organization).delete()
    schedules, outcomes = build_working_scenario(organization)
    return schedules[WORKING_DEMO_SCENARIO['schedules'][0][0]], outcomes


def _reject_foreign_relationships(organization, plan):
    # Express cross-organization checks without traversing or mutating foreign
    # rows. Model validation normally prevents these; this is a fail-closed
    # defense for manually corrupted databases.
    if ActivityCertificationRequirement.objects.filter(
        course__organization=organization
    ).exclude(certification__organization=organization).exists():
        _block(plan, 'A foreign activity-certification relationship blocks reset.')
    if InstructorCertification.objects.filter(
        instructor__organization=organization
    ).exclude(certification__organization=organization).exists():
        _block(plan, 'A foreign instructor-certification relationship blocks reset.')
    if InstructorLeadershipRole.objects.filter(
        instructor__organization=organization
    ).exclude(leadership_role__organization=organization).exists():
        _block(plan, 'A foreign instructor-leadership relationship blocks reset.')
    if Course.primary_locs.through.objects.filter(
        course__organization=organization
    ).exclude(locations__organization=organization).exists():
        _block(plan, 'A foreign activity-location relationship blocks reset.')
    if Schools.subject.through.objects.filter(
        schools__organization=organization
    ).exclude(course__organization=organization).exists():
        _block(plan, 'A foreign cohort-activity relationship blocks reset.')
    if TheSched.schools.through.objects.filter(
        thesched__organization=organization
    ).exclude(schools__organization=organization).exists():
        _block(plan, 'A foreign schedule-cohort relationship blocks reset.')


def _canonical_identities():
    return {
        'locations': {item['name'] for item in DEMO_SCENARIO['locations']},
        'certifications': set(DEMO_SCENARIO['certifications']),
        'courses': {item['name'] for item in DEMO_SCENARIO['activities']},
        'schools': {item['name'] for item in DEMO_SCENARIO['schools']},
        'instructors': {
            (item['first'], item['last']) for item in DEMO_SCENARIO['instructors']
        },
        'schedules': {DEMO_SCENARIO['schedule']['name']},
    }


def _build_plan(organization, schedule):
    plan = PreparedDemoResetPlan()
    identities = _canonical_identities()
    record_sets = (
        ('location', Locations.objects.filter(organization=organization), 'loc_name', identities['locations']),
        ('certification', Certification.objects.filter(organization=organization), 'name', identities['certifications']),
        ('activity', Course.objects.filter(organization=organization), 'course_name', identities['courses']),
        ('school/cohort', Schools.schools_list.filter(organization=organization), 'school_name', identities['schools']),
        ('schedule', TheSched.objects.filter(organization=organization), 'sched_name', identities['schedules']),
        ('leadership role', LeadershipRole.objects.filter(organization=organization), 'name', set()),
    )
    for record_type, queryset, field_name, expected in record_sets:
        current = set(queryset.values_list(field_name, flat=True))
        for identity in sorted(expected - current):
            plan.create.append(_item(record_type, identity, {'exists': True}, 'Canonical record is missing.'))
        for identity in sorted(current - expected):
            plan.delete.append(_item(record_type, identity, {'exists': False}, 'Visitor-created noncanonical record will be removed.'))
    current_instructors = set(
        Instructor.objects.filter(organization=organization).values_list('fname', 'lname')
    )
    for identity in sorted(identities['instructors'] - current_instructors):
        plan.create.append(_item('instructor', ' '.join(identity), {'exists': True}, 'Canonical instructor is missing.'))
    for identity in sorted(current_instructors - identities['instructors']):
        plan.delete.append(_item('instructor', ' '.join(identity), {'exists': False}, 'Visitor-created noncanonical instructor will be removed.'))

    scalar_specs = (
        (
            'location',
            Locations.objects.filter(organization=organization),
            'loc_name',
            {
                item['name']: {
                    'loc_short': item['short'],
                    'availible': item['available'],
                }
                for item in DEMO_SCENARIO['locations']
            },
        ),
        (
            'activity',
            Course.objects.filter(organization=organization),
            'course_name',
            {
                item['name']: {
                    'abriviation': item['short'],
                    'course_len': item['length'],
                    'required_instructor_count': 1,
                }
                for item in DEMO_SCENARIO['activities']
            },
        ),
        (
            'school/cohort',
            Schools.schools_list.filter(organization=organization),
            'school_name',
            {
                item['name']: {
                    'ag_num': item['groups'],
                    'arrive': item['arrive'],
                    'depart': item['depart'],
                    'total_students': item['students'],
                    'attending_year': item['attending_year'],
                }
                for item in DEMO_SCENARIO['schools']
            },
        ),
    )
    for record_type, queryset, identity_field, expected_records in scalar_specs:
        for record in queryset.filter(**{f'{identity_field}__in': expected_records}):
            identity = getattr(record, identity_field)
            expected = expected_records[identity]
            current = {field_name: getattr(record, field_name) for field_name in expected}
            target = plan.unchanged if current == expected else plan.update
            target.append(_item(record_type, identity, expected, 'Canonical scalar fields already match.' if target is plan.unchanged else 'Canonical scalar fields require restoration.', current))

    canonical_courses = {
        record.course_name: record
        for record in Course.objects.filter(
            organization=organization,
            course_name__in=identities['courses'],
        ).prefetch_related('primary_locs')
    }
    for expected in DEMO_SCENARIO['activities']:
        record = canonical_courses.get(expected['name'])
        if record is None:
            continue
        current = tuple(sorted(record.primary_locs.values_list('loc_name', flat=True)))
        wanted = tuple(sorted(expected['locations']))
        target = plan.unchanged if current == wanted else plan.reconcile
        target.append(_item('activity locations', expected['name'], wanted, 'Canonical relationship already matches.' if target is plan.unchanged else 'Canonical relationship requires reconciliation.', current))

    canonical_schools = {
        record.school_name: record
        for record in Schools.schools_list.filter(
            organization=organization,
            school_name__in=identities['schools'],
        ).prefetch_related('subject')
    }
    for expected in DEMO_SCENARIO['schools']:
        record = canonical_schools.get(expected['name'])
        if record is None:
            continue
        current = tuple(sorted(record.subject.values_list('course_name', flat=True)))
        wanted = tuple(sorted(expected['activities']))
        target = plan.unchanged if current == wanted else plan.reconcile
        target.append(_item('school activity selections', expected['name'], wanted, 'Canonical relationship already matches.' if target is plan.unchanged else 'Canonical relationship requires reconciliation.', current))

    schedule_schools = tuple(
        sorted(schedule.schools.values_list('school_name', flat=True))
    )
    expected_schedule_schools = tuple(sorted(DEMO_SCENARIO['schedule']['schools']))
    target = (
        plan.unchanged
        if schedule_schools == expected_schedule_schools
        else plan.reconcile
    )
    target.append(_item('schedule schools', schedule.sched_name, expected_schedule_schools, 'Canonical relationship already matches.' if target is plan.unchanged else 'Canonical relationship requires reconciliation.', schedule_schools))

    issues = _stored_generation_issues(organization, schedule)
    if issues:
        plan.restore.append(_item('schedule generated and operational state', schedule.sched_name, {'canonical': True}, 'Stored baseline requires regeneration.', issues))
    else:
        plan.unchanged.append(_item('schedule generated and operational state', schedule.sched_name, {'canonical': True}, 'Stored baseline already passes generation checks.'))
    return plan


def _delete_noncanonical_records(organization, plan):
    identities = _canonical_identities()
    extra_schedules = TheSched.objects.filter(organization=organization).exclude(
        sched_name__in=identities['schedules']
    )
    InstructorScheduleAvailability.objects.filter(
        organization=organization,
        schedule__in=extra_schedules,
    ).delete()
    InstructorScheduleParticipation.objects.filter(
        organization=organization,
        schedule__in=extra_schedules,
    ).delete()
    for schedule in extra_schedules:
        schedule.schools.clear()
    extra_schedules.delete()

    extra_schools = Schools.schools_list.filter(organization=organization).exclude(
        school_name__in=identities['schools']
    )
    for school in extra_schools:
        school.subject.clear()
    extra_schools.delete()

    canonical_pairs = identities['instructors']
    extra_instructor_ids = [
        record.pk
        for record in Instructor.objects.filter(organization=organization)
        if (record.fname, record.lname) not in canonical_pairs
    ]
    InstructorScheduleAvailability.objects.filter(
        organization=organization,
        instructor_id__in=extra_instructor_ids,
    ).delete()
    InstructorScheduleParticipation.objects.filter(
        organization=organization,
        instructor_id__in=extra_instructor_ids,
    ).delete()
    InstructorCertification.objects.filter(
        instructor_id__in=extra_instructor_ids,
        instructor__organization=organization,
    ).delete()
    InstructorLeadershipRole.objects.filter(
        instructor_id__in=extra_instructor_ids,
        instructor__organization=organization,
    ).delete()
    Instructor.objects.filter(
        organization=organization,
        pk__in=extra_instructor_ids,
    ).delete()

    extra_courses = Course.objects.filter(organization=organization).exclude(
        course_name__in=identities['courses']
    )
    ActivityCertificationRequirement.objects.filter(course__in=extra_courses).delete()
    for course in extra_courses:
        course.primary_locs.clear()
    extra_courses.delete()

    extra_certifications = Certification.objects.filter(
        organization=organization
    ).exclude(name__in=identities['certifications'])
    ActivityCertificationRequirement.objects.filter(
        certification__in=extra_certifications
    ).delete()
    InstructorCertification.objects.filter(
        certification__in=extra_certifications
    ).delete()
    extra_certifications.delete()

    LeadershipRole.objects.filter(organization=organization).delete()
    Locations.objects.filter(organization=organization).exclude(
        loc_name__in=identities['locations']
    ).delete()
    return tuple(plan.delete)


def _apply_canonical_scenario(organization):
    applied = DemoApplyResult(organization.name)
    _apply_locations(organization, applied)
    certifications = _apply_certifications(organization, applied)
    courses = _apply_courses(organization, applied)
    _apply_activity_requirement(organization, courses, certifications, applied)
    schools = _apply_schools(organization, courses, applied)
    instructors = _apply_instructors(organization, applied)
    _apply_instructor_certifications(
        organization,
        instructors,
        certifications,
        applied,
    )
    schedule = _apply_schedule(organization, schools, applied)
    _apply_participation(organization, schedule, instructors, applied)
    _apply_availability(organization, schedule, instructors, applied)
    return applied, schedule


def _reset_prepared_demo_session(*, demo_session, clock):
    session_id = getattr(demo_session, 'identifier', demo_session)
    now = clock()
    if timezone.is_naive(now):
        raise PreparedDemoResetError('Prepared reset clock must be timezone-aware.')

    with transaction.atomic():
        try:
            session = (
                DemoSession.objects.select_for_update()
                .select_related('user', 'organization')
                .get(identifier=session_id)
            )
        except (DemoSession.DoesNotExist, ValidationError, ValueError, TypeError) as error:
            raise PreparedDemoResetError('Prepared DemoSession was not found.') from error

        organization = Organization.objects.select_for_update().get(
            pk=session.organization_id
        )
        initial = PreparedDemoResetPlan()
        membership, schedule = _validate_ownership(session, now, initial)
        membership = OrganizationMembership.objects.select_for_update().get(
            pk=membership.pk
        )
        schedule = TheSched.objects.select_for_update().get(pk=schedule.pk)
        _reject_foreign_relationships(organization, initial)
        if session.scenario_version == WORKING_DEMO_SCENARIO_VERSION:
            plan = PreparedDemoResetPlan()
            schedule, outcomes = _replace_with_working_scenario(organization)
            session.refresh_from_db()
            return PreparedDemoResetResult(
                demo_session=session,
                user=session.user,
                organization=organization,
                membership=membership,
                schedule=schedule,
                plan=plan,
                applied=DemoApplyResult(organization.name),
                deleted=(),
                final_validation={
                    'generation_complete': outcomes[schedule.sched_name],
                    'operational_replay_clean': True,
                    'assignment_complete': True,
                },
                already_canonical=False,
                completed=True,
            )
        plan = _build_plan(organization, schedule)

        deleted = _delete_noncanonical_records(organization, plan)
        applied, schedule = _apply_canonical_scenario(organization)
        changed = bool(
            deleted or applied.created or applied.updated or applied.reconciled
        )
        generation_issues = _stored_generation_issues(organization, schedule)
        if changed or generation_issues:
            schedule.generate_and_store_schedule()
            schedule.refresh_from_db()
            applied.reconciled.append(_item('schedule generated output', schedule.sched_name, {'generation_complete': True}, 'Canonical schedule regenerated through the model lifecycle.', generation_issues))

        _validate_applied_reference_data(organization)
        assignment = _validate_demo_starting_state(organization, schedule)
        final_plan = PreparedDemoResetPlan()
        final_membership, final_schedule = _validate_ownership(
            DemoSession.objects.select_related('user', 'organization').get(pk=session.pk),
            now,
            final_plan,
        )
        if final_membership.pk != membership.pk or final_schedule.pk != schedule.pk:
            _block(final_plan, 'Prepared ownership changed during reset.')

        session.refresh_from_db()
        organization.refresh_from_db()
        membership.refresh_from_db()
        schedule.refresh_from_db()
        return PreparedDemoResetResult(
            demo_session=session,
            user=session.user,
            organization=organization,
            membership=membership,
            schedule=schedule,
            plan=plan,
            applied=applied,
            deleted=deleted,
            final_validation={
                'generation_complete': schedule.sched_data.get('generation_complete') is True,
                'operational_replay_clean': True,
                'assignment_complete': assignment['coverage']['complete'],
            },
            already_canonical=not changed and not generation_issues,
            completed=True,
        )


def reset_prepared_demo_session(*, demo_session, clock=timezone.now):
    """Restore one active prepared visitor without replacing its ownership."""
    try:
        return _reset_prepared_demo_session(
            demo_session=demo_session,
            clock=clock,
        )
    except PreparedDemoResetError:
        raise
    except Exception as error:
        raise PreparedDemoResetError(
            f'Prepared demo reset failed during {error.__class__.__name__}; '
            'all reset writes were rolled back.'
        ) from error
