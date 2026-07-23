from copy import deepcopy

from django.test import TestCase

from members.models import Organization

from .instructor_assignment import (
    normalize_daily_off_requirements,
    plan_instructor_assignments_with_daily_off,
)
from .models import (
    Instructor,
    InstructorScheduleAvailability,
    InstructorScheduleParticipation,
    TheSched,
)


class DailyOffAssignmentPlanningTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='OFF Planner Org')
        self.other_organization = Organization.objects.create(name='Foreign OFF Planner Org')
        self.schedule = TheSched.objects.create(
            organization=self.organization,
            sched_name='OFF Planner Week',
            sched_data={},
        )
        self.first = self.create_instructor('Avery', 'Alpha')
        self.second = self.create_instructor('Blake', 'Beta')

    def create_instructor(self, first, last, organization=None):
        return Instructor.objects.create(
            organization=organization or self.organization,
            fname=first,
            lname=last,
            ropes_lead=False,
            school_lead=False,
            cpr=True,
            firstaid='yes',
        )

    def occurrence(
        self,
        occurrence_id,
        activity_id,
        *slot_keys,
        group_index=0,
        organization_id=None,
    ):
        return {
            'schedule_id': self.schedule.pk,
            'organization_id': organization_id or self.organization.pk,
            'activity_id': activity_id,
            'activity_display_name': f'Activity {activity_id}',
            'group_index': group_index,
            'group_label': f'Group {group_index}',
            'occurrence_id': occurrence_id,
            'slot_footprint': [
                {
                    'block_id': f'{group_index}:{slot_key}:{occurrence_id}',
                    'slot_key': slot_key,
                    'slot_label': slot_key.upper(),
                    'position': position,
                }
                for position, slot_key in enumerate(slot_keys, start=1)
            ],
        }

    def availability(self, instructor, slot_key, state='unavailable', organization=None):
        return InstructorScheduleAvailability(
            organization_id=(organization or self.organization).pk,
            instructor_id=instructor.pk,
            schedule_id=self.schedule.pk,
            slot_key=slot_key,
            state=state,
        )

    def plan(
        self,
        occurrences=(),
        instructors=None,
        availability=(),
        certifications=None,
        requirements=None,
    ):
        instructors = tuple(instructors or (self.first,))
        availability = tuple(availability)
        normalized = normalize_daily_off_requirements(
            self.organization.pk,
            self.schedule.pk,
            instructors,
            availability,
        )
        return plan_instructor_assignments_with_daily_off(
            list(occurrences),
            list(instructors),
            certifications or {},
            requirements or {},
            list(availability),
            [],
            normalized,
        )

    def assigned_slot_keys(self, result, instructor=None):
        instructor = instructor or self.first
        return {
            slot['slot_key']
            for assignment in result['assignments']
            if assignment['assigned_instructor'] == instructor
            for slot in assignment['occurrence']['slot_footprint']
        }

    def test_unsatisfied_requirements_materialize_one_reservation_each(self):
        result = self.plan()

        self.assertEqual(
            result['off_reservations'],
            (
                {'instructor_id': self.first.pk, 'day_key': 'Tue', 'slot_key': 'tue_am1'},
                {'instructor_id': self.first.pk, 'day_key': 'Wed', 'slot_key': 'wed_am1'},
                {'instructor_id': self.first.pk, 'day_key': 'Thur', 'slot_key': 'thur_am1'},
            ),
        )
        self.assertEqual(result['coverage'], {
            'assigned_occurrence_count': 0,
            'unstaffed_occurrence_count': 0,
            'complete': True,
        })

    def test_availability_satisfies_day_without_extra_reservation(self):
        unavailable = self.availability(self.first, 'tue_pm1')
        result = self.plan(availability=[unavailable])

        self.assertEqual(
            result['requirements_satisfied_by_availability'][0][
                'satisfaction_slot_key'
            ],
            'tue_pm1',
        )
        self.assertNotIn(
            'Tue',
            [reservation['day_key'] for reservation in result['off_reservations']],
        )

    def test_each_eligible_slot_can_be_selected_in_canonical_order(self):
        eligible_slots = ('tue_am1', 'tue_am2', 'tue_pm1', 'tue_pm2', 'tue_night')
        for selected_index, expected_slot in enumerate(eligible_slots):
            occurrences = [
                self.occurrence(f'occurrence-{index}', index + 1, slot_key)
                for index, slot_key in enumerate(eligible_slots[:selected_index])
            ]
            with self.subTest(expected_slot=expected_slot):
                result = self.plan(occurrences=occurrences)
                tuesday = next(
                    reservation for reservation in result['off_reservations']
                    if reservation['day_key'] == 'Tue'
                )
                self.assertEqual(tuesday['slot_key'], expected_slot)
                self.assertNotIn(expected_slot, self.assigned_slot_keys(result))

    def test_non_required_day_assignments_create_no_extra_requirements(self):
        result = self.plan(occurrences=[
            self.occurrence('monday', 1, 'mon_pm1'),
            self.occurrence('friday', 2, 'fri_am1'),
        ])

        self.assertEqual(result['coverage']['assigned_occurrence_count'], 2)
        self.assertEqual(
            {reservation['day_key'] for reservation in result['off_reservations']},
            {'Tue', 'Wed', 'Thur'},
        )

    def test_ordinary_gaps_create_exactly_one_reservation_per_required_day(self):
        result = self.plan(occurrences=[
            self.occurrence('tuesday-work', 1, 'tue_pm2'),
        ])

        tuesday_requirements = [
            requirement for requirement in result['off_requirements']
            if requirement['day_key'] == 'Tue'
        ]
        self.assertEqual(len(tuesday_requirements), 1)
        self.assertEqual(
            tuesday_requirements[0]['remaining_candidate_slot_keys'],
            ('tue_am1', 'tue_am2', 'tue_pm1', 'tue_night'),
        )
        self.assertEqual(tuesday_requirements[0]['selected_reservation_slot_key'], 'tue_am1')

    def test_multiple_instructors_can_share_off_slot(self):
        result = self.plan(instructors=[self.second, self.first])
        tuesday_slots = [
            reservation['slot_key'] for reservation in result['off_reservations']
            if reservation['day_key'] == 'Tue'
        ]

        self.assertEqual(tuesday_slots, ['tue_am1', 'tue_am1'])
        self.assertTrue(result['coverage']['complete'])

    def test_insufficient_staffing_preserves_off_and_reports_unstaffed(self):
        slots = ('tue_am1', 'tue_am2', 'tue_pm1', 'tue_pm2', 'tue_night')
        result = self.plan(occurrences=[
            self.occurrence(f'occurrence-{index}', index + 1, slot_key)
            for index, slot_key in enumerate(slots)
        ])

        self.assertEqual(result['coverage']['assigned_occurrence_count'], 4)
        self.assertEqual(result['coverage']['unstaffed_occurrence_count'], 1)
        self.assertFalse(result['coverage']['complete'])
        reservation = next(
            item for item in result['off_reservations'] if item['day_key'] == 'Tue'
        )
        self.assertNotIn(reservation['slot_key'], self.assigned_slot_keys(result))
        rejection_codes = {
            reason['code']
            for assignment in result['unstaffed_occurrences']
            for rejection in assignment['constraint_rejections']
            for reason in rejection['reasons']
        }
        self.assertIn('daily_off_requirement', rejection_codes)

    def test_backtracking_reconsiders_for_unique_qualification(self):
        flexible = self.occurrence('flexible', 1, 'mon_pm1', group_index=0)
        specialized = self.occurrence('specialized', 2, 'mon_pm1', group_index=1)
        certification_id = 99

        result = self.plan(
            occurrences=[specialized, flexible],
            instructors=[self.first, self.second],
            certifications={self.first.pk: {certification_id}},
            requirements={2: {certification_id}},
        )

        assignments = {
            item['occurrence']['occurrence_id']: item['assigned_instructor']
            for item in result['assignments']
        }
        self.assertEqual(assignments['flexible'], self.second)
        self.assertEqual(assignments['specialized'], self.first)
        self.assertTrue(result['coverage']['complete'])

    def test_backtracking_reconsiders_to_preserve_off_capacity(self):
        later_slots = ('tue_am2', 'tue_pm1', 'tue_pm2', 'tue_night')
        occurrences = [self.occurrence('flexible', 1, 'tue_am1')]
        certification_id = 77
        for index, slot_key in enumerate(later_slots, start=2):
            occurrences.append(self.occurrence(f'specialized-{index}', index, slot_key))

        result = self.plan(
            occurrences=occurrences,
            instructors=[self.first, self.second],
            certifications={self.first.pk: {certification_id}},
            requirements={index: {certification_id} for index in range(2, 6)},
        )

        self.assertEqual(result['coverage']['assigned_occurrence_count'], 5)
        assignments = {
            item['occurrence']['occurrence_id']: item['assigned_instructor']
            for item in result['assignments']
        }
        self.assertEqual(assignments['flexible'], self.second)
        self.assertTrue(all(
            assignments[f'specialized-{index}'] == self.first
            for index in range(2, 6)
        ))
        first_tuesday = next(
            item for item in result['off_reservations']
            if item['instructor_id'] == self.first.pk and item['day_key'] == 'Tue'
        )
        self.assertEqual(first_tuesday['slot_key'], 'tue_am1')

    def test_globally_skipped_occurrence_has_truthful_planning_diagnostic(self):
        result = self.plan(occurrences=[
            self.occurrence('a-multi', 1, 'tue_am1', 'tue_am2'),
            self.occurrence('b-single-am1', 2, 'tue_am1'),
            self.occurrence('c-single-am2', 3, 'tue_am2'),
        ])

        self.assertEqual(result['coverage']['assigned_occurrence_count'], 2)
        skipped = next(
            assignment for assignment in result['assignments']
            if assignment['occurrence']['occurrence_id'] == 'a-multi'
        )
        self.assertEqual(skipped['status'], 'unstaffed')
        self.assertEqual(
            skipped['planning_diagnostics'][0]['code'],
            'global_planning_choice',
        )
        self.assertNotEqual(skipped['reason'], 'No eligible instructors available.')

    def test_unavailability_and_multi_slot_footprints_remain_hard_constraints(self):
        unavailable = self.availability(self.first, 'tue_am2')
        result = self.plan(
            occurrences=[self.occurrence('multi', 1, 'tue_am1', 'tue_am2')],
            availability=[unavailable],
        )

        self.assertEqual(result['coverage']['assigned_occurrence_count'], 0)
        self.assertEqual(
            result['unstaffed_occurrences'][0]['constraint_rejections'][0][
                'reasons'
            ][0]['code'],
            'explicitly_unavailable',
        )
        self.assertEqual(result['off_reservations'], (
            {'instructor_id': self.first.pk, 'day_key': 'Wed', 'slot_key': 'wed_am1'},
            {'instructor_id': self.first.pk, 'day_key': 'Thur', 'slot_key': 'thur_am1'},
        ))

    def test_overlap_remains_hard_and_different_instructors_can_cover_simultaneously(self):
        occurrences = [
            self.occurrence('first', 1, 'mon_pm1', group_index=0),
            self.occurrence('second', 2, 'mon_pm1', group_index=1),
        ]
        one_instructor = self.plan(occurrences=occurrences)
        two_instructors = self.plan(
            occurrences=occurrences,
            instructors=[self.first, self.second],
        )

        self.assertEqual(one_instructor['coverage']['assigned_occurrence_count'], 1)
        self.assertEqual(two_instructors['coverage']['assigned_occurrence_count'], 2)
        self.assertEqual(
            len({
                assignment['assigned_instructor'].pk
                for assignment in two_instructors['assignments']
            }),
            2,
        )

    def test_multi_slot_assignment_occupies_every_slot_and_counts_once(self):
        result = self.plan(occurrences=[
            self.occurrence('multi', 1, 'tue_am1', 'tue_am2')
        ])

        self.assertEqual(result['coverage']['assigned_occurrence_count'], 1)
        self.assertEqual(
            result['occupied_slot_keys_by_instructor'][self.first.pk],
            ('tue_am1', 'tue_am2'),
        )
        tuesday = next(
            item for item in result['off_reservations'] if item['day_key'] == 'Tue'
        )
        self.assertEqual(tuesday['slot_key'], 'tue_pm1')

    def test_nonparticipants_and_foreign_data_are_ineffective_and_scope_fails_closed(self):
        foreign = self.create_instructor('Foreign', 'Instructor', self.other_organization)
        foreign_unavailable = self.availability(
            foreign,
            'tue_am1',
            organization=self.other_organization,
        )
        result = self.plan(
            instructors=[self.first],
            availability=[foreign_unavailable],
        )

        self.assertEqual(
            {item['instructor_id'] for item in result['off_requirements']},
            {self.first.pk},
        )
        mixed = self.plan(occurrences=[
            self.occurrence(
                'foreign-occurrence',
                1,
                'mon_pm1',
                organization_id=self.other_organization.pk,
            )
        ])
        self.assertEqual(mixed['coverage']['assigned_occurrence_count'], 0)

    def test_reversed_inputs_are_deterministic_and_planner_is_pure(self):
        occurrences = [
            self.occurrence('later', 2, 'tue_pm1'),
            self.occurrence('earlier', 1, 'tue_am1'),
        ]
        normalized = normalize_daily_off_requirements(
            self.organization.pk,
            self.schedule.pk,
            [self.second, self.first],
            [],
        )
        before = deepcopy(normalized)
        database_counts = (
            InstructorScheduleAvailability.objects.count(),
            InstructorScheduleParticipation.objects.count(),
        )

        with self.assertNumQueries(0):
            first_result = plan_instructor_assignments_with_daily_off(
                occurrences,
                [self.second, self.first],
                {},
                {},
                [],
                [],
                normalized,
            )
            second_result = plan_instructor_assignments_with_daily_off(
                list(reversed(occurrences)),
                [self.first, self.second],
                {},
                {},
                [],
                [],
                normalized,
            )

        self.assertEqual(first_result, second_result)
        self.assertEqual(normalized, before)
        self.assertEqual(database_counts, (
            InstructorScheduleAvailability.objects.count(),
            InstructorScheduleParticipation.objects.count(),
        ))
