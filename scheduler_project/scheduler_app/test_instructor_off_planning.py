from copy import deepcopy
from inspect import signature
from time import perf_counter

from django.core.exceptions import ValidationError
from django.test import TestCase

from members.models import Organization

from .instructor_assignment import (
    _advance_group_continuity_state,
    _order_candidates_for_group_continuity,
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
        search_stats=None,
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
            _search_stats=search_stats,
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


class InstructorGroupContinuityPlanningTests(TestCase):
    """Desired continuity semantics for the maximum-coverage OFF planner."""

    CERTIFICATION_A = 101
    CERTIFICATION_B = 202

    def setUp(self):
        self.organization = Organization.objects.create(name='Continuity Planner Org')
        self.other_organization = Organization.objects.create(
            name='Foreign Continuity Planner Org'
        )
        self.schedule = TheSched.objects.create(
            organization=self.organization,
            sched_name='Continuity Planner Week',
            sched_data={},
        )
        self.first = self.create_instructor('Blake', 'Beta')
        self.second = self.create_instructor('Avery', 'Alpha')

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

    def availability(self, instructor, slot_key, state='unavailable'):
        return InstructorScheduleAvailability(
            organization_id=self.organization.pk,
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
        search_stats=None,
    ):
        instructors = tuple(instructors or (self.instructor_a, self.instructor_b))
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
            _search_stats=search_stats,
        )

    def assigned_slot_keys(self, result, instructor):
        return {
            slot['slot_key']
            for assignment in result['assignments']
            if assignment['assigned_instructor'] == instructor
            for slot in assignment['occurrence']['slot_footprint']
        }

    @property
    def instructor_a(self):
        # Deliberately higher-PK so returning to A cannot be a PK-order accident.
        return self.second

    @property
    def instructor_b(self):
        return self.first

    def assignment_map(self, result):
        return {
            assignment['occurrence']['occurrence_id']: assignment['assigned_instructor']
            for assignment in result['assignments']
        }

    def group_sequence(self, result, group_index=0):
        return tuple(
            assignment['assigned_instructor']
            for assignment in result['assignments']
            if assignment['occurrence']['group_index'] == group_index
            and assignment['status'] == 'assigned'
        )

    def assert_group_sequence(self, result, expected, group_index=0):
        self.assertEqual(self.group_sequence(result, group_index), tuple(expected))

    def assert_assignments_are_overlap_free(self, result):
        occupied_cells = []
        for assignment in result['assignments']:
            instructor = assignment['assigned_instructor']
            if instructor is None:
                continue
            occupied_cells.extend(
                (instructor.pk, slot['slot_key'])
                for slot in assignment['occurrence']['slot_footprint']
            )
        self.assertEqual(len(occupied_cells), len(set(occupied_cells)))

    def qualification_context(self, a_only=(), b_only=()):
        requirements = {
            activity_id: {self.CERTIFICATION_A} for activity_id in a_only
        }
        requirements.update({
            activity_id: {self.CERTIFICATION_B} for activity_id in b_only
        })
        return {
            'certifications': {
                self.instructor_a.pk: {self.CERTIFICATION_A},
                self.instructor_b.pk: {self.CERTIFICATION_B},
            },
            'requirements': requirements,
        }

    def continuity_state_for(
        self,
        occurrences,
        assignment_instructors,
        eligible_instructors_by_occurrence,
    ):
        state = {}
        candidate_orders = []
        for occurrence, selected, eligible in zip(
            occurrences,
            assignment_instructors,
            eligible_instructors_by_occurrence,
        ):
            candidate_orders.append(tuple(
                _order_candidates_for_group_continuity(
                    occurrence,
                    eligible,
                    state,
                )
            ))
            state = _advance_group_continuity_state(
                occurrence,
                selected,
                eligible,
                state,
            )
        return state, tuple(candidate_orders)

    def test_uninterrupted_continuity_beats_avoidable_return(self):
        occurrences = [
            # B must cover separate simultaneous work before and after the
            # target group's sequence, but both instructors are valid for all
            # three target-group occurrences.
            self.occurrence('other-before', 10, 'mon_pm1', group_index=0),
            self.occurrence('other-after', 11, 'tue_am1', group_index=0),
            self.occurrence('target-before', 20, 'mon_pm1', group_index=1),
            self.occurrence('target-middle', 21, 'mon_pm2', group_index=1),
            self.occurrence('target-after', 22, 'tue_am1', group_index=1),
        ]
        context = self.qualification_context(b_only=(10, 11))

        result = self.plan(
            occurrences=occurrences,
            instructors=[self.instructor_a, self.instructor_b],
            **context,
        )

        self.assertTrue(result['coverage']['complete'])
        self.assert_group_sequence(
            result,
            (self.instructor_a, self.instructor_a, self.instructor_a),
            group_index=1,
        )

    def test_returns_after_qualification_forced_interruption(self):
        occurrences = [
            self.occurrence('before', 1, 'mon_pm1'),
            self.occurrence('interruption', 2, 'mon_pm2'),
            self.occurrence('after', 3, 'mon_night'),
        ]
        context = self.qualification_context(a_only=(1,), b_only=(2,))

        result = self.plan(
            occurrences=occurrences,
            instructors=[self.instructor_b, self.instructor_a],
            **context,
        )

        self.assertTrue(result['coverage']['complete'])
        self.assert_group_sequence(
            result, (self.instructor_a, self.instructor_b, self.instructor_a)
        )

    def test_returns_after_availability_forced_interruption(self):
        occurrences = [
            self.occurrence('before', 1, 'mon_pm1'),
            self.occurrence('interruption', 2, 'mon_pm2'),
            self.occurrence('after', 3, 'mon_night'),
        ]
        context = self.qualification_context(a_only=(1,))
        unavailable = self.availability(self.instructor_a, 'mon_pm2')

        result = self.plan(
            occurrences=occurrences,
            instructors=[self.instructor_a, self.instructor_b],
            availability=[unavailable],
            **context,
        )

        self.assertTrue(result['coverage']['complete'])
        self.assert_group_sequence(
            result, (self.instructor_a, self.instructor_b, self.instructor_a)
        )

    def test_returns_after_off_forced_interruption_and_preserves_reservation(self):
        occurrences = [
            self.occurrence('other-am1', 10, 'tue_am1', group_index=0),
            self.occurrence('other-am2', 11, 'tue_am2', group_index=0),
            self.occurrence('other-pm1', 12, 'tue_pm1', group_index=0),
            self.occurrence('other-pm2', 13, 'tue_pm2', group_index=0),
            self.occurrence('target-before', 1, 'mon_pm1', group_index=1),
            self.occurrence('target-interruption', 2, 'tue_night', group_index=1),
            self.occurrence('target-after', 3, 'wed_am1', group_index=1),
        ]
        context = self.qualification_context(
            a_only=(1, 10, 11, 12, 13),
        )

        result = self.plan(
            occurrences=occurrences,
            instructors=[self.instructor_b, self.instructor_a],
            **context,
        )

        self.assertTrue(result['coverage']['complete'])
        a_tuesday_off = next(
            reservation for reservation in result['off_reservations']
            if reservation['instructor_id'] == self.instructor_a.pk
            and reservation['day_key'] == 'Tue'
        )
        self.assertNotIn(
            a_tuesday_off['slot_key'], self.assigned_slot_keys(result, self.instructor_a)
        )
        self.assert_group_sequence(
            result,
            (self.instructor_a, self.instructor_b, self.instructor_a),
            group_index=1,
        )

    def test_returns_after_overlap_forced_interruption(self):
        occurrences = [
            self.occurrence('other-simultaneous', 10, 'mon_pm2', group_index=0),
            self.occurrence('target-before', 1, 'mon_pm1', group_index=1),
            self.occurrence('target-interruption', 2, 'mon_pm2', group_index=1),
            self.occurrence('target-after', 3, 'mon_night', group_index=1),
        ]
        context = self.qualification_context(a_only=(1, 10))

        result = self.plan(
            occurrences=occurrences,
            instructors=[self.instructor_b, self.instructor_a],
            **context,
        )

        self.assertTrue(result['coverage']['complete'])
        self.assert_group_sequence(
            result,
            (self.instructor_a, self.instructor_b, self.instructor_a),
            group_index=1,
        )
        self.assert_assignments_are_overlap_free(result)

    def test_avoidable_return_does_not_beat_one_handoff(self):
        occurrences = [
            self.occurrence('first', 1, 'mon_pm1'),
            self.occurrence('second', 2, 'mon_pm2'),
            self.occurrence('third', 3, 'mon_night'),
        ]
        context = self.qualification_context(a_only=(1,))

        result = self.plan(
            occurrences=occurrences,
            instructors=[self.instructor_a, self.instructor_b],
            **context,
        )

        sequence = self.group_sequence(result)
        self.assertEqual(sequence[0], self.instructor_a)
        change_count = sum(
            previous != current for previous, current in zip(sequence, sequence[1:])
        )
        self.assertLessEqual(change_count, 1)

    def test_unstaffed_occurrence_is_ignored_for_continuity(self):
        occurrences = [
            self.occurrence('before', 1, 'mon_pm1'),
            self.occurrence('unstaffed', 2, 'mon_pm2'),
            self.occurrence('after', 3, 'mon_night'),
        ]
        context = self.qualification_context(a_only=(1,))
        context['requirements'][2] = {999}

        result = self.plan(
            occurrences=occurrences,
            instructors=[self.instructor_b, self.instructor_a],
            **context,
        )

        self.assertEqual(result['coverage']['assigned_occurrence_count'], 2)
        self.assertEqual(
            [item['occurrence']['occurrence_id'] for item in result['unstaffed_occurrences']],
            ['unstaffed'],
        )
        self.assert_group_sequence(result, (self.instructor_a, self.instructor_a))

    def test_continuity_crosses_day_boundary(self):
        occurrences = [
            self.occurrence('before', 1, 'mon_night'),
            self.occurrence('interruption', 2, 'tue_am1'),
            self.occurrence('after', 3, 'tue_am2'),
        ]
        context = self.qualification_context(a_only=(1,), b_only=(2,))

        result = self.plan(
            occurrences=occurrences,
            instructors=[self.instructor_b, self.instructor_a],
            **context,
        )

        self.assertTrue(result['coverage']['complete'])
        self.assert_group_sequence(
            result, (self.instructor_a, self.instructor_b, self.instructor_a)
        )

    def test_multi_slot_interruption_counts_once_and_returns_to_a(self):
        occurrences = [
            self.occurrence('before', 1, 'mon_pm1'),
            self.occurrence('interruption', 2, 'tue_am1', 'tue_am2'),
            self.occurrence('after', 3, 'tue_pm1'),
        ]
        context = self.qualification_context(a_only=(1,), b_only=(2,))

        result = self.plan(
            occurrences=occurrences,
            instructors=[self.instructor_b, self.instructor_a],
            **context,
        )

        self.assertEqual(len(result['assignments']), 3)
        interruption = self.assignment_map(result)['interruption']
        self.assertEqual(interruption, self.instructor_b)
        self.assertTrue(
            {'tue_am1', 'tue_am2'}.issubset(
                result['occupied_slot_keys_by_instructor'][self.instructor_b.pk]
            )
        )
        self.assert_group_sequence(
            result, (self.instructor_a, self.instructor_b, self.instructor_a)
        )

    def test_duplicate_group_labels_keep_independent_continuity(self):
        occurrences = [
            self.occurrence('group-zero-first', 10, 'mon_pm1', group_index=0),
            self.occurrence('group-zero-second', 11, 'mon_pm2', group_index=0),
            self.occurrence('group-one-before', 1, 'tue_am1', group_index=1),
            self.occurrence('group-one-interruption', 2, 'tue_am2', group_index=1),
            self.occurrence('group-one-after', 3, 'tue_pm1', group_index=1),
        ]
        for occurrence in occurrences:
            occurrence['group_label'] = 'Duplicate Label'
        context = self.qualification_context(
            a_only=(1,),
            b_only=(2, 10, 11),
        )

        result = self.plan(
            occurrences=occurrences,
            instructors=[self.instructor_b, self.instructor_a],
            **context,
        )

        self.assert_group_sequence(
            result, (self.instructor_b, self.instructor_b), group_index=0
        )
        self.assert_group_sequence(
            result,
            (self.instructor_a, self.instructor_b, self.instructor_a),
            group_index=1,
        )

    def test_maximum_coverage_dominates_stronger_apparent_continuity(self):
        occurrences = [
            self.occurrence('target-before', 1, 'mon_pm1', group_index=0),
            self.occurrence('target-simultaneous', 2, 'mon_pm2', group_index=0),
            self.occurrence('target-after', 3, 'mon_night', group_index=0),
            self.occurrence('specialized', 10, 'mon_pm2', group_index=1),
        ]
        context = self.qualification_context(a_only=(1, 10))

        result = self.plan(
            occurrences=occurrences,
            instructors=[self.instructor_b, self.instructor_a],
            **context,
        )

        self.assertEqual(result['coverage']['assigned_occurrence_count'], 4)
        assignments = self.assignment_map(result)
        self.assertEqual(assignments['specialized'], self.instructor_a)
        self.assertEqual(assignments['target-simultaneous'], self.instructor_b)

    def test_temporary_interruption_is_deterministic_under_reversed_inputs(self):
        occurrences = [
            self.occurrence('before', 1, 'mon_pm1'),
            self.occurrence('interruption', 2, 'mon_pm2'),
            self.occurrence('after', 3, 'mon_night'),
        ]
        context = self.qualification_context(a_only=(1,), b_only=(2,))
        normalized = normalize_daily_off_requirements(
            self.organization.pk,
            self.schedule.pk,
            [self.instructor_b, self.instructor_a],
            [],
        )

        first = plan_instructor_assignments_with_daily_off(
            occurrences,
            [self.instructor_b, self.instructor_a],
            context['certifications'],
            context['requirements'],
            [],
            [],
            normalized,
        )
        reversed_result = plan_instructor_assignments_with_daily_off(
            list(reversed(occurrences)),
            [self.instructor_a, self.instructor_b],
            dict(reversed(tuple(context['certifications'].items()))),
            dict(reversed(tuple(context['requirements'].items()))),
            [],
            [],
            normalized,
        )

        self.assertEqual(first['assignments'], reversed_result['assignments'])
        self.assertEqual(first['off_reservations'], reversed_result['off_reservations'])
        self.assert_group_sequence(
            first, (self.instructor_a, self.instructor_b, self.instructor_a)
        )

    def test_continuity_planning_is_organization_isolated_and_pure(self):
        foreign = self.create_instructor(
            'Foreign', 'Continuity', organization=self.other_organization
        )
        occurrences = [
            self.occurrence('before', 1, 'mon_pm1'),
            self.occurrence('interruption', 2, 'mon_pm2'),
            self.occurrence('after', 3, 'mon_night'),
        ]
        context = self.qualification_context(a_only=(1,), b_only=(2,))
        context['certifications'][foreign.pk] = {
            self.CERTIFICATION_A,
            self.CERTIFICATION_B,
        }
        schedule_before = deepcopy(self.schedule.sched_data)
        counts_before = (
            Instructor.objects.count(),
            InstructorScheduleAvailability.objects.count(),
            InstructorScheduleParticipation.objects.count(),
        )

        first = self.plan(
            occurrences=occurrences,
            instructors=[self.instructor_b, self.instructor_a],
            **context,
        )
        second = self.plan(
            occurrences=list(reversed(occurrences)),
            instructors=[self.instructor_a, self.instructor_b],
            **context,
        )

        self.assertEqual(first, second)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data, schedule_before)
        self.assertEqual(counts_before, (
            Instructor.objects.count(),
            InstructorScheduleAvailability.objects.count(),
            InstructorScheduleParticipation.objects.count(),
        ))
        self.assertNotIn(
            foreign,
            {
                assignment['assigned_instructor']
                for assignment in first['assignments']
            },
        )
        self.assert_group_sequence(
            first, (self.instructor_a, self.instructor_b, self.instructor_a)
        )

    def test_longer_temporary_interruption_counts_each_substitution_once(self):
        occurrences = [
            self.occurrence('before', 1, 'mon_pm1'),
            self.occurrence('interruption-one', 2, 'mon_pm2'),
            self.occurrence('interruption-two', 3, 'mon_night'),
            self.occurrence('return', 4, 'tue_am1'),
        ]
        state, candidate_orders = self.continuity_state_for(
            occurrences,
            [
                self.instructor_a,
                self.instructor_b,
                self.instructor_b,
                self.instructor_a,
            ],
            [
                [self.instructor_a],
                [self.instructor_b],
                [self.instructor_b],
                [self.instructor_b, self.instructor_a],
            ],
        )

        self.assertEqual(candidate_orders[-1][0], self.instructor_a)
        self.assertEqual(state[(self.schedule.pk, 0)], {
            'last_instructor_id': self.instructor_a.pk,
            'pending_return_instructor_id': None,
        })

    def test_temporary_interruption_allows_substitute_instructor_to_change(self):
        instructor_c = self.create_instructor('Casey', 'Gamma')
        occurrences = [
            self.occurrence('before', 1, 'mon_pm1'),
            self.occurrence('interruption-b', 2, 'mon_pm2'),
            self.occurrence('interruption-c', 3, 'mon_night'),
            self.occurrence('return', 4, 'tue_am1'),
        ]
        state, candidate_orders = self.continuity_state_for(
            occurrences,
            [self.instructor_a, self.instructor_b, instructor_c, self.instructor_a],
            [
                [self.instructor_a],
                [self.instructor_b],
                [instructor_c],
                [self.instructor_b, instructor_c, self.instructor_a],
            ],
        )

        self.assertEqual(candidate_orders[-1][0], self.instructor_a)
        self.assertEqual(state[(self.schedule.pk, 0)], {
            'last_instructor_id': self.instructor_a.pk,
            'pending_return_instructor_id': None,
        })

    def test_completed_interruption_does_not_hide_following_handoff(self):
        occurrences = [
            self.occurrence('before', 1, 'mon_pm1'),
            self.occurrence('interruption', 2, 'mon_pm2'),
            self.occurrence('return', 3, 'mon_night'),
            self.occurrence('following-handoff', 4, 'tue_am1'),
        ]
        state, candidate_orders = self.continuity_state_for(
            occurrences,
            [
                self.instructor_a,
                self.instructor_b,
                self.instructor_a,
                self.instructor_b,
            ],
            [
                [self.instructor_a],
                [self.instructor_b],
                [self.instructor_b, self.instructor_a],
                [self.instructor_b],
            ],
        )

        self.assertEqual(candidate_orders[2][0], self.instructor_a)
        self.assertEqual(state[(self.schedule.pk, 0)], {
            'last_instructor_id': self.instructor_b.pk,
            'pending_return_instructor_id': self.instructor_a.pk,
        })

    def test_bounded_search_instrumentation_scenarios(self):
        instructor_c = self.create_instructor('Casey', 'Gamma')
        scenarios = []

        constrained_occurrences = [
            self.occurrence('constrained-a', 1, 'mon_pm1'),
            self.occurrence('constrained-b', 2, 'mon_pm2'),
            self.occurrence('constrained-return', 3, 'mon_night'),
        ]
        constrained_context = self.qualification_context(a_only=(1,), b_only=(2,))
        scenarios.append((
            'mostly-constrained',
            constrained_occurrences,
            [self.instructor_a, self.instructor_b],
            constrained_context,
        ))

        scenarios.append((
            'interchangeable-one-group',
            [
                self.occurrence(f'one-group-{index}', 20 + index, slot_key)
                for index, slot_key in enumerate(
                    ('mon_pm1', 'mon_pm2', 'mon_night', 'tue_am1', 'tue_am2')
                )
            ],
            [self.instructor_a, self.instructor_b, instructor_c],
            {},
        ))

        scenarios.append((
            'interchangeable-multiple-groups',
            [
                self.occurrence(
                    f'group-{group_index}-{slot_key}',
                    40 + group_index * 10 + slot_index,
                    slot_key,
                    group_index=group_index,
                )
                for group_index in range(2)
                for slot_index, slot_key in enumerate(
                    ('mon_pm1', 'mon_pm2', 'mon_night')
                )
            ],
            [self.instructor_a, self.instructor_b, instructor_c],
            {},
        ))

        scenarios.append((
            'several-off-candidates',
            [
                self.occurrence(f'off-{index}', 70 + index, slot_key)
                for index, slot_key in enumerate(
                    ('tue_am1', 'tue_am2', 'tue_pm1', 'tue_pm2')
                )
            ],
            [self.instructor_a, self.instructor_b],
            {},
        ))

        scenarios.append((
            'multi-slot',
            [
                self.occurrence('multi-before', 80, 'mon_pm1'),
                self.occurrence('multi', 81, 'tue_am1', 'tue_am2'),
                self.occurrence('multi-after', 82, 'tue_pm1'),
            ],
            [self.instructor_a, self.instructor_b],
            {},
        ))

        measurements = {}
        for name, occurrences, instructors, context in scenarios:
            stats = {}
            started = perf_counter()
            result = self.plan(
                occurrences=occurrences,
                instructors=instructors,
                certifications=context.get('certifications'),
                requirements=context.get('requirements'),
                search_stats=stats,
            )
            measurements[name] = {
                **stats,
                'runtime_seconds': perf_counter() - started,
            }
            with self.subTest(name=name):
                self.assertGreater(stats['explored_node_count'], 0)
                self.assertGreater(stats['completed_plan_count'], 0)
                self.assertLess(stats['explored_node_count'], 100000)
                self.assertEqual(
                    result['coverage']['assigned_occurrence_count'], len(occurrences)
                )

        self.assertEqual(set(measurements), {name for name, *_ in scenarios})

    def test_production_shaped_interchangeable_search_remains_bounded(self):
        instructors = [self.instructor_a, self.instructor_b] + [
            self.create_instructor(f'Extra {index}', f'Instructor {index}')
            for index in range(10)
        ]
        occurrence_footprints = (
            ('mon_pm1',),
            ('mon_pm2',),
            ('mon_night',),
            ('tue_am1', 'tue_am2'),
            ('tue_pm1',),
            ('wed_am1', 'wed_am2'),
            ('wed_pm1',),
        )
        occurrences = [
            self.occurrence(
                f'production-{group_index}-{occurrence_index}',
                200 + group_index * 10 + occurrence_index,
                *slot_keys,
                group_index=group_index,
            )
            for group_index in range(3)
            for occurrence_index, slot_keys in enumerate(occurrence_footprints)
        ]
        first_stats = {}
        started = perf_counter()
        first = self.plan(
            occurrences=occurrences,
            instructors=instructors,
            search_stats=first_stats,
        )
        runtime_seconds = perf_counter() - started
        second_stats = {}
        second = self.plan(
            occurrences=list(reversed(occurrences)),
            instructors=list(reversed(instructors)),
            search_stats=second_stats,
        )

        self.assertEqual(first['coverage']['assigned_occurrence_count'], 21)
        self.assertTrue(first['coverage']['complete'])
        self.assertLess(first_stats['explored_node_count'], 1000)
        self.assertLess(first_stats['completed_plan_count'], 10)
        self.assertEqual(first_stats, second_stats)
        self.assertEqual(first['assignments'], second['assignments'])
        self.assertEqual(first['off_reservations'], second['off_reservations'])
        self.assertGreater(runtime_seconds, 0)

    def test_search_instrumentation_is_per_call_private_and_reset_on_failure(self):
        parameter = signature(
            plan_instructor_assignments_with_daily_off
        ).parameters['_search_stats']
        self.assertIsNone(parameter.default)

        uninstrumented_result = self.plan(
            occurrences=[self.occurrence('uninstrumented', 1, 'mon_pm1')],
        )
        self.assertNotIn('search_stats', uninstrumented_result)

        stats = {'stale': 99}
        result = self.plan(
            occurrences=[self.occurrence('instrumented', 1, 'mon_pm1')],
            search_stats=stats,
        )

        self.assertEqual(set(stats), {
            'explored_node_count',
            'completed_plan_count',
        })
        self.assertNotIn('search_stats', result)
        self.assertFalse(hasattr(
            plan_instructor_assignments_with_daily_off,
            '_last_search_stats',
        ))

        with self.assertRaises(ValidationError):
            plan_instructor_assignments_with_daily_off(
                [],
                [self.instructor_a],
                {},
                {},
                [],
                [],
                {'requirements': ()},
                _search_stats=stats,
            )
        self.assertEqual(stats, {
            'explored_node_count': 0,
            'completed_plan_count': 0,
        })

    def test_distinct_search_instrumentation_mappings_remain_independent(self):
        first_stats = {}
        second_stats = {}
        self.plan(
            occurrences=[self.occurrence('first-one', 1, 'mon_pm1')],
            search_stats=first_stats,
        )
        first_snapshot = dict(first_stats)

        self.plan(
            occurrences=[
                self.occurrence('second-one', 2, 'mon_pm1'),
                self.occurrence('second-two', 3, 'mon_pm2'),
            ],
            search_stats=second_stats,
        )

        self.assertEqual(first_stats, first_snapshot)
        self.assertIsNot(first_stats, second_stats)
        self.assertNotEqual(first_stats, second_stats)

    def test_reused_search_instrumentation_mapping_is_cleared_each_call(self):
        stats = {}
        self.plan(
            occurrences=[
                self.occurrence('longer-one', 1, 'mon_pm1'),
                self.occurrence('longer-two', 2, 'mon_pm2'),
            ],
            search_stats=stats,
        )
        first_snapshot = dict(stats)
        stats['stale'] = 99

        self.plan(
            occurrences=[self.occurrence('shorter', 3, 'mon_pm1')],
            search_stats=stats,
        )

        self.assertNotIn('stale', stats)
        self.assertNotEqual(stats, first_snapshot)

    def test_validation_failure_resets_only_supplied_instrumentation_mapping(self):
        failing_stats = {'stale': 99}
        untouched_stats = {'independent': 42}

        with self.assertRaises(ValidationError):
            plan_instructor_assignments_with_daily_off(
                [],
                [self.instructor_a],
                {},
                {},
                [],
                [],
                {'requirements': ()},
                _search_stats=failing_stats,
            )

        self.assertEqual(failing_stats, {
            'explored_node_count': 0,
            'completed_plan_count': 0,
        })
        self.assertEqual(untouched_stats, {'independent': 42})
