"""Guarded one-time copy of the protected default scheduling dataset."""

from copy import deepcopy
from dataclasses import dataclass, field
import re

from django.db import transaction

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


class DefaultDatasetCopyError(Exception):
    """Raised when the source or target cannot be copied safely."""

    def __init__(self, message, *, plan=None):
        super().__init__(message)
        self.plan = plan


@dataclass
class DefaultDatasetCopyPlan:
    source_name: str
    target_name: str
    target_will_be_created: bool
    counts: dict[str, int]
    relationships: dict[str, int]
    schedule_states: tuple[dict[str, str], ...]
    excluded_categories: tuple[str, ...]
    expected_mutations: int
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    already_copied: bool = False


@dataclass(frozen=True)
class DefaultDatasetCopyResult:
    plan: DefaultDatasetCopyPlan
    source: Organization
    target: Organization
    created_target: bool
    copied: bool
    target_has_membership: bool


EXCLUDED_CATEGORIES = (
    'users',
    'memberships',
    'django sessions',
    'demo sessions',
    'capacity coordinators and reservations',
    'provisioning and reset attempts',
    'operation leases',
    'cleanup metadata',
    'admin log records',
)


def _natural_instructor(instructor):
    return (instructor.fname, instructor.lname)


def _organization_graph(organization, *, normalize_schedule_state=False):
    locations = list(
        Locations.objects.filter(organization=organization).order_by(
            'loc_name',
        )
    )
    certifications = list(
        Certification.objects.filter(organization=organization).order_by(
            'name',
        )
    )
    leadership_roles = list(
        LeadershipRole.objects.filter(organization=organization).order_by(
            'name',
        )
    )
    courses = list(
        Course.objects.filter(organization=organization)
        .prefetch_related('primary_locs', 'required_certifications')
        .order_by('course_name')
    )
    schools = list(
        Schools._default_manager.filter(organization=organization)
        .prefetch_related('subject')
        .order_by('school_name')
    )
    schedules = list(
        TheSched.objects.filter(organization=organization)
        .prefetch_related('schools')
        .order_by('sched_name')
    )
    instructors = list(
        Instructor.objects.filter(organization=organization)
        .prefetch_related('certifications', 'leadership_roles')
        .order_by('fname', 'lname', 'pk')
    )
    participation = list(
        InstructorScheduleParticipation.objects.filter(
            organization=organization,
        )
        .select_related('instructor', 'schedule')
        .order_by(
            'schedule__sched_name',
            'instructor__fname',
            'instructor__lname',
        )
    )
    availability = list(
        InstructorScheduleAvailability.objects.filter(
            organization=organization,
        )
        .select_related('instructor', 'schedule')
        .order_by(
            'schedule__sched_name',
            'instructor__fname',
            'instructor__lname',
            'slot_key',
        )
    )

    records = {
        'locations': tuple(
            (
                item.loc_name,
                item.loc_short,
                item.description,
                item.availible,
            )
            for item in locations
        ),
        'certifications': tuple(item.name for item in certifications),
        'leadership_roles': tuple(item.name for item in leadership_roles),
        'activities': tuple(
            (
                item.course_name,
                item.abriviation,
                item.course_len,
                item.required_instructor_count,
            )
            for item in courses
        ),
        'schools': tuple(
            (
                item.school_name,
                item.arrive,
                item.depart,
                item.total_students,
                item.ag_num,
                item.attending_year,
                item.sorted_subject_lst,
            )
            for item in schools
        ),
        'schedules': tuple(
            (
                item.sched_name,
                None if normalize_schedule_state else deepcopy(item.sched_data),
                item.timestamp_og,
            )
            for item in schedules
        ),
        'instructors': tuple(
            (
                item.fname,
                item.lname,
                item.ropes_lead,
                item.school_lead,
                item.cpr,
                item.firstaid,
            )
            for item in instructors
        ),
        'participation': tuple(
            (
                _natural_instructor(item.instructor),
                item.schedule.sched_name,
                item.state,
            )
            for item in participation
        ),
        'availability': tuple(
            (
                _natural_instructor(item.instructor),
                item.schedule.sched_name,
                item.slot_key,
                item.state,
            )
            for item in availability
        ),
    }
    relationships = {
        'activity_locations': tuple(
            (
                item.course_name,
                tuple(sorted(item.primary_locs.values_list('loc_name', flat=True))),
            )
            for item in courses
        ),
        'activity_certification_requirements': tuple(
            (
                item.course_name,
                tuple(
                    sorted(
                        item.required_certifications.values_list(
                            'name',
                            flat=True,
                        )
                    )
                ),
            )
            for item in courses
        ),
        'school_activities': tuple(
            (
                item.school_name,
                tuple(sorted(item.subject.values_list('course_name', flat=True))),
            )
            for item in schools
        ),
        'schedule_schools': tuple(
            (
                item.sched_name,
                tuple(sorted(item.schools.values_list('school_name', flat=True))),
            )
            for item in schedules
        ),
        'instructor_certifications': tuple(
            (
                _natural_instructor(item),
                tuple(sorted(item.certifications.values_list('name', flat=True))),
            )
            for item in instructors
        ),
        'instructor_leadership_roles': tuple(
            (
                _natural_instructor(item),
                tuple(
                    sorted(item.leadership_roles.values_list('name', flat=True))
                ),
            )
            for item in instructors
        ),
    }
    return {'records': records, 'relationships': relationships}


def _counts(graph):
    return {
        category: len(items)
        for category, items in graph['records'].items()
    }


def _relationship_counts(graph):
    counts = {}
    for category, groups in graph['relationships'].items():
        counts[category] = sum(len(values) for _identity, values in groups)
    return counts


def _source_organizations(*, lock=False):
    queryset = Organization.objects.filter(name=DEFAULT_ORGANIZATION_NAME)
    if lock:
        queryset = queryset.select_for_update()
    return list(queryset[:2])


def _target_organizations(target_name, *, lock=False):
    queryset = Organization.objects.filter(name=target_name)
    if lock:
        queryset = queryset.select_for_update()
    return list(queryset[:2])


def _foreign_relationship_blockers(source):
    checks = (
        (
            'activity-location',
            Course.primary_locs.through.objects.filter(
                course__organization=source,
            ).exclude(locations__organization=source),
        ),
        (
            'school-activity',
            Schools.subject.through.objects.filter(
                schools__organization=source,
            ).exclude(course__organization=source),
        ),
        (
            'schedule-school',
            TheSched.schools.through.objects.filter(
                thesched__organization=source,
            ).exclude(schools__organization=source),
        ),
        (
            'activity-certification',
            ActivityCertificationRequirement.objects.filter(
                course__organization=source,
            ).exclude(certification__organization=source),
        ),
        (
            'instructor-certification',
            InstructorCertification.objects.filter(
                instructor__organization=source,
            ).exclude(certification__organization=source),
        ),
        (
            'instructor-leadership',
            InstructorLeadershipRole.objects.filter(
                instructor__organization=source,
            ).exclude(leadership_role__organization=source),
        ),
        (
            'participation',
            InstructorScheduleParticipation.objects.filter(
                organization=source,
            ).exclude(
                instructor__organization=source,
                schedule__organization=source,
            ),
        ),
        (
            'availability',
            InstructorScheduleAvailability.objects.filter(
                organization=source,
            ).exclude(
                instructor__organization=source,
                schedule__organization=source,
            ),
        ),
    )
    return [
        f'Foreign {label} relationships block the copy.'
        for label, queryset in checks
        if queryset.exists()
    ]


def _duplicate_identity_blockers(source):
    identities = list(
        Instructor.objects.filter(organization=source).values_list(
            'fname',
            'lname',
        )
    )
    duplicates = sorted(
        identity for identity in set(identities)
        if identities.count(identity) > 1
    )
    if not duplicates:
        return []
    return [
        'Duplicate instructor natural identities block deterministic copying.'
    ]


def _privacy_warnings(source):
    descriptions = Locations.objects.filter(
        organization=source,
    ).exclude(description__isnull=True).exclude(description='')
    warning_count = descriptions.count()
    warnings = []
    if warning_count:
        warnings.append(
            f'{warning_count} nonempty location descriptions will be copied '
            'without printing their values; review them before later export.'
        )
    suspicious = re.compile(
        r'(?:[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|password|api[_ -]?token)',
        re.IGNORECASE,
    )
    if any(suspicious.search(value or '') for value in descriptions.values_list(
        'description',
        flat=True,
    )):
        warnings.append(
            'One or more location descriptions contain credential- or '
            'contact-like text; review before later scenario extraction.'
        )
    return warnings


def _schedule_states(source):
    states = []
    for schedule in TheSched.objects.filter(
        organization=source,
    ).order_by('sched_name'):
        stored = schedule.get_stored_generation_result()
        if not stored['has_generated_schedule']:
            source_state = 'not-generated'
        elif stored['generation_complete']:
            source_state = 'complete'
        else:
            source_state = 'infeasible-or-incomplete'
        states.append({
            'schedule': schedule.sched_name,
            'source_state': source_state,
            'target_state': 'generated and operational state cleared',
        })
    return tuple(states)


def plan_default_dataset_copy(target_name):
    target_name = (target_name or '').strip()
    if not target_name:
        raise DefaultDatasetCopyError('Target organization name is required.')
    if target_name == DEFAULT_ORGANIZATION_NAME:
        raise DefaultDatasetCopyError(
            'Default Organization cannot be the copy target.'
        )

    sources = _source_organizations()
    if len(sources) != 1:
        raise DefaultDatasetCopyError(
            'Exactly one protected Default Organization is required.'
        )
    source = sources[0]
    targets = _target_organizations(target_name)
    if len(targets) > 1:
        raise DefaultDatasetCopyError(
            'Target organization name is ambiguous.'
        )
    target = targets[0] if targets else None
    if target and target.purpose != Organization.Purpose.CUSTOMER:
        raise DefaultDatasetCopyError(
            'Target must be a permanent customer organization.'
        )

    source_graph = _organization_graph(source, normalize_schedule_state=True)
    blockers = (
        _foreign_relationship_blockers(source)
        + _duplicate_identity_blockers(source)
    )
    already_copied = False
    if target:
        target_graph = _organization_graph(
            target,
            normalize_schedule_state=False,
        )
        target_count = sum(_counts(target_graph).values())
        target_relationship_count = sum(
            _relationship_counts(target_graph).values()
        )
        if target_count or target_relationship_count:
            if target_graph == source_graph:
                already_copied = True
            else:
                blockers.append(
                    'Target contains operational records that do not exactly '
                    'match the protected normalized copy.'
                )

    counts = _counts(source_graph)
    relationships = _relationship_counts(source_graph)
    expected_mutations = (
        (0 if target else 1)
        + sum(counts.values())
        + sum(relationships.values())
    )
    plan = DefaultDatasetCopyPlan(
        source_name=source.name,
        target_name=target_name,
        target_will_be_created=target is None,
        counts=counts,
        relationships=relationships,
        schedule_states=_schedule_states(source),
        excluded_categories=EXCLUDED_CATEGORIES,
        expected_mutations=0 if already_copied else expected_mutations,
        blockers=blockers,
        warnings=_privacy_warnings(source),
        already_copied=already_copied,
    )
    if blockers:
        raise DefaultDatasetCopyError(
            'Default dataset copy plan is blocked.',
            plan=plan,
        )
    return plan


def _copy_graph(source, target):
    location_map = {}
    for source_item in Locations.objects.filter(
        organization=source,
    ).order_by('loc_name'):
        location_map[source_item.pk] = Locations.objects.create(
            organization=target,
            loc_name=source_item.loc_name,
            loc_short=source_item.loc_short,
            description=source_item.description,
            availible=source_item.availible,
        )

    certification_map = {}
    for source_item in Certification.objects.filter(
        organization=source,
    ).order_by('name'):
        certification_map[source_item.pk] = Certification.objects.create(
            organization=target,
            name=source_item.name,
        )

    leadership_map = {}
    for source_item in LeadershipRole.objects.filter(
        organization=source,
    ).order_by('name'):
        leadership_map[source_item.pk] = LeadershipRole.objects.create(
            organization=target,
            name=source_item.name,
        )

    course_map = {}
    source_courses = Course.objects.filter(
        organization=source,
    ).prefetch_related('primary_locs', 'required_certifications').order_by(
        'course_name',
    )
    for source_item in source_courses:
        target_item = Course.objects.create(
            organization=target,
            course_name=source_item.course_name,
            abriviation=source_item.abriviation,
            course_len=source_item.course_len,
            required_instructor_count=source_item.required_instructor_count,
        )
        target_item.primary_locs.set(
            location_map[item.pk]
            for item in source_item.primary_locs.all()
        )
        for requirement in source_item.certification_requirements.all():
            ActivityCertificationRequirement.objects.create(
                course=target_item,
                certification=certification_map[requirement.certification_id],
            )
        course_map[source_item.pk] = target_item

    school_map = {}
    source_schools = Schools._default_manager.filter(
        organization=source,
    ).prefetch_related('subject').order_by('school_name')
    for source_item in source_schools:
        target_item = Schools._default_manager.create(
            organization=target,
            school_name=source_item.school_name,
            arrive=source_item.arrive,
            depart=source_item.depart,
            total_students=source_item.total_students,
            ag_num=source_item.ag_num,
            attending_year=source_item.attending_year,
            sorted_subject_lst=source_item.sorted_subject_lst,
        )
        target_item.subject.set(
            course_map[item.pk] for item in source_item.subject.all()
        )
        Schools._default_manager.filter(pk=target_item.pk).update(
            sorted_subject_lst=source_item.sorted_subject_lst,
        )
        target_item.refresh_from_db()
        school_map[source_item.pk] = target_item

    schedule_map = {}
    source_schedules = TheSched.objects.filter(
        organization=source,
    ).prefetch_related('schools').order_by('sched_name')
    for source_item in source_schedules:
        target_item = TheSched.objects.create(
            organization=target,
            sched_name=source_item.sched_name,
            sched_data=None,
        )
        target_item.schools.set(
            school_map[item.pk] for item in source_item.schools.all()
        )
        TheSched.objects.filter(pk=target_item.pk).update(
            timestamp_og=source_item.timestamp_og,
        )
        target_item.refresh_from_db()
        target_item.get_display_schedule_result()
        schedule_map[source_item.pk] = target_item

    instructor_map = {}
    source_instructors = Instructor.objects.filter(
        organization=source,
    ).prefetch_related('certifications', 'leadership_roles').order_by(
        'fname',
        'lname',
        'pk',
    )
    for source_item in source_instructors:
        target_item = Instructor.objects.create(
            organization=target,
            fname=source_item.fname,
            lname=source_item.lname,
            ropes_lead=source_item.ropes_lead,
            school_lead=source_item.school_lead,
            cpr=source_item.cpr,
            firstaid=source_item.firstaid,
        )
        for relationship in source_item.certification_relationships.all():
            InstructorCertification.objects.create(
                instructor=target_item,
                certification=certification_map[
                    relationship.certification_id
                ],
            )
        for relationship in source_item.leadership_role_relationships.all():
            InstructorLeadershipRole.objects.create(
                instructor=target_item,
                leadership_role=leadership_map[
                    relationship.leadership_role_id
                ],
            )
        instructor_map[source_item.pk] = target_item

    for source_item in InstructorScheduleParticipation.objects.filter(
        organization=source,
    ).order_by('pk'):
        InstructorScheduleParticipation.objects.create(
            organization=target,
            instructor=instructor_map[source_item.instructor_id],
            schedule=schedule_map[source_item.schedule_id],
            state=source_item.state,
        )

    for source_item in InstructorScheduleAvailability.objects.filter(
        organization=source,
    ).order_by('pk'):
        InstructorScheduleAvailability.objects.create(
            organization=target,
            instructor=instructor_map[source_item.instructor_id],
            schedule=schedule_map[source_item.schedule_id],
            slot_key=source_item.slot_key,
            state=source_item.state,
        )


def copy_default_dataset_to_organization(target_name, *, confirmed=False):
    plan = plan_default_dataset_copy(target_name)
    if not confirmed:
        return plan
    if plan.already_copied:
        target = Organization.objects.get(name=plan.target_name)
        return DefaultDatasetCopyResult(
            plan=plan,
            source=Organization.objects.get(name=DEFAULT_ORGANIZATION_NAME),
            target=target,
            created_target=False,
            copied=False,
            target_has_membership=OrganizationMembership.objects.filter(
                organization=target,
            ).exists(),
        )

    with transaction.atomic():
        sources = _source_organizations(lock=True)
        if len(sources) != 1:
            raise DefaultDatasetCopyError(
                'Exactly one protected Default Organization is required.'
            )
        source = sources[0]
        if source.name != DEFAULT_ORGANIZATION_NAME:
            raise DefaultDatasetCopyError(
                'Locked source identity no longer matches Default Organization.'
            )
        source_purpose = source.purpose
        source_graph = _organization_graph(
            source,
            normalize_schedule_state=False,
        )

        targets = _target_organizations(plan.target_name, lock=True)
        if len(targets) > 1:
            raise DefaultDatasetCopyError(
                'Target organization name became ambiguous.'
            )
        if targets:
            target = targets[0]
            created_target = False
            if target.purpose != Organization.Purpose.CUSTOMER:
                raise DefaultDatasetCopyError(
                    'Target is no longer a permanent customer organization.'
                )
        else:
            target = Organization.objects.create(
                name=plan.target_name,
                purpose=Organization.Purpose.CUSTOMER,
            )
            created_target = True

        current_target = _organization_graph(target)
        if sum(_counts(current_target).values()) or sum(
            _relationship_counts(current_target).values()
        ):
            raise DefaultDatasetCopyError(
                'Target became populated before the copy could begin.'
            )

        _copy_graph(source, target)

        if (
            source.purpose != source_purpose
            or _organization_graph(
                source,
                normalize_schedule_state=False,
            ) != source_graph
        ):
            raise DefaultDatasetCopyError(
                'Source immutability verification failed; copy rolled back.'
            )
        target_graph = _organization_graph(target)
        normalized_source = _organization_graph(
            source,
            normalize_schedule_state=True,
        )
        if target_graph != normalized_source:
            raise DefaultDatasetCopyError(
                'Target graph verification failed; copy rolled back.'
            )
        if (
            target.name == DEFAULT_ORGANIZATION_NAME
            or target.purpose != Organization.Purpose.CUSTOMER
            or DemoSession.objects.filter(organization=target).exists()
        ):
            raise DefaultDatasetCopyError(
                'Target ownership verification failed; copy rolled back.'
            )

        return DefaultDatasetCopyResult(
            plan=plan,
            source=source,
            target=target,
            created_target=created_target,
            copied=True,
            target_has_membership=OrganizationMembership.objects.filter(
                organization=target,
            ).exists(),
        )
