"""Transactional in-place switching for one valid temporary demo owner."""

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from members.models import DemoSession, Organization, OrganizationMembership

from .demo_scaffolding import (
    DEMO_SCENARIO,
    PREPARED_DEMO_SCENARIO_VERSION,
    build_demo_scenario_for_organization,
)
from .demo_session_provisioning import _verify_temporary_ownership_unit
from .models import (
    ActivityCertificationRequirement, Certification, Course, Instructor,
    InstructorCertification, InstructorLeadershipRole,
    InstructorScheduleAvailability, InstructorScheduleParticipation,
    LeadershipRole, Locations, Schools, TheSched,
)
from .prepared_scenarios import build_working_scenario, get_prepared_scenario
from .working_demo_scenario import (
    WORKING_DEMO_SCENARIO,
    WORKING_DEMO_SCENARIO_VERSION,
)


class DemoModeSwitchError(Exception):
    """Raised when in-place switching cannot be completed safely."""


@dataclass(frozen=True)
class DemoModeSwitchResult:
    demo_session: DemoSession
    user: object
    organization: Organization
    membership: OrganizationMembership
    schedule: TheSched | None
    mode: str
    scenario_version: str


def _clear_operational_graph(organization):
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


def _lock_valid_ownership(demo_session, now):
    session = (
        DemoSession.objects.select_for_update()
        .select_related('user', 'organization')
        .get(pk=demo_session.pk)
    )
    memberships = list(
        OrganizationMembership.objects.select_for_update().filter(
            user=session.user,
            organization=session.organization,
        )[:2]
    )
    if (
        len(memberships) != 1
        or session.status != DemoSession.Status.ACTIVE
        or session.expires_at <= now
        or session.organization.purpose != Organization.Purpose.TEMPORARY_DEMO
        or session.user.is_staff
        or session.user.is_superuser
        or DemoSession.objects.filter(user=session.user).count() != 1
        or DemoSession.objects.filter(organization=session.organization).count() != 1
    ):
        raise DemoModeSwitchError('Temporary demo ownership is not valid for switching.')
    return session, memberships[0]


def switch_demo_mode(*, demo_session, mode, scenario_version=''):
    """Replace one temporary organization's contents without replacing ownership."""
    if mode == DemoSession.Mode.CLEAN:
        scenario_version = ''
    elif mode == DemoSession.Mode.PREPARED:
        try:
            get_prepared_scenario(scenario_version)
        except ValidationError as error:
            raise DemoModeSwitchError('Prepared scenario is not registered.') from error
    else:
        raise DemoModeSwitchError('Unsupported demo mode.')

    now = timezone.now()
    try:
        with transaction.atomic():
            session, membership = _lock_valid_ownership(demo_session, now)
            organization = Organization.objects.select_for_update().get(
                pk=session.organization_id
            )
            _clear_operational_graph(organization)

            session.mode = mode
            session.scenario_version = scenario_version
            session.status = (
                DemoSession.Status.ACTIVE
                if mode == DemoSession.Mode.CLEAN
                else DemoSession.Status.PROVISIONING
            )
            session.full_clean()
            session.save(update_fields=('mode', 'scenario_version', 'status'))

            schedule = None
            if scenario_version == PREPARED_DEMO_SCENARIO_VERSION:
                _applied, schedule, _assignment = build_demo_scenario_for_organization(
                    organization,
                    session,
                    scenario=DEMO_SCENARIO,
                    expected_schedule_name=DEMO_SCENARIO['schedule']['name'],
                    ownership_context='prepared_visitor',
                    allow_stable_creation=True,
                    establish_mutable_baseline=True,
                    require_generation=True,
                    require_full_validation=True,
                )
            elif scenario_version == WORKING_DEMO_SCENARIO_VERSION:
                schedules, _outcomes = build_working_scenario(organization)
                schedule = schedules[WORKING_DEMO_SCENARIO['schedules'][0][0]]

            if mode == DemoSession.Mode.PREPARED:
                session.status = DemoSession.Status.ACTIVE
                session.full_clean()
                session.save(update_fields=('status',))

            _verify_temporary_ownership_unit(
                session,
                session.user,
                organization,
                membership,
                expected_mode=mode,
                expected_status=DemoSession.Status.ACTIVE,
                require_empty=mode == DemoSession.Mode.CLEAN,
            )
            return DemoModeSwitchResult(
                demo_session=session,
                user=session.user,
                organization=organization,
                membership=membership,
                schedule=schedule,
                mode=mode,
                scenario_version=scenario_version,
            )
    except DemoModeSwitchError:
        raise
    except Exception as error:
        raise DemoModeSwitchError(
            f'Demo mode switch failed during {error.__class__.__name__}; '
            'all changes were rolled back.'
        ) from error
