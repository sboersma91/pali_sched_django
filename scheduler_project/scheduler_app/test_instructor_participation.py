from copy import deepcopy
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from members.models import Organization

from .instructor_assignment import (
    DAILY_OFF_RESERVATION_REQUIRED,
    DAILY_OFF_SATISFIED_BY_AVAILABILITY,
    evaluate_resolved_instructor_availability,
    normalize_daily_off_requirements,
    plan_instructor_assignments_with_daily_off,
    run_instructor_assignment,
)
from .instructor_availability import (
    build_participating_instructors,
    preload_instructor_schedule_participation,
    resolve_schedule_participating_instructors,
)
from .models import (
    ActivityCertificationRequirement,
    Certification,
    Course,
    Instructor,
    InstructorCertification,
    InstructorScheduleAvailability,
    InstructorScheduleParticipation,
    TheSched,
)
from .schedule_blocks import (
    DAILY_OFF_ELIGIBLE_SLOT_KEYS_BY_DAY,
    DAILY_OFF_REQUIRED_DAY_KEYS,
)


class ParticipationTestMixin:
    def create_schedule(self, name='Participation Week', organization=None, generated=None):
        organization = organization or self.organization
        sched_data = {}
        if generated is not None:
            sched_data = {
                'version': 1,
                'generated_schedule': generated,
                'manual_moves': [],
                'generation_diagnostics': [],
                'generation_runtime_diagnostics': [],
                'generation_complete': True,
            }
        return TheSched.objects.create(
            organization=organization,
            sched_name=name,
            sched_data=sched_data,
        )

    def create_instructor(self, first='Avery', last='Instructor', organization=None):
        return Instructor.objects.create(
            organization=organization or self.organization,
            fname=first,
            lname=last,
            ropes_lead=False,
            school_lead=False,
            cpr=True,
            firstaid='yes',
        )

    def participation(self, instructor=None, schedule=None, state=None, organization=None):
        return InstructorScheduleParticipation.objects.create(
            organization=organization or self.organization,
            instructor=instructor or self.instructor,
            schedule=schedule or self.schedule,
            state=state or InstructorScheduleParticipation.PARTICIPATING,
        )

    def occurrence(self, *slot_keys, schedule=None, organization=None):
        schedule = schedule or self.schedule
        organization = organization or self.organization
        return {
            'schedule_id': schedule.pk,
            'organization_id': organization.pk,
            'activity_id': 1,
            'activity_display_name': 'Activity',
            'group_index': 0,
            'group_label': 'School 0',
            'occurrence_id': f'occurrence:0:{slot_keys[0]}',
            'slot_footprint': [
                {
                    'block_id': f'0:{slot_key}',
                    'slot_key': slot_key,
                    'slot_label': slot_key.upper(),
                    'position': position,
                }
                for position, slot_key in enumerate(slot_keys, start=1)
            ],
        }


class InstructorScheduleParticipationModelTests(ParticipationTestMixin, TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Participation Model Org')
        self.other_organization = Organization.objects.create(name='Other Participation Org')
        self.schedule = self.create_schedule()
        self.other_schedule = self.create_schedule('Other Org Week', self.other_organization)
        self.instructor = self.create_instructor()
        self.other_instructor = self.create_instructor(
            'Foreign', organization=self.other_organization
        )

    def test_valid_participation_record(self):
        record = self.participation()

        self.assertEqual(record.organization, self.organization)
        self.assertEqual(record.instructor, self.instructor)
        self.assertEqual(record.schedule, self.schedule)
        self.assertEqual(record.state, InstructorScheduleParticipation.PARTICIPATING)

    def test_both_states_validate(self):
        for index, state in enumerate((
            InstructorScheduleParticipation.PARTICIPATING,
            InstructorScheduleParticipation.NOT_PARTICIPATING,
        )):
            instructor = self.create_instructor(f'Instructor {index}')
            self.participation(instructor=instructor, state=state)

        self.assertEqual(InstructorScheduleParticipation.objects.count(), 2)

    def test_invalid_state_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.participation(state='partial')

    def test_unique_instructor_and_schedule(self):
        self.participation()
        with self.assertRaises(IntegrityError):
            InstructorScheduleParticipation.objects.bulk_create([
                InstructorScheduleParticipation(
                    organization=self.organization,
                    instructor=self.instructor,
                    schedule=self.schedule,
                    state=InstructorScheduleParticipation.NOT_PARTICIPATING,
                )
            ])

    def test_instructor_organization_mismatch_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.participation(instructor=self.other_instructor)

    def test_schedule_organization_mismatch_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.participation(schedule=self.other_schedule)

    def test_deleting_instructor_cascades(self):
        self.participation()
        self.instructor.delete()
        self.assertFalse(InstructorScheduleParticipation.objects.exists())

    def test_deleting_schedule_cascades(self):
        self.participation()
        self.schedule.delete()
        self.assertFalse(InstructorScheduleParticipation.objects.exists())

    def test_participation_protects_organization(self):
        self.participation()
        with self.assertRaises(ProtectedError):
            self.organization.delete()


class ParticipatingCandidateServiceTests(ParticipationTestMixin, TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Candidate Pool Org')
        self.other_organization = Organization.objects.create(name='Foreign Candidate Org')
        self.schedule = self.create_schedule()
        self.instructor = self.create_instructor('Zoe', 'Alpha')
        self.second = self.create_instructor('Avery', 'Beta')
        self.undecided = self.create_instructor('Undecided', 'Zulu')
        self.foreign = self.create_instructor('Foreign', organization=self.other_organization)
        self.foreign_schedule = self.create_schedule(
            'Foreign Candidate Week', self.other_organization
        )

    def test_pool_includes_only_explicit_participants_in_deterministic_order(self):
        first_record = self.participation(instructor=self.instructor)
        second_record = self.participation(instructor=self.second)
        excluded = self.create_instructor('Excluded', 'Able')
        excluded_record = self.participation(
            instructor=excluded,
            state=InstructorScheduleParticipation.NOT_PARTICIPATING,
        )

        with self.assertNumQueries(0):
            candidates = build_participating_instructors(
                self.organization.pk,
                self.schedule.pk,
                [first_record, excluded_record, second_record],
            )

        self.assertEqual(candidates, (self.instructor, self.second))
        self.assertNotIn(self.undecided, candidates)

    def test_opt_out_pool_includes_missing_and_explicit_participants(self):
        explicit = self.participation(instructor=self.instructor)
        opted_out = self.participation(
            instructor=self.second,
            state=InstructorScheduleParticipation.NOT_PARTICIPATING,
        )

        with self.assertNumQueries(0):
            candidates = resolve_schedule_participating_instructors(
                self.organization.pk,
                self.schedule.pk,
                [self.undecided, self.second, self.instructor, self.foreign],
                [opted_out, explicit],
            )

        self.assertEqual(candidates, (self.instructor, self.undecided))

    def test_preload_excludes_foreign_organization_and_is_bounded(self):
        expected = self.participation()
        InstructorScheduleParticipation.objects.create(
            organization=self.other_organization,
            instructor=self.foreign,
            schedule=self.foreign_schedule,
            state=InstructorScheduleParticipation.PARTICIPATING,
        )

        with self.assertNumQueries(1):
            records = preload_instructor_schedule_participation(
                self.organization, self.schedule
            )

        self.assertEqual(records, (expected,))

    def test_duplicate_relevant_context_fails_safely(self):
        record = self.participation()
        with self.assertRaises(ValidationError):
            build_participating_instructors(
                self.organization.pk, self.schedule.pk, [record, record]
            )

    def test_invalid_relevant_context_fails_safely(self):
        record = InstructorScheduleParticipation(
            organization=self.organization,
            instructor=self.instructor,
            schedule=self.schedule,
            state='partial',
        )
        with self.assertRaises(ValidationError):
            build_participating_instructors(
                self.organization.pk, self.schedule.pk, [record]
            )

    def test_foreign_context_is_ignored(self):
        foreign_record = InstructorScheduleParticipation(
            organization=self.other_organization,
            instructor=self.foreign,
            schedule=self.foreign_schedule,
            state=InstructorScheduleParticipation.PARTICIPATING,
        )
        self.assertEqual(
            build_participating_instructors(
                self.organization.pk, self.schedule.pk, [foreign_record]
            ),
            (),
        )


class ResolvedInstructorAvailabilityTests(ParticipationTestMixin, TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Resolved Availability Org')
        self.other_organization = Organization.objects.create(name='Foreign Resolved Org')
        self.schedule = self.create_schedule()
        self.other_schedule = self.create_schedule('Other Resolved Week')
        self.foreign_schedule = self.create_schedule(
            'Foreign Resolved Week', self.other_organization
        )
        self.instructor = self.create_instructor()
        self.foreign = self.create_instructor('Foreign', organization=self.other_organization)
        self.record = self.participation()

    def evaluate(self, occurrence=None, participation=None, availability=None):
        return evaluate_resolved_instructor_availability(
            occurrence or self.occurrence('mon_pm1'),
            self.instructor,
            [self.record] if participation is None else participation,
            [] if availability is None else availability,
        )

    def availability(self, slot_key, state='available', schedule=None):
        return InstructorScheduleAvailability.objects.create(
            organization=self.organization,
            instructor=self.instructor,
            schedule=schedule or self.schedule,
            slot_key=slot_key,
            state=state,
        )

    def test_participant_without_slot_rows_passes_single_and_multi_slot(self):
        with self.assertNumQueries(0):
            single = self.evaluate()
            multi = self.evaluate(self.occurrence('mon_pm1', 'mon_pm2'))

        self.assertTrue(single['passes'])
        self.assertTrue(multi['passes'])

    def test_explicit_unavailable_blocks_matching_slot_and_multi_slot(self):
        unavailable = self.availability('mon_pm2', 'unavailable')

        single = self.evaluate(
            self.occurrence('mon_pm2'), availability=[unavailable]
        )
        multi = self.evaluate(
            self.occurrence('mon_pm1', 'mon_pm2'), availability=[unavailable]
        )

        self.assertEqual(single['code'], 'explicitly_unavailable')
        self.assertEqual(multi['details']['failed_slots'][0]['slot_key'], 'mon_pm2')

    def test_explicit_available_remains_valid(self):
        self.assertTrue(self.evaluate(
            availability=[self.availability('mon_pm1')]
        )['passes'])

    def test_other_schedule_and_organization_rows_do_not_override_baseline(self):
        other_schedule_record = self.availability(
            'mon_pm1', 'unavailable', schedule=self.other_schedule
        )
        foreign_record = InstructorScheduleAvailability(
            organization=self.other_organization,
            instructor=self.foreign,
            schedule=self.foreign_schedule,
            slot_key='mon_pm1',
            state='unavailable',
        )

        self.assertTrue(self.evaluate(
            availability=[other_schedule_record, foreign_record]
        )['passes'])

    def test_nonparticipant_is_blocked_and_missing_participation_receives_baseline(self):
        self.record.state = InstructorScheduleParticipation.NOT_PARTICIPATING
        nonparticipant = self.evaluate()
        undecided = self.evaluate(participation=[])

        self.assertEqual(nonparticipant['code'], 'not_participating')
        self.assertTrue(undecided['passes'])

    def test_duplicate_and_invalid_detailed_records_remain_blocking(self):
        stored = self.availability('mon_pm1')
        duplicate = InstructorScheduleAvailability(
            organization=self.organization,
            instructor=self.instructor,
            schedule=self.schedule,
            slot_key='mon_pm1',
            state='available',
        )
        invalid = InstructorScheduleAvailability(
            organization=self.organization,
            instructor=self.instructor,
            schedule=self.schedule,
            slot_key='mon_pm1',
            state='invalid',
        )

        self.assertEqual(
            self.evaluate(availability=[stored, duplicate])['code'],
            'duplicate_availability',
        )
        self.assertEqual(
            self.evaluate(availability=[invalid])['code'],
            'invalid_availability_state',
        )

    def test_evaluation_is_pure_and_does_not_write(self):
        occurrence = self.occurrence('mon_pm1', 'mon_pm2')
        before = deepcopy(occurrence)
        counts = (
            InstructorScheduleParticipation.objects.count(),
            InstructorScheduleAvailability.objects.count(),
        )

        with self.assertNumQueries(0):
            result = self.evaluate(occurrence)

        self.assertTrue(result['passes'])
        self.assertEqual(occurrence, before)
        self.assertEqual(counts, (
            InstructorScheduleParticipation.objects.count(),
            InstructorScheduleAvailability.objects.count(),
        ))


class DailyOffRequirementNormalizationTests(ParticipationTestMixin, TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Daily OFF Normalization Org')
        self.other_organization = Organization.objects.create(name='Foreign Daily OFF Org')
        self.schedule = self.create_schedule()
        self.other_schedule = self.create_schedule('Other Daily OFF Week')
        self.instructor = self.create_instructor('Zoe', 'Alpha')
        self.second = self.create_instructor('Avery', 'Beta')
        self.foreign = self.create_instructor(
            'Foreign',
            organization=self.other_organization,
        )

    def availability(
        self,
        instructor,
        slot_key,
        state=InstructorScheduleAvailability.UNAVAILABLE,
        organization=None,
        schedule=None,
    ):
        return InstructorScheduleAvailability(
            organization_id=(organization or self.organization).pk,
            instructor_id=instructor.pk,
            schedule_id=(schedule or self.schedule).pk,
            slot_key=slot_key,
            state=state,
        )

    def normalize(self, instructors=None, records=None):
        return normalize_daily_off_requirements(
            self.organization.pk,
            self.schedule.pk,
            list(instructors or (self.instructor,)),
            list(records or ()),
        )

    def test_canonical_metadata_contains_only_required_days_and_all_five_slots(self):
        self.assertEqual(DAILY_OFF_REQUIRED_DAY_KEYS, ('Tue', 'Wed', 'Thur'))
        self.assertEqual(
            DAILY_OFF_ELIGIBLE_SLOT_KEYS_BY_DAY,
            (
                ('Tue', ('tue_am1', 'tue_am2', 'tue_pm1', 'tue_pm2', 'tue_night')),
                ('Wed', ('wed_am1', 'wed_am2', 'wed_pm1', 'wed_pm2', 'wed_night')),
                (
                    'Thur',
                    ('thur_am1', 'thur_am2', 'thur_pm1', 'thur_pm2', 'thur_night'),
                ),
            ),
        )

    def test_every_participant_receives_one_requirement_per_required_day(self):
        result = self.normalize(instructors=[self.second, self.instructor])

        self.assertEqual(
            [(item['instructor_id'], item['day_key']) for item in result['requirements']],
            [
                (self.instructor.pk, 'Tue'),
                (self.instructor.pk, 'Wed'),
                (self.instructor.pk, 'Thur'),
                (self.second.pk, 'Tue'),
                (self.second.pk, 'Wed'),
                (self.second.pk, 'Thur'),
            ],
        )
        self.assertTrue(all(
            item['status'] == DAILY_OFF_RESERVATION_REQUIRED
            and item['satisfaction_slot_key'] is None
            for item in result['requirements']
        ))

    def test_each_eligible_slot_can_satisfy_its_day(self):
        for day_key, eligible_slot_keys in DAILY_OFF_ELIGIBLE_SLOT_KEYS_BY_DAY:
            for slot_key in eligible_slot_keys:
                with self.subTest(day_key=day_key, slot_key=slot_key):
                    result = self.normalize(records=[
                        self.availability(self.instructor, slot_key)
                    ])
                    requirement = next(
                        item for item in result['requirements']
                        if item['day_key'] == day_key
                    )
                    self.assertEqual(
                        requirement['status'],
                        DAILY_OFF_SATISFIED_BY_AVAILABILITY,
                    )
                    self.assertEqual(requirement['satisfaction_slot_key'], slot_key)

    def test_multiple_unavailable_slots_choose_first_canonical_satisfaction_slot(self):
        result = self.normalize(records=[
            self.availability(self.instructor, 'tue_night'),
            self.availability(self.instructor, 'tue_pm1'),
            self.availability(self.instructor, 'tue_am2'),
        ])
        tuesday = result['requirements'][0]

        self.assertEqual(tuesday['status'], DAILY_OFF_SATISFIED_BY_AVAILABILITY)
        self.assertEqual(tuesday['satisfaction_slot_key'], 'tue_am2')
        self.assertEqual(
            result['unavailable_slot_keys_by_instructor'][self.instructor.pk],
            ('tue_am2', 'tue_pm1', 'tue_night'),
        )

    def test_available_and_missing_records_require_future_reservations(self):
        result = self.normalize(records=[
            self.availability(
                self.instructor,
                'tue_am1',
                InstructorScheduleAvailability.AVAILABLE,
            )
        ])

        self.assertTrue(all(
            item['status'] == DAILY_OFF_RESERVATION_REQUIRED
            for item in result['requirements']
        ))
        self.assertEqual(
            result['unavailable_slot_keys_by_instructor'][self.instructor.pk],
            (),
        )

    def test_non_required_day_unavailability_is_preserved_without_requirement(self):
        result = self.normalize(records=[
            self.availability(self.instructor, 'mon_pm1'),
            self.availability(self.instructor, 'fri_am1'),
        ])

        self.assertEqual(
            result['unavailable_slot_keys_by_instructor'][self.instructor.pk],
            ('mon_pm1', 'fri_am1'),
        )
        self.assertEqual(
            [item['day_key'] for item in result['requirements']],
            ['Tue', 'Wed', 'Thur'],
        )
        self.assertTrue(all(
            item['status'] == DAILY_OFF_RESERVATION_REQUIRED
            for item in result['requirements']
        ))

    def test_nonparticipants_foreign_instructors_and_foreign_records_are_ineffective(self):
        result = self.normalize(
            instructors=[self.instructor, self.foreign],
            records=[
                self.availability(self.second, 'tue_am1'),
                self.availability(
                    self.foreign,
                    'tue_am1',
                    organization=self.other_organization,
                    schedule=self.create_schedule(
                        'Foreign Daily OFF Week', self.other_organization
                    ),
                ),
                self.availability(
                    self.instructor,
                    'tue_am1',
                    schedule=self.other_schedule,
                ),
            ],
        )

        self.assertEqual(
            {item['instructor_id'] for item in result['requirements']},
            {self.instructor.pk},
        )
        self.assertTrue(all(
            item['status'] == DAILY_OFF_RESERVATION_REQUIRED
            for item in result['requirements']
        ))

    def test_duplicate_and_malformed_relevant_records_fail_closed(self):
        valid = self.availability(self.instructor, 'tue_am1')
        malformed_slot = self.availability(self.instructor, 'sat_am1')
        malformed_state = self.availability(self.instructor, 'tue_am1', 'tentative')

        for records in ([valid, valid], [malformed_slot], [malformed_state]):
            with self.subTest(records=records), self.assertRaises(ValidationError):
                self.normalize(records=records)

    def test_reversed_inputs_are_identical_and_normalization_has_no_queries_or_writes(self):
        records = [
            self.availability(self.instructor, 'wed_night'),
            self.availability(self.second, 'tue_pm2'),
            self.availability(self.second, 'fri_am1'),
        ]
        before_counts = (
            InstructorScheduleAvailability.objects.count(),
            InstructorScheduleParticipation.objects.count(),
        )

        with self.assertNumQueries(0):
            forward = self.normalize(
                instructors=[self.second, self.instructor],
                records=records,
            )
            reversed_result = self.normalize(
                instructors=[self.instructor, self.second],
                records=list(reversed(records)),
            )

        self.assertEqual(forward, reversed_result)
        self.assertEqual(before_counts, (
            InstructorScheduleAvailability.objects.count(),
            InstructorScheduleParticipation.objects.count(),
        ))


class InstructorAssignmentOrchestrationTests(ParticipationTestMixin, TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Assignment Orchestration Org')
        self.other_organization = Organization.objects.create(name='Foreign Assignment Org')
        self.course = Course.objects.create(
            organization=self.organization,
            course_name='Archery',
            abriviation='ARCH',
            course_len=1,
        )
        self.schedule = self.create_schedule(generated={
            'ags': ['School 0'],
            'mon_pm1': [self.course.course_name],
        })
        self.instructor = self.create_instructor('First')
        self.nonparticipant = self.create_instructor('Second')
        self.undecided = self.create_instructor('Third')

    def run_assignment(self):
        return run_instructor_assignment(self.schedule)

    def opt_out(self, *instructors):
        for instructor in instructors:
            self.participation(
                instructor=instructor,
                state=InstructorScheduleParticipation.NOT_PARTICIPATING,
            )

    def test_production_invokes_off_planner_and_not_legacy_greedy_strategy(self):
        with patch(
            'scheduler_app.instructor_assignment.assign_occurrences_deterministically',
            side_effect=AssertionError('legacy greedy strategy invoked'),
        ), patch(
            'scheduler_app.instructor_assignment.plan_instructor_assignments_with_daily_off',
            wraps=plan_instructor_assignments_with_daily_off,
        ) as planner:
            result = self.run_assignment()

        planner.assert_called_once()
        self.assertIn('off_requirements', result)
        self.assertIn('off_reservations', result)
        self.assertIn('coverage', result)

    def test_production_exposes_required_days_and_availability_satisfaction(self):
        self.opt_out(self.nonparticipant, self.undecided)
        InstructorScheduleAvailability.objects.create(
            organization=self.organization,
            instructor=self.instructor,
            schedule=self.schedule,
            slot_key='tue_pm1',
            state='unavailable',
        )

        result = self.run_assignment()

        self.assertEqual(
            [item['day_key'] for item in result['off_requirements']],
            ['Tue', 'Wed', 'Thur'],
        )
        self.assertEqual(
            result['requirements_satisfied_by_availability'][0][
                'satisfaction_slot_key'
            ],
            'tue_pm1',
        )
        self.assertEqual(
            [item['day_key'] for item in result['off_reservations']],
            ['Wed', 'Thur'],
        )
        self.assertEqual(
            result['unavailable_slot_keys_by_instructor'][self.instructor.pk],
            ('tue_pm1',),
        )

    def test_night_is_selected_as_production_reservation(self):
        self.opt_out(self.nonparticipant, self.undecided)
        self.schedule.sched_data['generated_schedule'] = {
            'ags': ['School 0'],
            'tue_am1': [self.course.course_name],
            'tue_am2': [self.course.course_name],
            'tue_pm1': [self.course.course_name],
            'tue_pm2': [self.course.course_name],
        }
        self.schedule.save(update_fields=['sched_data'])

        result = self.run_assignment()
        tuesday = next(
            item for item in result['off_reservations'] if item['day_key'] == 'Tue'
        )

        self.assertEqual(tuesday['slot_key'], 'tue_night')
        self.assertEqual(result['coverage']['assigned_occurrence_count'], 4)
        self.assertTrue(result['coverage']['complete'])
        self.assertNotIn(
            'tue_night',
            {
                slot['slot_key']
                for assignment in result['assignments']
                if assignment['assigned_instructor'] == self.instructor
                for slot in assignment['occurrence']['slot_footprint']
            },
        )

    def test_production_reports_incomplete_coverage_without_violating_off(self):
        self.opt_out(self.nonparticipant, self.undecided)
        self.schedule.sched_data['generated_schedule'] = {
            'ags': ['School 0'],
            'tue_am1': [self.course.course_name],
            'tue_am2': [self.course.course_name],
            'tue_pm1': [self.course.course_name],
            'tue_pm2': [self.course.course_name],
            'tue_night': [self.course.course_name],
        }
        self.schedule.save(update_fields=['sched_data'])

        result = self.run_assignment()

        self.assertEqual(result['coverage'], {
            'assigned_occurrence_count': 4,
            'unstaffed_occurrence_count': 1,
            'complete': False,
        })
        self.assertEqual(len(result['unstaffed_occurrences']), 1)
        reservation = next(
            item for item in result['off_reservations'] if item['day_key'] == 'Tue'
        )
        assigned_slots = {
            slot['slot_key']
            for assignment in result['assignments']
            if assignment['assigned_instructor'] == self.instructor
            for slot in assignment['occurrence']['slot_footprint']
        }
        self.assertNotIn(reservation['slot_key'], assigned_slots)

    def test_production_reconsiders_legacy_greedy_qualification_choice(self):
        specialized = Course.objects.create(
            organization=self.organization,
            course_name='Specialized Climbing',
            abriviation='SPCL',
            course_len=1,
        )
        self.schedule.sched_data['generated_schedule'] = {
            'ags': ['School 0', 'School 1'],
            'mon_pm1': [self.course.course_name, specialized.course_name],
        }
        self.schedule.save(update_fields=['sched_data'])
        self.opt_out(self.undecided)
        certification = Certification.objects.create(
            organization=self.organization,
            name='Specialized Qualification',
        )
        ActivityCertificationRequirement.objects.create(
            course=specialized,
            certification=certification,
        )
        InstructorCertification.objects.create(
            instructor=self.instructor,
            certification=certification,
        )

        result = self.run_assignment()
        assignments = {
            assignment['occurrence']['activity_id']: assignment['assigned_instructor']
            for assignment in result['assignments']
        }

        self.assertEqual(assignments[self.course.pk], self.nonparticipant)
        self.assertEqual(assignments[specialized.pk], self.instructor)
        self.assertTrue(result['coverage']['complete'])

    def test_production_planning_fields_are_deterministic_and_read_only(self):
        stored_before = deepcopy(self.schedule.sched_data)
        counts_before = (
            InstructorScheduleParticipation.objects.count(),
            InstructorScheduleAvailability.objects.count(),
        )

        first = self.run_assignment()
        second = self.run_assignment()

        for field in (
            'assignments',
            'off_requirements',
            'off_reservations',
            'requirements_satisfied_by_availability',
            'unavailable_slot_keys_by_instructor',
            'coverage',
        ):
            self.assertEqual(first[field], second[field])
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data, stored_before)
        self.assertEqual(counts_before, (
            InstructorScheduleParticipation.objects.count(),
            InstructorScheduleAvailability.objects.count(),
        ))

    def test_production_query_count_is_bounded_independent_of_occurrence_count(self):
        with CaptureQueriesContext(connection) as single_occurrence_queries:
            self.run_assignment()

        self.schedule.sched_data['generated_schedule'] = {
            'ags': ['School 0'],
            'tue_am1': [self.course.course_name],
            'tue_am2': [self.course.course_name],
            'tue_pm1': [self.course.course_name],
            'tue_pm2': [self.course.course_name],
            'tue_night': [self.course.course_name],
        }
        self.schedule.save(update_fields=['sched_data'])
        with CaptureQueriesContext(connection) as five_occurrence_queries:
            self.run_assignment()

        self.assertEqual(
            len(single_occurrence_queries),
            len(five_occurrence_queries),
        )
        self.assertTrue(all(
            query['sql'].lstrip().upper().startswith('SELECT')
            for query in five_occurrence_queries.captured_queries
        ))

    def test_default_and_explicit_participants_are_candidates_without_slot_rows(self):
        self.participation(instructor=self.instructor)
        self.participation(
            instructor=self.nonparticipant,
            state=InstructorScheduleParticipation.NOT_PARTICIPATING,
        )

        result = self.run_assignment()

        self.assertEqual(result['schedule_id'], self.schedule.pk)
        self.assertEqual(result['organization_id'], self.organization.pk)
        self.assertEqual(
            result['candidate_instructors'],
            (self.instructor, self.undecided),
        )
        self.assertEqual(
            result['assignments'][0]['assigned_instructor'], self.instructor
        )
        self.assertIn(self.undecided, result['candidate_instructors'])
        self.assertFalse(InstructorScheduleAvailability.objects.exists())

    def test_unavailable_exception_prevents_assignment(self):
        self.participation(instructor=self.instructor)
        InstructorScheduleAvailability.objects.create(
            organization=self.organization,
            instructor=self.instructor,
            schedule=self.schedule,
            slot_key='mon_pm1',
            state='unavailable',
        )
        self.participation(
            instructor=self.nonparticipant,
            state=InstructorScheduleParticipation.NOT_PARTICIPATING,
        )
        self.participation(
            instructor=self.undecided,
            state=InstructorScheduleParticipation.NOT_PARTICIPATING,
        )

        assignment = self.run_assignment()['assignments'][0]

        self.assertEqual(assignment['status'], 'unstaffed')
        self.assertEqual(
            assignment['constraint_rejections'][0]['reasons'][0]['code'],
            'explicitly_unavailable',
        )

    def test_qualification_still_blocks_unqualified_participant(self):
        self.participation(instructor=self.instructor)
        certification = Certification.objects.create(
            organization=self.organization, name='Ropes'
        )
        ActivityCertificationRequirement.objects.create(
            course=self.course, certification=certification
        )

        assignment = self.run_assignment()['assignments'][0]

        self.assertEqual(assignment['status'], 'unstaffed')
        self.assertEqual(assignment['reason'], 'No qualified instructors available.')

    def test_qualified_participant_can_assign(self):
        self.participation(instructor=self.instructor)
        certification = Certification.objects.create(
            organization=self.organization, name='Ropes'
        )
        ActivityCertificationRequirement.objects.create(
            course=self.course, certification=certification
        )
        InstructorCertification.objects.create(
            instructor=self.instructor, certification=certification
        )

        self.assertEqual(
            self.run_assignment()['assignments'][0]['assigned_instructor'], self.instructor
        )

    def test_overlap_uses_distinct_default_participants_for_simultaneous_occurrences(self):
        second_course = Course.objects.create(
            organization=self.organization,
            course_name='Climbing',
            abriviation='CLMB',
            course_len=1,
        )
        self.schedule.sched_data['generated_schedule'] = {
            'ags': ['School 0', 'School 1'],
            'mon_pm1': [self.course.course_name, second_course.course_name],
        }
        self.schedule.save(update_fields=['sched_data'])
        self.participation(instructor=self.instructor)

        assignments = self.run_assignment()['assignments']

        self.assertEqual(assignments[0]['assigned_instructor'], self.instructor)
        self.assertEqual(assignments[1]['assigned_instructor'], self.nonparticipant)
        self.assertEqual(
            assignments[1]['constraint_rejections'][0]['reasons'][0]['code'],
            'overlapping_assignment',
        )

    def test_multi_slot_assignment_uses_complete_footprint(self):
        self.course.course_len = 2
        self.course.save(update_fields=['course_len'])
        self.schedule.sched_data['generated_schedule'] = {
            'ags': ['School 0'],
            'tue_am1': [self.course.course_name],
            'tue_am2': [self.course.course_name],
        }
        self.schedule.save(update_fields=['sched_data'])
        self.participation(instructor=self.instructor)

        result = self.run_assignment()

        self.assertEqual(len(result['assignments']), 1)
        self.assertEqual(
            [slot['slot_key'] for slot in result['occurrences'][0]['slot_footprint']],
            ['tue_am1', 'tue_am2'],
        )

    def test_results_are_deterministic_and_not_persisted(self):
        self.participation(instructor=self.instructor)
        stored_before = deepcopy(self.schedule.sched_data)

        first = self.run_assignment()
        second = self.run_assignment()

        self.assertEqual(first, second)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data, stored_before)
        self.assertNotIn('instructor_assignments', self.schedule.sched_data)

    def test_mixed_schedule_occurrences_are_rejected(self):
        self.participation(instructor=self.instructor)
        mixed = self.occurrence('mon_pm1')
        mixed['schedule_id'] = self.schedule.pk + 1000

        with patch(
            'scheduler_app.instructor_assignment.extract_operational_occurrences',
            return_value=[mixed],
        ), self.assertRaises(ValidationError):
            self.run_assignment()

    def test_mixed_organization_occurrences_are_rejected(self):
        self.participation(instructor=self.instructor)
        mixed = self.occurrence('mon_pm1')
        mixed['organization_id'] = self.other_organization.pk

        with patch(
            'scheduler_app.instructor_assignment.extract_operational_occurrences',
            return_value=[mixed],
        ), self.assertRaises(ValidationError):
            self.run_assignment()
