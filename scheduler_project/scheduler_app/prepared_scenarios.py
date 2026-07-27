"""Registry and generic construction for repository-owned prepared scenarios."""

from django.core.exceptions import ValidationError

from .demo_scaffolding import DEMO_SCENARIO, PREPARED_DEMO_SCENARIO_VERSION
from .models import (
    Course, Instructor, InstructorScheduleAvailability,
    InstructorScheduleParticipation, Locations, Schools, TheSched,
)
from .working_demo_scenario import (
    WORKING_DEMO_SCENARIO, WORKING_DEMO_SCENARIO_VERSION,
)

PREPARED_SCENARIOS = {
    PREPARED_DEMO_SCENARIO_VERSION: DEMO_SCENARIO,
    WORKING_DEMO_SCENARIO_VERSION: WORKING_DEMO_SCENARIO,
}


def get_prepared_scenario(version):
    try:
        return PREPARED_SCENARIOS[version]
    except KeyError as error:
        raise ValidationError('Unknown prepared demo scenario version.') from error


def build_working_scenario(organization):
    """Create working-v1 using only committed logical identifiers."""
    locations = {}
    for name, short, available in WORKING_DEMO_SCENARIO['locations']:
        locations[name] = Locations.objects.create(
            organization=organization, loc_name=name, loc_short=short,
            description='', availible=available,
        )
    courses = {}
    for name, short, length, location_names in WORKING_DEMO_SCENARIO['activities']:
        course = Course.objects.create(
            organization=organization, course_name=name, abriviation=short,
            course_len=length, required_instructor_count=1,
        )
        course.primary_locs.set(locations[item] for item in location_names)
        courses[name] = course
    schools = {}
    for name, arrive, depart, students, groups, attending_year, activity_names in WORKING_DEMO_SCENARIO['schools']:
        school = Schools._default_manager.create(
            organization=organization, school_name=name, arrive=arrive,
            depart=depart, total_students=students, ag_num=groups,
            attending_year=attending_year,
        )
        school.subject.set(courses[item] for item in activity_names)
        school.save()
        schools[name] = school
    schedules = {}
    for name, school_names, _expected in WORKING_DEMO_SCENARIO['schedules']:
        schedule = TheSched.objects.create(
            organization=organization, sched_name=name, sched_data=None,
        )
        schedule.schools.set(schools[item] for item in school_names)
        schedules[name] = schedule
    instructors = {
        identity: Instructor.objects.create(
            organization=organization, fname=identity[0], lname=identity[1],
        )
        for identity in WORKING_DEMO_SCENARIO['instructors']
    }
    for identity, schedule_name, state in WORKING_DEMO_SCENARIO['participation']:
        InstructorScheduleParticipation.objects.create(
            organization=organization, instructor=instructors[identity],
            schedule=schedules[schedule_name], state=state,
        )
    for identity, schedule_name, slot_key, state in WORKING_DEMO_SCENARIO['availability']:
        InstructorScheduleAvailability.objects.create(
            organization=organization, instructor=instructors[identity],
            schedule=schedules[schedule_name], slot_key=slot_key, state=state,
        )
    outcomes = {}
    for name, _school_names, expected in WORKING_DEMO_SCENARIO['schedules']:
        schedule = schedules[name]
        schedule.generate_and_store_schedule()
        schedule.refresh_from_db()
        complete = schedule.get_stored_generation_result()['generation_complete']
        if complete != (expected == 'complete'):
            raise ValidationError(
                f'Prepared schedule {name!r} did not match its expected outcome.'
            )
        outcomes[name] = complete
    return schedules, outcomes
