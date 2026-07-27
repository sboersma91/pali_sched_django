"""Read-only planning boundary for a future demo environment.

This module intentionally performs only organization-scoped ORM reads. The
temporary setting-based ownership rule is acceptable only while every operation
is a dry run; future write behavior requires separately approved safeguards.
"""

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from members.models import DEFAULT_ORGANIZATION_NAME, Organization

from .models import (
    ActivityCertificationRequirement,
    Certification,
    Course,
    Instructor,
    InstructorScheduleAvailability,
    InstructorScheduleParticipation,
    Locations,
    Schools,
    TheSched,
)
from .instructor_assignment import (
    evaluate_occurrence_constraints,
    evaluate_occurrence_qualifications,
    preload_instructor_availability,
    run_instructor_assignment,
)
from .schedule_blocks import (
    SCHEDULE_SLOT_KEYS,
    UNASSIGNED_SLOT_VALUE,
    UNAVAILABLE_SLOT_VALUE,
)


DEMO_SCENARIO = {
    'locations': (
        {'name': 'Demo Commons', 'short': 'DC', 'available': True},
        {'name': 'Demo Field', 'short': 'DF', 'available': True},
        {'name': 'Demo Studio', 'short': 'DS', 'available': True},
        {'name': 'Demo Workshop', 'short': 'DW', 'available': True},
    ),
    'certifications': ('Demo Field Safety', 'Demo Technical Skills'),
    'activities': (
        {'name': 'Demo Navigation', 'short': 'DNAV', 'length': 1, 'locations': ('Demo Field',)},
        {'name': 'Demo Creative Lab', 'short': 'DART', 'length': 1, 'locations': ('Demo Studio',)},
        {'name': 'Demo Team Challenge', 'short': 'DTEAM', 'length': 1, 'locations': ('Demo Commons',)},
        {'name': 'Demo Technical Course', 'short': 'DTECH', 'length': 2, 'locations': ('Demo Workshop',)},
        {'name': 'Demo Evening Program', 'short': 'DNITE', 'length': 0, 'locations': ('Demo Commons',)},
    ),
    'activity_requirement': {
        'activity': 'Demo Technical Course',
        'certification': 'Demo Technical Skills',
    },
    'schools': (
        {
            'name': 'Demo Cohort North',
            'groups': 2,
            'arrive': 'Mon',
            'depart': 'Fri',
            'students': 32,
            'attending_year': date(2030, 1, 1),
            'activities': (
                'Demo Navigation',
                'Demo Technical Course',
                'Demo Evening Program',
            ),
        },
        {
            'name': 'Demo Cohort South',
            'groups': 2,
            'arrive': 'Mon',
            'depart': 'Fri',
            'students': 32,
            'attending_year': date(2030, 1, 1),
            'activities': (
                'Demo Creative Lab',
                'Demo Team Challenge',
            ),
        },
    ),
    'instructors': (
        {'first': 'Alex', 'last': 'Demo', 'certifications': ('Demo Technical Skills',)},
        {'first': 'Blair', 'last': 'Demo', 'certifications': ('Demo Technical Skills',)},
        {'first': 'Casey', 'last': 'Demo', 'certifications': ('Demo Field Safety',)},
        {'first': 'Devon', 'last': 'Demo', 'certifications': ()},
        {'first': 'Emery', 'last': 'Demo', 'certifications': ()},
    ),
    'schedule': {
        'name': 'Demo Program Week',
        'schools': ('Demo Cohort North', 'Demo Cohort South'),
    },
    'participating_instructors': (
        {'first': 'Alex', 'last': 'Demo'},
        {'first': 'Blair', 'last': 'Demo'},
        {'first': 'Casey', 'last': 'Demo'},
        {'first': 'Devon', 'last': 'Demo'},
    ),
    'participation_opt_out': {'first': 'Emery', 'last': 'Demo'},
    'availability_exception': {
        'first': 'Casey',
        'last': 'Demo',
        'slot_key': 'tue_am1',
        'state': InstructorScheduleAvailability.UNAVAILABLE,
    },
    'qualification_sensitive_activity': 'Demo Technical Course',
    'eligible_manual_alternate': {'first': 'Blair', 'last': 'Demo'},
    'expected_group_count': 4,
    'required_schedule_slots': SCHEDULE_SLOT_KEYS,
}
PREPARED_DEMO_SCENARIO_VERSION = 'canonical-v1'


@dataclass(frozen=True)
class PlanItem:
    record_type: str
    identity: str
    expected: Any
    reason: str
    current: Any = None


@dataclass
class DemoInspectionResult:
    organization_identifier: str
    create: list[PlanItem] = field(default_factory=list)
    update: list[PlanItem] = field(default_factory=list)
    reconcile: list[PlanItem] = field(default_factory=list)
    restore: list[PlanItem] = field(default_factory=list)
    unchanged: list[PlanItem] = field(default_factory=list)
    warnings: list[PlanItem] = field(default_factory=list)
    blockers: list[PlanItem] = field(default_factory=list)

    def categories(self):
        return (
            ('create', self.create),
            ('update', self.update),
            ('reconcile', self.reconcile),
            ('restore', self.restore),
            ('unchanged', self.unchanged),
            ('warnings', self.warnings),
            ('blockers', self.blockers),
        )


@dataclass
class DemoApplyResult:
    organization_identifier: str
    created: list[PlanItem] = field(default_factory=list)
    updated: list[PlanItem] = field(default_factory=list)
    reconciled: list[PlanItem] = field(default_factory=list)
    unchanged: list[PlanItem] = field(default_factory=list)
    remaining: DemoInspectionResult | None = None

    def categories(self):
        return (
            ('created', self.created),
            ('updated', self.updated),
            ('reconciled', self.reconciled),
            ('unchanged', self.unchanged),
        )


@dataclass
class DemoResetResult:
    organization_identifier: str
    plan: DemoInspectionResult
    applied: DemoApplyResult
    final: DemoInspectionResult
    already_canonical: bool


class DemoSafetyError(Exception):
    def __init__(self, message, result):
        super().__init__(message)
        self.result = result


def _item(record_type, identity, expected, reason, current=None):
    return PlanItem(record_type, identity, expected, reason, current)


def _block(identifier, reason):
    result = DemoInspectionResult(identifier)
    result.blockers.append(_item('organization', identifier, None, reason))
    raise DemoSafetyError(reason, result)


def resolve_demo_organization(identifier):
    """Resolve exactly one explicitly authorized organization without fallback creation."""
    if not getattr(settings, 'DEMO_SCAFFOLDING_ENABLED', False):
        _block(identifier, 'Demo scaffolding is disabled.')
    allowed = getattr(settings, 'DEMO_ORGANIZATION_IDENTIFIER', '').strip()
    if not allowed:
        _block(identifier, 'No allowed demo organization identifier is configured.')
    if identifier != allowed:
        _block(identifier, 'The requested organization does not exactly match the configured demo identifier.')
    if identifier == DEFAULT_ORGANIZATION_NAME:
        _block(identifier, 'Default Organization can never be targeted for demo scaffolding.')

    matches = list(Organization.objects.filter(name=identifier)[:2])
    if not matches:
        _block(identifier, 'The configured demo organization does not exist.')
    if len(matches) != 1:
        _block(identifier, 'The demo organization identifier is ambiguous.')
    if matches[0].name != allowed:
        _block(identifier, 'The resolved organization does not satisfy the temporary ownership rule.')
    if matches[0].purpose == Organization.Purpose.TEMPORARY_DEMO:
        _block(
            identifier,
            'A temporary demo organization cannot be targeted by canonical demo scaffolding.',
        )
    if matches[0].purpose != Organization.Purpose.CANONICAL_DEMO:
        _block(
            identifier,
            'The configured demo organization is not classified as canonical_demo. '
            'Run the approved canonical classification command before using '
            'canonical demo scaffolding.',
        )
    return matches[0]


def _compare_fields(result, record_type, identity, record, expected_fields):
    current = {field: getattr(record, field) for field in expected_fields}
    if current == expected_fields:
        result.unchanged.append(_item(record_type, identity, expected_fields, 'Expected fields already match.', current))
    else:
        result.update.append(_item(record_type, identity, expected_fields, 'Expected fields differ.', current))


def inspect_demo_environment(identifier):
    """Build a structured future-change plan using organization-scoped reads only."""
    organization = resolve_demo_organization(identifier)
    result = DemoInspectionResult(identifier)
    result.unchanged.append(_item('organization', identifier, {'name': identifier}, 'Safe demo target resolved.', {'name': organization.name}))

    memberships = list(organization.memberships.select_related('user').all())
    if memberships:
        for membership in memberships:
            result.unchanged.append(_item('organization membership', membership.user.get_username(), {'organization': identifier}, 'Existing membership is organization-owned.', {'organization': identifier}))
    else:
        result.create.append(_item('organization membership', 'demo operator (credential provisioning deferred)', {'organization': identifier}, 'A future demo operator membership is required.'))
        result.warnings.append(_item('credentials', 'demo operator', None, 'Credential and user provisioning remain deliberately outside this command.'))

    locations = {item.loc_name: item for item in Locations.objects.filter(organization=organization)}
    for expected in DEMO_SCENARIO['locations']:
        record = locations.get(expected['name'])
        if record is None:
            result.create.append(_item('location', expected['name'], expected, 'Expected demo location is missing.'))
        else:
            _compare_fields(result, 'location', expected['name'], record, {
                'loc_short': expected['short'],
                'availible': expected['available'],
            })

    certifications = {item.name: item for item in Certification.objects.filter(organization=organization)}
    for name in DEMO_SCENARIO['certifications']:
        target = result.unchanged if name in certifications else result.create
        target.append(_item('certification', name, {'name': name}, 'Expected certification exists.' if name in certifications else 'Expected certification is missing.'))

    courses = {
        item.course_name: item
        for item in Course.objects.filter(organization=organization).prefetch_related('primary_locs')
    }
    for expected in DEMO_SCENARIO['activities']:
        record = courses.get(expected['name'])
        if record is None:
            result.create.append(_item('activity', expected['name'], expected, 'Expected demo activity is missing.'))
            continue
        _compare_fields(result, 'activity', expected['name'], record, {
            'abriviation': expected['short'],
            'course_len': expected['length'],
            'required_instructor_count': 1,
        })
        current_locations = tuple(sorted(record.primary_locs.filter(organization=organization).values_list('loc_name', flat=True)))
        expected_locations = tuple(sorted(expected['locations']))
        target = result.unchanged if current_locations == expected_locations else result.reconcile
        target.append(_item('activity locations', expected['name'], expected_locations, 'Activity location relationship matches.' if target is result.unchanged else 'Activity locations require reconciliation.', current_locations))

    requirement = DEMO_SCENARIO['activity_requirement']
    requirement_exists = ActivityCertificationRequirement.objects.filter(
        course__organization=organization,
        course__course_name=requirement['activity'],
        certification__organization=organization,
        certification__name=requirement['certification'],
    ).exists()
    target = result.unchanged if requirement_exists else result.reconcile
    target.append(_item('activity certification requirement', f"{requirement['activity']} → {requirement['certification']}", requirement, 'Requirement exists.' if requirement_exists else 'Requirement must be reconciled.'))

    schools = {
        item.school_name: item
        for item in Schools.schools_list.filter(organization=organization).prefetch_related('subject')
    }
    for expected in DEMO_SCENARIO['schools']:
        record = schools.get(expected['name'])
        if record is None:
            result.create.append(_item('school/cohort', expected['name'], expected, 'Expected demo cohort is missing.'))
            continue
        _compare_fields(result, 'school/cohort', expected['name'], record, {
            'ag_num': expected['groups'],
            'arrive': expected['arrive'],
            'depart': expected['depart'],
            'total_students': expected['students'],
            'attending_year': expected['attending_year'],
        })
        current_activities = tuple(sorted(record.subject.filter(organization=organization).values_list('course_name', flat=True)))
        expected_activities = tuple(sorted(expected['activities']))
        target = result.unchanged if current_activities == expected_activities else result.reconcile
        target.append(_item('school activity selections', expected['name'], expected_activities, 'Activity selections match.' if target is result.unchanged else 'Activity selections require reconciliation.', current_activities))

    instructors = {
        (item.fname, item.lname): item
        for item in Instructor.objects.filter(organization=organization).prefetch_related('certifications')
    }
    for expected in DEMO_SCENARIO['instructors']:
        key = (expected['first'], expected['last'])
        identity = ' '.join(key)
        record = instructors.get(key)
        if record is None:
            result.create.append(_item('instructor', identity, expected, 'Expected fictional instructor is missing.'))
            continue
        result.unchanged.append(_item('instructor', identity, {'name': identity}, 'Expected instructor exists.', {'name': str(record)}))
        current_certifications = tuple(sorted(record.certifications.filter(organization=organization).values_list('name', flat=True)))
        expected_certifications = tuple(sorted(expected['certifications']))
        target = result.unchanged if current_certifications == expected_certifications else result.reconcile
        target.append(_item('instructor certifications', identity, expected_certifications, 'Certification relationships match.' if target is result.unchanged else 'Certification relationships require reconciliation.', current_certifications))

    schedule_spec = DEMO_SCENARIO['schedule']
    schedules = list(TheSched.objects.filter(organization=organization, sched_name=schedule_spec['name']).prefetch_related('schools'))
    schedule = schedules[0] if len(schedules) == 1 else None
    if schedule is None:
        result.create.append(_item('schedule', schedule_spec['name'], schedule_spec, 'Expected demo schedule is missing.'))
        result.reconcile.append(_item('schedule schools', schedule_spec['name'], schedule_spec['schools'], 'Future schedule-school relationships are required.'))
        opt_out = DEMO_SCENARIO['participation_opt_out']
        result.reconcile.append(_item(
            'schedule participation',
            f"{opt_out['first']} {opt_out['last']}",
            {'state': InstructorScheduleParticipation.NOT_PARTICIPATING},
            'Future participation opt-out is required after the schedule exists.',
        ))
        exception = DEMO_SCENARIO['availability_exception']
        result.reconcile.append(_item(
            'schedule availability',
            f"{exception['first']} {exception['last']} — {exception['slot_key']}",
            {'state': exception['state']},
            'Future availability exception is required after the schedule exists.',
        ))
        result.restore.append(_item('schedule generated output', schedule_spec['name'], {'generated_schedule_present': True, 'generation_complete': True}, 'Stored generated output must be created by future approved work.'))
        result.restore.append(_item('schedule operational state', schedule_spec['name'], {'manual_moves': [], 'manual_instructor_overrides': [], 'instructor_override_revision': 0}, 'Future reset must establish a clean starting state.'))
    else:
        current_schools = tuple(sorted(schedule.schools.filter(organization=organization).values_list('school_name', flat=True)))
        expected_schools = tuple(sorted(schedule_spec['schools']))
        target = result.unchanged if current_schools == expected_schools else result.reconcile
        target.append(_item('schedule schools', schedule_spec['name'], expected_schools, 'Schedule-school relationships match.' if target is result.unchanged else 'Schedule-school relationships require reconciliation.', current_schools))
        _inspect_schedule_state(result, schedule)
        _inspect_schedule_staffing_state(result, organization, schedule, instructors)
        try:
            _validate_demo_starting_state(organization, schedule)
        except DemoSafetyError as error:
            result.restore.extend(
                _item(
                    'canonical starting state',
                    schedule.sched_name,
                    {'valid': True},
                    blocker.reason,
                )
                for blocker in error.result.blockers
            )
        except Exception as error:
            result.restore.append(_item(
                'canonical starting state',
                schedule.sched_name,
                {'valid': True},
                f'Canonical starting-state validation could not complete: {error}',
            ))
        else:
            result.unchanged.append(_item(
                'canonical starting state',
                schedule.sched_name,
                {'valid': True},
                'Generated output, operational replay, and automatic assignment are valid.',
            ))

    return result


def _validated_save(record, *, update_fields=None):
    record.full_clean()
    record.save(update_fields=update_fields)


def _record_change(result, changed, category, record_type, identity, expected, reason):
    target = getattr(result, category if changed else 'unchanged')
    target.append(_item(record_type, identity, expected, reason))


def _reconcile_fields(record, expected_fields):
    changed_fields = [
        field
        for field, expected in expected_fields.items()
        if getattr(record, field) != expected
    ]
    for field in changed_fields:
        setattr(record, field, expected_fields[field])
    if changed_fields:
        _validated_save(record, update_fields=changed_fields)
    return changed_fields


def _unique_owned(queryset, *, record_type, identity):
    matches = list(queryset[:2])
    if len(matches) > 1:
        raise ValidationError(
            f'Multiple {record_type} records match canonical identity {identity!r}.'
        )
    return matches[0] if matches else None


def apply_demo_reference_data(identifier, *, inspection=None):
    """Atomically create and reconcile stable scenario reference records only."""
    inspection = inspection or inspect_demo_environment(identifier)
    if inspection.blockers:
        raise DemoSafetyError('Inspection contains blocking conditions.', inspection)

    organization = resolve_demo_organization(identifier)
    if not organization.memberships.exists():
        blocked = deepcopy(inspection)
        blocked.blockers.append(_item(
            'organization membership',
            'existing demo operator',
            {'organization': identifier},
            'Apply requires an existing organization membership; account provisioning is separate.',
        ))
        raise DemoSafetyError(
            'Apply requires an existing organization membership.',
            blocked,
        )

    result = DemoApplyResult(identifier)
    with transaction.atomic():
        organization = Organization.objects.select_for_update().get(
            pk=organization.pk,
            name=identifier,
        )
        _apply_locations(organization, result)
        certifications = _apply_certifications(organization, result)
        courses = _apply_courses(organization, result)
        _apply_activity_requirement(organization, courses, certifications, result)
        schools = _apply_schools(organization, courses, result)
        instructors = _apply_instructors(organization, result)
        _apply_instructor_certifications(
            organization,
            instructors,
            certifications,
            result,
        )
        schedule = _apply_schedule(organization, schools, result)
        _apply_participation(organization, schedule, instructors, result)
        _apply_availability(organization, schedule, instructors, result)
        generation_input_types = {
            'location',
            'activity',
            'activity locations',
            'activity certification requirement',
            'school/cohort',
            'school activity selections',
            'schedule',
            'schedule schools',
        }
        generation_inputs_changed = any(
            item.record_type in generation_input_types
            for items in (result.created, result.updated, result.reconciled)
            for item in items
        )
        baseline_issues = _stored_generation_issues(organization, schedule)
        if generation_inputs_changed or baseline_issues:
            schedule.generate_and_store_schedule()
            result.reconciled.append(_item(
                'schedule generated output',
                schedule.sched_name,
                {'generation_complete': True},
                'Schedule generated and stored through the normal model lifecycle.',
                baseline_issues,
            ))
        else:
            result.unchanged.append(_item(
                'schedule generated output',
                schedule.sched_name,
                {'generation_complete': True},
                'Valid generated baseline already exists; generation was not repeated.',
            ))
        _validate_applied_reference_data(organization)
        _validate_demo_starting_state(organization, schedule)
        result.remaining = inspect_demo_environment(identifier)

    return result


def build_demo_scenario_for_organization(
    organization,
    demo_session,
    *,
    scenario=DEMO_SCENARIO,
    expected_schedule_name=None,
    ownership_context='prepared_visitor',
    allow_stable_creation=True,
    establish_mutable_baseline=True,
    require_generation=True,
    require_full_validation=True,
):
    """Build the approved scenario in an already authorized temporary target."""
    expected_schedule_name = (
        expected_schedule_name or DEMO_SCENARIO['schedule']['name']
    )
    if scenario != DEMO_SCENARIO:
        raise ValidationError('Only the approved prepared demo scenario is supported.')
    if expected_schedule_name != DEMO_SCENARIO['schedule']['name']:
        raise ValidationError('The prepared schedule name is not canonical.')
    if ownership_context != 'prepared_visitor':
        raise ValidationError('The scenario builder ownership context is not approved.')
    if not (
        allow_stable_creation
        and establish_mutable_baseline
        and require_generation
        and require_full_validation
    ):
        raise ValidationError(
            'Prepared visitor construction requires full creation and validation.'
        )
    if organization.purpose != Organization.Purpose.TEMPORARY_DEMO:
        raise ValidationError('Prepared scenario targets must be temporary demo organizations.')
    if (
        demo_session.organization_id != organization.pk
        or demo_session.mode != demo_session.Mode.PREPARED
        or demo_session.status != demo_session.Status.PROVISIONING
        or demo_session.expires_at <= timezone.now()
    ):
        raise ValidationError('Prepared scenario ownership is not in provisioning state.')
    demo_session.full_clean()

    result = DemoApplyResult(organization.name)
    _apply_locations(organization, result)
    certifications = _apply_certifications(organization, result)
    courses = _apply_courses(organization, result)
    _apply_activity_requirement(organization, courses, certifications, result)
    schools = _apply_schools(organization, courses, result)
    instructors = _apply_instructors(organization, result)
    _apply_instructor_certifications(
        organization,
        instructors,
        certifications,
        result,
    )
    schedule = _apply_schedule(organization, schools, result)
    _apply_participation(organization, schedule, instructors, result)
    _apply_availability(organization, schedule, instructors, result)
    schedule.generate_and_store_schedule()
    schedule.refresh_from_db()
    _validate_applied_reference_data(organization)
    assignment = _validate_demo_starting_state(organization, schedule)
    return result, schedule, assignment


def reset_demo_environment(identifier, *, inspection=None):
    """Restore only the existing canonical demo schedule and owned scenario state."""
    inspection = inspection or inspect_demo_environment(identifier)
    if inspection.blockers:
        raise DemoSafetyError('Reset inspection contains blocking conditions.', inspection)

    organization = resolve_demo_organization(identifier)
    if not organization.memberships.exists():
        blocked = deepcopy(inspection)
        blocked.blockers.append(_item(
            'organization membership',
            'existing demo operator',
            {'organization': identifier},
            'Reset requires an existing organization membership.',
        ))
        raise DemoSafetyError(
            'Reset requires an existing organization membership.',
            blocked,
        )

    schedule_name = DEMO_SCENARIO['schedule']['name']
    schedule_matches = list(TheSched.objects.filter(
        organization=organization,
        sched_name=schedule_name,
    )[:2])
    if not schedule_matches:
        blocked = deepcopy(inspection)
        blocked.blockers.append(_item(
            'schedule',
            schedule_name,
            {'exists': True},
            'Reset requires the existing canonical demo schedule; run confirmed apply for initial setup.',
        ))
        raise DemoSafetyError(
            'Reset requires the existing canonical demo schedule.',
            blocked,
        )
    if len(schedule_matches) != 1:
        blocked = deepcopy(inspection)
        blocked.blockers.append(_item(
            'schedule',
            schedule_name,
            {'unique': True},
            'The canonical demo schedule is ambiguous.',
        ))
        raise DemoSafetyError('The canonical demo schedule is ambiguous.', blocked)

    with transaction.atomic():
        locked_organization = Organization.objects.select_for_update().get(
            pk=organization.pk,
            name=identifier,
        )
        locked_schedule = TheSched.objects.select_for_update().get(
            pk=schedule_matches[0].pk,
            organization=locked_organization,
            sched_name=schedule_name,
        )
        # Pass a fresh inspection into the existing reconciliation boundary
        # after both reset targets are locked. Its schedule query resolves the
        # same locked row, avoiding mutation through the pre-lock instance.
        locked_plan = inspect_demo_environment(identifier)
        applied = apply_demo_reference_data(
            identifier,
            inspection=locked_plan,
        )
        locked_schedule.refresh_from_db()
        _validate_demo_starting_state(locked_organization, locked_schedule)
        final = inspect_demo_environment(identifier)
        if final.blockers:
            raise DemoSafetyError(
                'Final reset inspection contains blocking conditions.',
                final,
            )

    changed = bool(applied.created or applied.updated or applied.reconciled)
    return DemoResetResult(
        organization_identifier=identifier,
        plan=inspection,
        applied=applied,
        final=final,
        already_canonical=not changed,
    )


def _apply_locations(organization, result):
    for expected in DEMO_SCENARIO['locations']:
        record = _unique_owned(
            Locations.objects.filter(
                organization=organization,
                loc_name=expected['name'],
            ),
            record_type='location',
            identity=expected['name'],
        )
        fields = {
            'loc_short': expected['short'],
            'availible': expected['available'],
        }
        if record is None:
            record = Locations(
                organization=organization,
                loc_name=expected['name'],
                **fields,
            )
            _validated_save(record)
            result.created.append(_item('location', expected['name'], expected, 'Missing location created.'))
        else:
            changed = _reconcile_fields(record, fields)
            _record_change(result, changed, 'updated', 'location', expected['name'], fields, 'Location fields reconciled.' if changed else 'Location already matched.')


def _apply_certifications(organization, result):
    records = {}
    for name in DEMO_SCENARIO['certifications']:
        record = _unique_owned(
            Certification.objects.filter(organization=organization, name=name),
            record_type='certification',
            identity=name,
        )
        if record is None:
            record = Certification(organization=organization, name=name)
            _validated_save(record)
            result.created.append(_item('certification', name, {'name': name}, 'Missing certification created.'))
        else:
            result.unchanged.append(_item('certification', name, {'name': name}, 'Certification already matched.'))
        records[name] = record
    return records


def _apply_courses(organization, result):
    records = {}
    locations = {
        record.loc_name: record
        for record in Locations.objects.filter(
            organization=organization,
            loc_name__in=[
                location['name']
                for location in DEMO_SCENARIO['locations']
            ],
        )
    }
    for expected in DEMO_SCENARIO['activities']:
        record = _unique_owned(
            Course.objects.filter(
                organization=organization,
                course_name=expected['name'],
            ),
            record_type='activity',
            identity=expected['name'],
        )
        fields = {
            'abriviation': expected['short'],
            'course_len': expected['length'],
            'required_instructor_count': 1,
        }
        if record is None:
            record = Course(
                organization=organization,
                course_name=expected['name'],
                **fields,
            )
            _validated_save(record)
            result.created.append(_item('activity', expected['name'], expected, 'Missing activity created.'))
        else:
            changed = _reconcile_fields(record, fields)
            _record_change(result, changed, 'updated', 'activity', expected['name'], fields, 'Activity fields reconciled.' if changed else 'Activity already matched.')

        expected_locations = [locations[name] for name in expected['locations']]
        current_ids = set(record.primary_locs.values_list('pk', flat=True))
        expected_ids = {location.pk for location in expected_locations}
        if current_ids != expected_ids:
            record.primary_locs.set(expected_locations)
            result.reconciled.append(_item('activity locations', expected['name'], expected['locations'], 'Canonical activity locations reconciled.'))
        else:
            result.unchanged.append(_item('activity locations', expected['name'], expected['locations'], 'Activity locations already matched.'))
        records[expected['name']] = record
    return records


def _apply_activity_requirement(organization, courses, certifications, result):
    expected = DEMO_SCENARIO['activity_requirement']
    course = courses[expected['activity']]
    certification = certifications[expected['certification']]
    relationship = _unique_owned(
        ActivityCertificationRequirement.objects.filter(
            course=course,
            course__organization=organization,
            certification=certification,
            certification__organization=organization,
        ),
        record_type='activity certification requirement',
        identity=f"{expected['activity']} → {expected['certification']}",
    )
    identity = f"{expected['activity']} → {expected['certification']}"
    if relationship is None:
        relationship = ActivityCertificationRequirement(
            course=course,
            certification=certification,
        )
        _validated_save(relationship)
        result.reconciled.append(_item('activity certification requirement', identity, expected, 'Missing requirement created.'))
    else:
        result.unchanged.append(_item('activity certification requirement', identity, expected, 'Requirement already matched.'))


def _apply_schools(organization, courses, result):
    records = {}
    for expected in DEMO_SCENARIO['schools']:
        record = _unique_owned(
            Schools.schools_list.filter(
                organization=organization,
                school_name=expected['name'],
            ),
            record_type='school/cohort',
            identity=expected['name'],
        )
        fields = {
            'ag_num': expected['groups'],
            'arrive': expected['arrive'],
            'depart': expected['depart'],
            'total_students': expected['students'],
            'attending_year': expected['attending_year'],
        }
        if record is None:
            record = Schools(
                organization=organization,
                school_name=expected['name'],
                **fields,
            )
            _validated_save(record)
            result.created.append(_item('school/cohort', expected['name'], expected, 'Missing cohort created.'))
        else:
            changed = _reconcile_fields(record, fields)
            _record_change(result, changed, 'updated', 'school/cohort', expected['name'], fields, 'Cohort fields reconciled.' if changed else 'Cohort already matched.')

        expected_courses = [courses[name] for name in expected['activities']]
        current_ids = set(record.subject.values_list('pk', flat=True))
        expected_ids = {course.pk for course in expected_courses}
        relationship_changed = current_ids != expected_ids
        if relationship_changed:
            record.subject.set(expected_courses)
        previous_sorted = record.sorted_subject_lst
        record.update_sorted_subject_lst()
        derived_changed = previous_sorted != record.sorted_subject_lst
        if derived_changed:
            _validated_save(record, update_fields=['sorted_subject_lst'])
        if relationship_changed or derived_changed:
            result.reconciled.append(_item('school activity selections', expected['name'], expected['activities'], 'Canonical activities and derived ordering reconciled.'))
        else:
            result.unchanged.append(_item('school activity selections', expected['name'], expected['activities'], 'Cohort activities already matched.'))
        records[expected['name']] = record
    return records


def _apply_instructors(organization, result):
    records = {}
    for expected in DEMO_SCENARIO['instructors']:
        identity = f"{expected['first']} {expected['last']}"
        record = _unique_owned(
            Instructor.objects.filter(
                organization=organization,
                fname=expected['first'],
                lname=expected['last'],
            ),
            record_type='instructor',
            identity=identity,
        )
        if record is None:
            record = Instructor(
                organization=organization,
                fname=expected['first'],
                lname=expected['last'],
            )
            _validated_save(record)
            result.created.append(_item('instructor', identity, {'name': identity}, 'Missing fictional instructor created.'))
        else:
            result.unchanged.append(_item('instructor', identity, {'name': identity}, 'Instructor already matched.'))
        records[(expected['first'], expected['last'])] = record
    return records


def _apply_instructor_certifications(
    organization,
    instructors,
    certifications,
    result,
):
    for expected in DEMO_SCENARIO['instructors']:
        identity = f"{expected['first']} {expected['last']}"
        instructor = instructors[(expected['first'], expected['last'])]
        expected_records = [
            certifications[name]
            for name in expected['certifications']
        ]
        current_ids = set(
            instructor.certifications.filter(
                organization=organization,
            ).values_list('pk', flat=True)
        )
        expected_ids = {record.pk for record in expected_records}
        if current_ids != expected_ids:
            instructor.certifications.set(expected_records)
            result.reconciled.append(_item('instructor certifications', identity, expected['certifications'], 'Canonical instructor certifications reconciled.'))
        else:
            result.unchanged.append(_item('instructor certifications', identity, expected['certifications'], 'Instructor certifications already matched.'))


def _apply_schedule(organization, schools, result):
    expected = DEMO_SCENARIO['schedule']
    schedule = _unique_owned(
        TheSched.objects.filter(
            organization=organization,
            sched_name=expected['name'],
        ),
        record_type='schedule',
        identity=expected['name'],
    )
    if schedule is None:
        schedule = TheSched(
            organization=organization,
            sched_name=expected['name'],
            sched_data={'version': 1},
        )
        _validated_save(schedule)
        result.created.append(_item('schedule', expected['name'], expected, 'Missing schedule record created without generated output.'))
    else:
        result.unchanged.append(_item('schedule', expected['name'], {'name': expected['name']}, 'Schedule already matched; sched_data was untouched.'))

    expected_schools = [schools[name] for name in expected['schools']]
    current_ids = set(schedule.schools.values_list('pk', flat=True))
    expected_ids = {school.pk for school in expected_schools}
    if current_ids != expected_ids:
        schedule.schools.set(expected_schools)
        result.reconciled.append(_item('schedule schools', expected['name'], expected['schools'], 'Canonical schedule cohorts reconciled.'))
    else:
        result.unchanged.append(_item('schedule schools', expected['name'], expected['schools'], 'Schedule cohorts already matched.'))
    return schedule


def _apply_participation(organization, schedule, instructors, result):
    opt_out = DEMO_SCENARIO['participation_opt_out']
    opted_out = instructors[(opt_out['first'], opt_out['last'])]
    participating_ids = {
        instructors[(item['first'], item['last'])].pk
        for item in DEMO_SCENARIO['participating_instructors']
    }

    redundant = list(InstructorScheduleParticipation.objects.filter(
        organization=organization,
        schedule=schedule,
        instructor_id__in=participating_ids,
    ))
    if redundant:
        InstructorScheduleParticipation.objects.filter(
            pk__in=[record.pk for record in redundant],
            organization=organization,
            schedule=schedule,
        ).delete()
        result.reconciled.append(_item(
            'schedule participation',
            'canonical participating instructors',
            {'state': 'participating by default'},
            'Removed conflicting explicit participation records for normal participants.',
        ))
    else:
        result.unchanged.append(_item(
            'schedule participation',
            'canonical participating instructors',
            {'state': 'participating by default'},
            'Canonical participating instructors already use the default state.',
        ))

    record = _unique_owned(
        InstructorScheduleParticipation.objects.filter(
            organization=organization,
            schedule=schedule,
            instructor=opted_out,
        ),
        record_type='schedule participation',
        identity=str(opted_out),
    )
    if record is None:
        record = InstructorScheduleParticipation(
            organization=organization,
            schedule=schedule,
            instructor=opted_out,
            state=InstructorScheduleParticipation.NOT_PARTICIPATING,
        )
        _validated_save(record)
        result.created.append(_item(
            'schedule participation',
            str(opted_out),
            {'state': InstructorScheduleParticipation.NOT_PARTICIPATING},
            'Canonical opt-out created.',
        ))
    else:
        changed = _reconcile_fields(record, {
            'state': InstructorScheduleParticipation.NOT_PARTICIPATING,
        })
        _record_change(
            result,
            changed,
            'updated',
            'schedule participation',
            str(opted_out),
            {'state': InstructorScheduleParticipation.NOT_PARTICIPATING},
            'Canonical opt-out corrected.' if changed else 'Canonical opt-out already matched.',
        )


def _apply_availability(organization, schedule, instructors, result):
    expected = DEMO_SCENARIO['availability_exception']
    instructor = instructors[(expected['first'], expected['last'])]
    canonical_instructor_ids = [record.pk for record in instructors.values()]
    existing = list(InstructorScheduleAvailability.objects.filter(
        organization=organization,
        schedule=schedule,
        instructor_id__in=canonical_instructor_ids,
    ))
    desired = next(
        (
            record for record in existing
            if record.instructor_id == instructor.pk
            and record.slot_key == expected['slot_key']
        ),
        None,
    )
    extra_ids = [
        record.pk for record in existing
        if desired is None or record.pk != desired.pk
    ]
    if extra_ids:
        InstructorScheduleAvailability.objects.filter(
            pk__in=extra_ids,
            organization=organization,
            schedule=schedule,
        ).delete()

    identity = f"{instructor} — {expected['slot_key']}"
    if desired is None:
        desired = InstructorScheduleAvailability(
            organization=organization,
            schedule=schedule,
            instructor=instructor,
            slot_key=expected['slot_key'],
            state=expected['state'],
        )
        _validated_save(desired)
        result.created.append(_item(
            'schedule availability',
            identity,
            {'state': expected['state']},
            'Canonical availability exception created.',
        ))
    else:
        changed = _reconcile_fields(desired, {'state': expected['state']})
        _record_change(
            result,
            changed,
            'updated',
            'schedule availability',
            identity,
            {'state': expected['state']},
            'Canonical availability exception corrected.' if changed else 'Canonical availability exception already matched.',
        )
    if extra_ids:
        result.reconciled.append(_item(
            'schedule availability',
            'canonical instructor availability set',
            {'exception_count': 1},
            'Removed conflicting canonical-instructor availability exceptions.',
        ))


def _stored_generation_issues(organization, schedule):
    issues = []
    data = schedule.sched_data
    if not isinstance(data, dict):
        return ['sched_data is not a JSON object']
    generated = data.get('generated_schedule')
    if not isinstance(generated, dict) or not generated:
        issues.append('stored generated output is missing')
        return issues
    if data.get('generation_complete') is not True:
        issues.append('generation is incomplete')
    if data.get('manual_moves') != []:
        issues.append('manual activity-move state is dirty')
    if data.get('manual_instructor_overrides') != []:
        issues.append('manual instructor-override history is dirty')
    if data.get('instructor_override_revision') != 0:
        issues.append('instructor override revision is not zero')
    groups = generated.get('ags')
    if not isinstance(groups, list) or len(groups) != DEMO_SCENARIO['expected_group_count']:
        issues.append('canonical group count is missing or incorrect')
    missing_slots = [
        slot for slot in DEMO_SCENARIO['required_schedule_slots']
        if not isinstance(generated.get(slot), list)
    ]
    if missing_slots:
        issues.append(f'missing canonical schedule slots: {", ".join(missing_slots)}')
    return issues


def _validation_failure(identifier, issues):
    result = DemoInspectionResult(identifier)
    result.blockers.extend(
        _item('canonical starting state', identifier, None, issue)
        for issue in issues
    )
    raise DemoSafetyError(
        'Canonical demo starting-state validation failed: '
        + '; '.join(issues),
        result,
    )


def _validate_demo_starting_state(organization, schedule):
    issues = _stored_generation_issues(organization, schedule)
    data = schedule.sched_data if isinstance(schedule.sched_data, dict) else {}
    generated = data.get('generated_schedule')
    if not isinstance(generated, dict):
        _validation_failure(organization.name, issues or ['generated output is malformed'])

    expected_groups = {
        f"{school['name']} {group_index}"
        for school in DEMO_SCENARIO['schools']
        for group_index in range(school['groups'])
    }
    actual_groups = set(generated.get('ags') or ())
    if actual_groups != expected_groups:
        issues.append('generated groups do not exactly match the four canonical groups')

    activity_names = {item['name'] for item in DEMO_SCENARIO['activities']}
    generated_values = {
        value
        for slot in DEMO_SCENARIO['required_schedule_slots']
        for value in generated.get(slot, ())
    }
    real_activities = generated_values - {
        UNASSIGNED_SLOT_VALUE,
        UNAVAILABLE_SLOT_VALUE,
    }
    if not real_activities:
        issues.append('generated output contains no scheduled activities')
    missing_activities = activity_names - real_activities
    if missing_activities:
        issues.append(
            'canonical activities missing from generated output: '
            + ', '.join(sorted(missing_activities))
        )
    foreign_values = real_activities - activity_names
    if foreign_values:
        issues.append(
            'generated output contains noncanonical activities: '
            + ', '.join(sorted(foreign_values))
        )

    try:
        display = schedule.get_display_schedule_result()
        replay = display.get('override_replay_result') or {}
    except Exception as error:
        issues.append(f'operational display construction failed: {error}')
        replay = {}
    if replay.get('replay_conflicts'):
        issues.append('operational replay contains conflicts')
    if replay.get('ignored_overrides'):
        issues.append('operational replay contains ignored overrides')
    if replay.get('holding_area'):
        issues.append('operational holding area is not empty')

    try:
        assignment = run_instructor_assignment(schedule)
    except Exception as error:
        issues.append(f'automatic instructor assignment failed: {error}')
        assignment = None
    if assignment is not None:
        if assignment.get('organization_id') != organization.pk:
            issues.append('assignment result belongs to another organization')
        if any(
            occurrence.get('schedule_id') != schedule.pk
            or occurrence.get('organization_id') != organization.pk
            for occurrence in assignment.get('occurrences', ())
        ):
            issues.append('assignment occurrences are not isolated to the canonical schedule')
        if not assignment.get('coverage', {}).get('complete'):
            issues.append('automatic instructor assignment does not fully staff the demo schedule')
        candidate_ids = {
            instructor.pk
            for instructor in assignment.get('candidate_instructors', ())
        }
        organization_ids = set(Instructor.objects.filter(
            organization=organization,
        ).values_list('pk', flat=True))
        if not candidate_ids <= organization_ids:
            issues.append('assignment candidates include a foreign instructor')

        opt_out = DEMO_SCENARIO['participation_opt_out']
        opted_out = Instructor.objects.get(
            organization=organization,
            fname=opt_out['first'],
            lname=opt_out['last'],
        )
        assigned_ids = {
            item['assigned_instructor'].pk
            for item in assignment.get('assignments', ())
            if item.get('assigned_instructor') is not None
        }
        if opted_out.pk in assigned_ids:
            issues.append('opted-out instructor received an assignment')

        availability = DEMO_SCENARIO['availability_exception']
        unavailable = Instructor.objects.get(
            organization=organization,
            fname=availability['first'],
            lname=availability['last'],
        )
        if any(
            item.get('assigned_instructor') == unavailable
            and availability['slot_key'] in {
                slot['slot_key']
                for slot in item['occurrence'].get('slot_footprint', ())
            }
            for item in assignment.get('assignments', ())
        ):
            issues.append('unavailable instructor was assigned in the blocked slot')

        sensitive = Course.objects.get(
            organization=organization,
            course_name=DEMO_SCENARIO['qualification_sensitive_activity'],
        )
        required_certification_ids = set(
            sensitive.required_certifications.values_list('pk', flat=True)
        )
        for item in assignment.get('assignments', ()):
            if (
                item['occurrence'].get('activity_id') == sensitive.pk
                and item.get('assigned_instructor') is not None
                and not required_certification_ids <= set(
                    item['assigned_instructor'].certifications.values_list(
                        'pk',
                        flat=True,
                    )
                )
            ):
                issues.append('qualification-sensitive activity has an ineligible assignment')

        alternate_spec = DEMO_SCENARIO['eligible_manual_alternate']
        alternate = Instructor.objects.get(
            organization=organization,
            fname=alternate_spec['first'],
            lname=alternate_spec['last'],
        )
        alternate_certifications = {
            alternate.pk: set(
                alternate.certifications.values_list('pk', flat=True)
            ),
        }
        course_requirements = {
            sensitive.pk: required_certification_ids,
        }
        participation_records = tuple(
            InstructorScheduleParticipation.objects.filter(
                organization=organization,
                schedule=schedule,
            ).select_related('instructor')
        )
        availability_records = preload_instructor_availability(
            organization.pk,
            schedule.pk,
            (alternate,),
        )
        alternate_valid = False
        for occurrence in assignment.get('occurrences', ()):
            if occurrence.get('activity_id') != sensitive.pk:
                continue
            qualification = evaluate_occurrence_qualifications(
                occurrence,
                (alternate,),
                alternate_certifications,
                course_requirements,
            )
            constraints = evaluate_occurrence_constraints(
                occurrence,
                qualification['qualified_instructors'],
                (),
                availability_records,
                participation_records,
            )
            if alternate in constraints['eligible_instructors']:
                alternate_valid = True
                break
        if not alternate_valid:
            issues.append('no canonical qualification-sensitive occurrence accepts the manual alternate')

    if issues:
        _validation_failure(organization.name, issues)
    return assignment


def _validate_applied_reference_data(organization):
    """Fail the transaction if final stable relationships cross organization scope."""
    scenario_course_names = [item['name'] for item in DEMO_SCENARIO['activities']]
    if Course.objects.filter(
        organization=organization,
        course_name__in=scenario_course_names,
    ).exclude(primary_locs__organization=organization).exists():
        raise ValidationError('A canonical activity references a foreign location.')
    schedule = TheSched.objects.get(
        organization=organization,
        sched_name=DEMO_SCENARIO['schedule']['name'],
    )
    if schedule.schools.exclude(organization=organization).exists():
        raise ValidationError('The canonical schedule references a foreign cohort.')


def _inspect_schedule_state(result, schedule):
    data = deepcopy(schedule.sched_data)
    is_mapping = isinstance(data, dict)
    generated_present = is_mapping and isinstance(data.get('generated_schedule'), dict) and bool(data['generated_schedule'])
    generation_complete = data.get('generation_complete') if is_mapping else None
    generated_current = {
        'generated_schedule_present': generated_present,
        'generation_complete': generation_complete,
    }
    generated_expected = {'generated_schedule_present': True, 'generation_complete': True}
    target = result.unchanged if generated_current == generated_expected else result.restore
    reason = 'Stored generated output is complete.' if target is result.unchanged else 'Stored generated output is a future required action; this inspection does not generate it.'
    target.append(_item('schedule generated output', schedule.sched_name, generated_expected, reason, generated_current))

    clean_expected = {'manual_moves': [], 'manual_instructor_overrides': [], 'instructor_override_revision': 0}
    clean_current = {
        key: data.get(key) if is_mapping else None
        for key in clean_expected
    }
    target = result.unchanged if clean_current == clean_expected else result.restore
    target.append(_item('schedule operational state', schedule.sched_name, clean_expected, 'Operational starting state is clean.' if target is result.unchanged else 'Mutable operational state requires future restoration.', clean_current))


def _inspect_schedule_staffing_state(result, organization, schedule, instructors):
    participating_ids = {
        instructors[(item['first'], item['last'])].pk
        for item in DEMO_SCENARIO['participating_instructors']
        if (item['first'], item['last']) in instructors
    }
    conflicting_participation = list(
        InstructorScheduleParticipation.objects.filter(
            organization=organization,
            schedule=schedule,
            instructor_id__in=participating_ids,
        ).values('instructor_id', 'state')
    )
    target = result.reconcile if conflicting_participation else result.unchanged
    target.append(_item(
        'schedule participation',
        'canonical participating instructors',
        {'state': 'participating by default'},
        (
            'Conflicting explicit participation state requires reconciliation.'
            if conflicting_participation
            else 'Canonical participating instructors use the normal default state.'
        ),
        conflicting_participation,
    ))

    opt_out = DEMO_SCENARIO['participation_opt_out']
    instructor = instructors.get((opt_out['first'], opt_out['last']))
    participation_records = list(InstructorScheduleParticipation.objects.filter(
        organization=organization,
        schedule=schedule,
        instructor=instructor,
    ).values('state')) if instructor else []
    participation_matches = (
        len(participation_records) == 1
        and participation_records[0]['state']
        == InstructorScheduleParticipation.NOT_PARTICIPATING
    )
    target = result.unchanged if participation_matches else result.reconcile
    target.append(_item('schedule participation', f"{opt_out['first']} {opt_out['last']}", {'state': InstructorScheduleParticipation.NOT_PARTICIPATING}, 'Participation opt-out exists.' if participation_matches else 'Participation opt-out requires reconciliation.', participation_records))

    exception = DEMO_SCENARIO['availability_exception']
    instructor = instructors.get((exception['first'], exception['last']))
    canonical_ids = [record.pk for record in instructors.values()]
    availability_records = list(InstructorScheduleAvailability.objects.filter(
        organization=organization,
        schedule=schedule,
        instructor_id__in=canonical_ids,
    ).values('instructor_id', 'slot_key', 'state'))
    availability_matches = (
        instructor is not None
        and availability_records == [{
            'instructor_id': instructor.pk,
            'slot_key': exception['slot_key'],
            'state': exception['state'],
        }]
    )
    target = result.unchanged if availability_matches else result.reconcile
    target.append(_item('schedule availability', f"{exception['first']} {exception['last']} — {exception['slot_key']}", {'state': exception['state']}, 'Availability exception exists.' if availability_matches else 'Availability exception requires reconciliation.', availability_records))
