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
        fixed_assignments=(),
        participation=(),
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
            list(participation),
            normalized,
            fixed_assignments=list(fixed_assignments),
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


class FixedInstructorAssignmentPlanningTests(DailyOffAssignmentPlanningTests):
    def fixed(self, occurrence, instructor):
        return ({'occurrence': occurrence, 'instructor': instructor},)

    def assignment_map(self, result):
        return {
            assignment['occurrence']['occurrence_id']:
            assignment['assigned_instructor']
            for assignment in result['assignments']
        }

    def diagnostic(self, result):
        return result['fixed_assignment_diagnostics'][0]

    def test_no_fixed_assignment_preserves_existing_result_shape_and_value(self):
        occurrences = (
            self.occurrence('first', 1, 'mon_pm1'),
            self.occurrence('second', 2, 'mon_pm2'),
        )

        existing = self.plan(occurrences, instructors=(self.first, self.second))
        explicit_empty = self.plan(
            occurrences,
            instructors=(self.first, self.second),
            fixed_assignments=(),
        )

        self.assertEqual(existing, explicit_empty)
        self.assertNotIn('fixed_assignment_diagnostics', existing)

    def test_valid_replacement_is_honored_and_releases_automatic_instructor(self):
        occurrences = (
            self.occurrence('fixed', 1, 'mon_pm1'),
            self.occurrence('other', 2, 'mon_pm1', group_index=1),
        )
        baseline = self.plan(occurrences, instructors=(self.first, self.second))

        result = self.plan(
            occurrences,
            instructors=(self.first, self.second),
            fixed_assignments=self.fixed(occurrences[0], self.second),
        )

        self.assertEqual(
            self.assignment_map(baseline)['fixed'],
            self.first,
        )
        self.assertEqual(self.assignment_map(result), {
            'fixed': self.second,
            'other': self.first,
        })
        self.assertEqual(
            result['assignments'][0]['assignment_source'],
            'fixed',
        )

    def test_valid_fixed_assignment_can_staff_baseline_unstaffed_occurrence(self):
        occurrences = (
            self.occurrence('ordinary', 1, 'mon_pm1'),
            self.occurrence('special', 2, 'mon_pm1', group_index=1),
        )
        baseline = self.plan(
            occurrences,
            instructors=(self.first,),
        )
        self.assertEqual(
            self.assignment_map(baseline)['special'],
            None,
        )

        result = self.plan(
            occurrences,
            instructors=(self.first,),
            fixed_assignments=self.fixed(occurrences[1], self.first),
        )

        self.assertEqual(self.assignment_map(result)['special'], self.first)
        self.assertIsNone(self.assignment_map(result)['ordinary'])
        self.assertTrue(self.diagnostic(result)['accepted'])

    def test_unqualified_nonparticipant_and_foreign_instructors_are_rejected(self):
        occurrence = self.occurrence('fixed', 1, 'mon_pm1')
        unqualified = self.plan(
            (occurrence,),
            instructors=(self.first, self.second),
            certifications={self.first.pk: {10}, self.second.pk: set()},
            requirements={1: {10}},
            fixed_assignments=self.fixed(occurrence, self.second),
        )
        self.assertEqual(
            self.diagnostic(unqualified)['rejection_code'],
            'qualification_requirements_not_met',
        )

        opt_out = InstructorScheduleParticipation(
            organization=self.organization,
            schedule=self.schedule,
            instructor=self.second,
            state=InstructorScheduleParticipation.NOT_PARTICIPATING,
        )
        nonparticipant = self.plan(
            (occurrence,),
            instructors=(self.first,),
            fixed_assignments=self.fixed(occurrence, self.second),
            participation=(opt_out,),
        )
        self.assertEqual(
            self.diagnostic(nonparticipant)['rejection_code'],
            'not_participating',
        )

        foreign = self.create_instructor(
            'Foreign',
            'Instructor',
            organization=self.other_organization,
        )
        isolated = self.plan(
            (occurrence,),
            instructors=(self.first,),
            fixed_assignments=self.fixed(occurrence, foreign),
        )
        self.assertEqual(
            self.diagnostic(isolated)['rejection_code'],
            'organization_mismatch',
        )

    def test_unavailable_single_and_multi_slot_fixed_assignments_are_rejected(self):
        single = self.occurrence('single', 1, 'mon_pm1')
        single_result = self.plan(
            (single,),
            availability=(self.availability(self.first, 'mon_pm1'),),
            fixed_assignments=self.fixed(single, self.first),
        )
        self.assertEqual(
            self.diagnostic(single_result)['rejection_code'],
            'explicitly_unavailable',
        )

        multi = self.occurrence('multi', 2, 'tue_am1', 'tue_am2')
        multi_result = self.plan(
            (multi,),
            availability=(self.availability(self.first, 'tue_am2'),),
            fixed_assignments=self.fixed(multi, self.first),
        )
        diagnostic = self.diagnostic(multi_result)
        self.assertEqual(diagnostic['rejection_code'], 'explicitly_unavailable')
        self.assertEqual(diagnostic['affected_slot_keys'], ('tue_am2',))

    def test_valid_multi_slot_fixed_assignment_reserves_complete_footprint(self):
        occurrence = self.occurrence('multi', 1, 'mon_pm1', 'mon_pm2')

        result = self.plan(
            (occurrence,),
            fixed_assignments=self.fixed(occurrence, self.first),
        )

        self.assertEqual(
            result['occupied_slot_keys_by_instructor'][self.first.pk],
            ('mon_pm1', 'mon_pm2'),
        )
        self.assertEqual(
            self.diagnostic(result)['affected_slot_keys'],
            ('mon_pm1', 'mon_pm2'),
        )

    def test_fixed_assignment_rejects_final_off_slot_and_can_relocate_off(self):
        occurrence = self.occurrence(
            'fixed',
            1,
            'tue_am1',
            'tue_am2',
            'tue_pm1',
            'tue_pm2',
            'tue_night',
        )
        rejected = self.plan(
            (occurrence,),
            fixed_assignments=self.fixed(occurrence, self.first),
        )
        self.assertEqual(
            self.diagnostic(rejected)['rejection_code'],
            'daily_off_requirement',
        )

        relocatable = self.occurrence('relocatable', 2, 'tue_am1')
        relocated = self.plan(
            (relocatable,),
            fixed_assignments=self.fixed(relocatable, self.first),
        )
        tuesday_off = next(
            reservation for reservation in relocated['off_reservations']
            if reservation['day_key'] == 'Tue'
        )
        self.assertNotEqual(tuesday_off['slot_key'], 'tue_am1')

    def test_future_fixed_reservation_prevents_automatic_overlap(self):
        occurrences = (
            self.occurrence('automatic', 1, 'mon_pm1'),
            self.occurrence('fixed', 2, 'mon_pm1', group_index=1),
        )

        result = self.plan(
            occurrences,
            instructors=(self.first, self.second),
            fixed_assignments=self.fixed(occurrences[1], self.first),
        )

        self.assertEqual(self.assignment_map(result), {
            'automatic': self.second,
            'fixed': self.first,
        })

    def test_missing_occurrence_and_unsupported_count_are_rejected(self):
        occurrence = self.occurrence('current', 1, 'mon_pm1')
        missing = self.occurrence('missing', 2, 'mon_pm2')
        missing_result = self.plan(
            (occurrence,),
            fixed_assignments=self.fixed(missing, self.first),
        )
        self.assertEqual(
            self.diagnostic(missing_result)['rejection_code'],
            'occurrence_not_found',
        )

        occurrence['required_instructor_count'] = 2
        unsupported = self.plan(
            (occurrence,),
            fixed_assignments=self.fixed(occurrence, self.first),
        )
        self.assertEqual(
            self.diagnostic(unsupported)['rejection_code'],
            'unsupported_instructor_count',
        )

    def test_fixed_results_are_deterministic_and_service_is_read_only(self):
        occurrences = (
            self.occurrence('first', 1, 'mon_pm1'),
            self.occurrence('second', 2, 'mon_pm2'),
        )
        fixed = self.fixed(occurrences[0], self.second)
        schedule_before = deepcopy(self.schedule.sched_data)
        counts_before = (
            Instructor.objects.count(),
            InstructorScheduleAvailability.objects.count(),
            InstructorScheduleParticipation.objects.count(),
        )

        first = self.plan(
            occurrences,
            instructors=(self.first, self.second),
            fixed_assignments=fixed,
        )
        second = self.plan(
            tuple(reversed(occurrences)),
            instructors=(self.second, self.first),
            fixed_assignments=fixed,
        )

        self.assertEqual(first, second)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data, schedule_before)
        self.assertEqual(counts_before, (
            Instructor.objects.count(),
            InstructorScheduleAvailability.objects.count(),
            InstructorScheduleParticipation.objects.count(),
        ))

    def test_nonfixed_planner_still_maximizes_coverage_over_continuity(self):
        occurrences = (
            self.occurrence('fixed', 1, 'mon_pm1'),
            self.occurrence('open', 2, 'mon_pm1', group_index=1),
            self.occurrence('scarce', 3, 'mon_pm2', group_index=1),
        )
        certifications = {
            self.first.pk: {10},
            self.second.pk: set(),
        }
        requirements = {3: {10}}

        result = self.plan(
            occurrences,
            instructors=(self.first, self.second),
            certifications=certifications,
            requirements=requirements,
            fixed_assignments=self.fixed(occurrences[0], self.second),
        )

        self.assertEqual(result['coverage']['assigned_occurrence_count'], 3)
        self.assertEqual(self.assignment_map(result)['scarce'], self.first)

    def test_fixed_assignment_advances_chronological_continuity_state(self):
        occurrences = (
            self.occurrence('fixed', 1, 'mon_pm1'),
            self.occurrence('following', 2, 'mon_pm2'),
        )

        result = self.plan(
            occurrences,
            instructors=(self.first, self.second),
            fixed_assignments=self.fixed(occurrences[0], self.second),
        )

        self.assertEqual(self.assignment_map(result), {
            'fixed': self.second,
            'following': self.second,
        })

    def test_multiple_fixed_assignments_are_honored_deterministically(self):
        occurrences = (
            self.occurrence('first', 1, 'mon_pm1'),
            self.occurrence('second', 2, 'mon_pm2'),
            self.occurrence('third', 3, 'tue_pm1'),
            self.occurrence('automatic', 4, 'tue_pm2'),
        )
        fixed = (
            {'occurrence': occurrences[2], 'instructor': self.first},
            {'occurrence': occurrences[0], 'instructor': self.second},
            {'occurrence': occurrences[1], 'instructor': self.second},
        )

        result = self.plan(
            tuple(reversed(occurrences)),
            instructors=(self.second, self.first),
            fixed_assignments=fixed,
        )

        self.assertEqual(self.assignment_map(result), {
            'first': self.second,
            'second': self.second,
            'third': self.first,
            'automatic': self.second,
        })
        self.assertEqual(
            [
                assignment['occurrence']['occurrence_id']
                for assignment in result['accepted_fixed_assignments']
            ],
            ['first', 'second', 'third'],
        )
        self.assertTrue(all(
            diagnostic['accepted']
            for diagnostic in result['fixed_assignment_diagnostics']
        ))

    def test_duplicate_target_and_overlapping_fixed_footprints_reject_set(self):
        first = self.occurrence('first', 1, 'mon_pm1')
        second = self.occurrence('second', 2, 'mon_pm1', group_index=1)

        duplicate = self.plan(
            (first,),
            instructors=(self.first, self.second),
            fixed_assignments=(
                {'occurrence': first, 'instructor': self.first},
                {'occurrence': first, 'instructor': self.second},
            ),
        )
        self.assertEqual(
            duplicate['fixed_assignment_diagnostics'][1]['rejection_code'],
            'duplicate_fixed_occurrence',
        )
        self.assertEqual(duplicate['accepted_fixed_assignments'], ())

        overlap = self.plan(
            (first, second),
            fixed_assignments=(
                {'occurrence': first, 'instructor': self.first},
                {'occurrence': second, 'instructor': self.first},
            ),
        )
        self.assertEqual(
            overlap['fixed_assignment_diagnostics'][1]['rejection_code'],
            'instructor_overlap',
        )
        self.assertEqual(overlap['accepted_fixed_assignments'], ())

    def test_combined_fixed_footprints_can_exhaust_daily_off(self):
        first = self.occurrence(
            'first',
            1,
            'tue_am1',
            'tue_am2',
            'tue_pm1',
        )
        second = self.occurrence('second', 2, 'tue_pm2', 'tue_night')

        result = self.plan(
            (first, second),
            fixed_assignments=(
                {'occurrence': first, 'instructor': self.first},
                {'occurrence': second, 'instructor': self.first},
            ),
        )

        self.assertFalse(all(
            diagnostic['accepted']
            for diagnostic in result['fixed_assignment_diagnostics']
        ))
        self.assertIn(
            'daily_off_requirement',
            {
                diagnostic['rejection_code']
                for diagnostic in result['fixed_assignment_diagnostics']
            },
        )

    def test_coverage_diagnostics_report_confirmation_only_for_reduction(self):
        no_reduction_occurrences = (
            self.occurrence('first', 1, 'mon_pm1'),
            self.occurrence('second', 2, 'mon_pm2'),
        )
        no_reduction = self.plan(
            no_reduction_occurrences,
            instructors=(self.first, self.second),
            fixed_assignments=self.fixed(
                no_reduction_occurrences[0],
                self.second,
            ),
        )
        self.assertGreaterEqual(self.diagnostic(no_reduction)['coverage_delta'], 0)
        self.assertFalse(self.diagnostic(no_reduction)['requires_confirmation'])

        reduction_occurrences = (
            self.occurrence('fixed', 3, 'mon_pm1', 'mon_pm2'),
            self.occurrence('simultaneous', 4, 'mon_pm1', group_index=1),
            self.occurrence('only-first', 5, 'mon_pm2'),
        )
        certifications = {
            self.first.pk: {10},
            self.second.pk: set(),
        }
        requirements = {5: {10}}
        reduction = self.plan(
            reduction_occurrences,
            instructors=(self.first, self.second),
            certifications=certifications,
            requirements=requirements,
            fixed_assignments=self.fixed(
                reduction_occurrences[0],
                self.first,
            ),
        )
        diagnostic = self.diagnostic(reduction)
        self.assertTrue(diagnostic['accepted'])
        self.assertLess(diagnostic['coverage_delta'], 0)
        self.assertTrue(diagnostic['requires_confirmation'])


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


class InstructorAssignmentScalabilityRegressionTests(TestCase):
    """Stable search-shape benchmarks for exact maximum-coverage planning."""

    SCARCE_CERTIFICATION = 901

    def setUp(self):
        self.organization = Organization.objects.create(name='Scalability Test Org')
        self.schedule = TheSched.objects.create(
            organization=self.organization,
            sched_name='Scalability Test Week',
            sched_data={},
        )

    def create_instructors(self, count):
        return [
            Instructor.objects.create(
                organization=self.organization,
                fname=f'Instructor {index}',
                lname=f'Benchmark {index}',
                ropes_lead=False,
                school_lead=False,
                cpr=True,
                firstaid='yes',
            )
            for index in range(count)
        ]

    def occurrence(self, occurrence_id, activity_id, group_index, *slot_keys):
        return {
            'schedule_id': self.schedule.pk,
            'organization_id': self.organization.pk,
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

    def unavailable(self, instructor, slot_key):
        return InstructorScheduleAvailability(
            organization_id=self.organization.pk,
            instructor_id=instructor.pk,
            schedule_id=self.schedule.pk,
            slot_key=slot_key,
            state=InstructorScheduleAvailability.UNAVAILABLE,
        )

    def slot_demands(self, occurrences):
        demands = {}
        for occurrence in occurrences:
            for slot in occurrence['slot_footprint']:
                slot_key = slot['slot_key']
                demands[slot_key] = demands.get(slot_key, 0) + 1
        return demands

    def run_plan(
        self,
        occurrences,
        instructors,
        *,
        certifications=None,
        requirements=None,
        availability=(),
        reverse_inputs=False,
    ):
        certifications = certifications or {}
        requirements = requirements or {}
        availability = list(availability)
        normalized = normalize_daily_off_requirements(
            self.organization.pk,
            self.schedule.pk,
            instructors,
            availability,
        )
        stats = {}
        started = perf_counter()
        result = plan_instructor_assignments_with_daily_off(
            list(reversed(occurrences)) if reverse_inputs else list(occurrences),
            list(reversed(instructors)) if reverse_inputs else list(instructors),
            (
                dict(reversed(tuple(certifications.items())))
                if reverse_inputs
                else certifications
            ),
            (
                dict(reversed(tuple(requirements.items())))
                if reverse_inputs
                else requirements
            ),
            availability,
            [],
            normalized,
            _search_stats=stats,
        )
        return result, stats, perf_counter() - started

    def assert_deterministic_benchmark(
        self,
        occurrences,
        instructors,
        *,
        expected_coverage,
        node_ceiling,
        certifications=None,
        requirements=None,
        availability=(),
    ):
        schedule_before = deepcopy(self.schedule.sched_data)
        database_counts_before = (
            Instructor.objects.count(),
            InstructorScheduleAvailability.objects.count(),
            InstructorScheduleParticipation.objects.count(),
        )
        first, first_stats, first_runtime = self.run_plan(
            occurrences,
            instructors,
            certifications=certifications,
            requirements=requirements,
            availability=availability,
        )
        second, second_stats, _second_runtime = self.run_plan(
            occurrences,
            instructors,
            certifications=certifications,
            requirements=requirements,
            availability=availability,
            reverse_inputs=True,
        )

        self.assertEqual(
            first['coverage']['assigned_occurrence_count'],
            expected_coverage,
        )
        self.assertEqual(first['assignments'], second['assignments'])
        self.assertEqual(first['off_reservations'], second['off_reservations'])
        self.assertEqual(first_stats, second_stats)
        self.assertLess(first_stats['explored_node_count'], node_ceiling)
        self.assertGreater(first_runtime, 0)
        self.assertNotIn('search_stats', first)

        self.assert_hard_constraints(
            first,
            instructors,
            certifications or {},
            requirements or {},
            availability,
        )
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data, schedule_before)
        self.assertEqual(database_counts_before, (
            Instructor.objects.count(),
            InstructorScheduleAvailability.objects.count(),
            InstructorScheduleParticipation.objects.count(),
        ))
        return first, first_stats, first_runtime

    def assert_hard_constraints(
        self,
        result,
        instructors,
        certifications,
        requirements,
        availability,
    ):
        occupied_cells = []
        assigned_slot_keys_by_instructor = {
            instructor.pk: set() for instructor in instructors
        }
        unavailable_cells = {
            (record.instructor_id, record.slot_key)
            for record in availability
            if record.state == InstructorScheduleAvailability.UNAVAILABLE
        }

        for assignment in result['assignments']:
            instructor = assignment['assigned_instructor']
            if instructor is None:
                continue
            occurrence = assignment['occurrence']
            self.assertTrue(
                set(requirements.get(occurrence['activity_id'], ())).issubset(
                    certifications.get(instructor.pk, ())
                )
            )
            footprint_slot_keys = {
                slot['slot_key'] for slot in occurrence['slot_footprint']
            }
            self.assertTrue(footprint_slot_keys.issubset(
                result['occupied_slot_keys_by_instructor'][instructor.pk]
            ))
            for slot_key in footprint_slot_keys:
                self.assertNotIn((instructor.pk, slot_key), unavailable_cells)
                occupied_cells.append((instructor.pk, slot_key))
                assigned_slot_keys_by_instructor[instructor.pk].add(slot_key)

        self.assertEqual(len(occupied_cells), len(set(occupied_cells)))
        for reservation in result['off_reservations']:
            self.assertNotIn(
                reservation['slot_key'],
                assigned_slot_keys_by_instructor[reservation['instructor_id']],
            )
        self.assertEqual(
            len(result['off_requirements']),
            len(instructors) * 3,
        )

    def test_complete_three_group_production_shape_remains_bounded(self):
        instructors = self.create_instructors(12)
        footprints = (
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
                f'complete-small-{group_index}-{occurrence_index}',
                100 + group_index * 10 + occurrence_index,
                group_index,
                *slot_keys,
            )
            for group_index in range(3)
            for occurrence_index, slot_keys in enumerate(footprints)
        ]

        result, stats, _runtime = self.assert_deterministic_benchmark(
            occurrences,
            instructors,
            expected_coverage=21,
            # The characterized fixture explores 253 nodes. This ceiling has
            # nearly 4x headroom while catching equal-coverage enumeration.
            node_ceiling=1000,
        )

        self.assertTrue(result['coverage']['complete'])
        self.assertEqual(stats['completed_plan_count'], 1)
        self.assertEqual(
            sum(len(item['slot_footprint']) > 1 for item in occurrences),
            6,
        )
        demands = self.slot_demands(occurrences)
        self.assertEqual(max(demands.values()), 3)
        self.assertEqual(sum(demand > 1 for demand in demands.values()), 9)

    def test_larger_complete_production_shape_remains_bounded(self):
        instructors = self.create_instructors(11)
        early_footprints = (
            ('mon_pm1',),
            ('mon_pm2',),
            ('mon_night',),
            ('tue_am1', 'tue_am2'),
            ('tue_pm1',),
            ('wed_am1', 'wed_am2'),
            ('wed_pm1',),
        )
        late_footprints = (
            ('wed_night',),
            ('tue_night',),
            ('wed_pm2',),
            ('thur_am1', 'thur_am2'),
            ('thur_pm1',),
            ('thur_pm2',),
            ('fri_am1', 'fri_am2'),
        )
        occurrences = []
        for group_index in range(8):
            footprints = early_footprints if group_index < 4 else late_footprints
            for occurrence_index, slot_keys in enumerate(footprints):
                if group_index == 4 and occurrence_index == 0:
                    slot_keys = ('mon_pm1',)
                occurrences.append(self.occurrence(
                    f'complete-large-{group_index}-{occurrence_index}',
                    300 + group_index * 10 + occurrence_index,
                    group_index,
                    *slot_keys,
                ))
        availability = (
            self.unavailable(instructors[0], 'mon_pm1'),
            self.unavailable(instructors[1], 'thur_pm1'),
        )

        result, stats, _runtime = self.assert_deterministic_benchmark(
            occurrences,
            instructors,
            availability=availability,
            expected_coverage=56,
            # The analogous local-data shape explored 558 nodes. More than 5x
            # headroom permits fixture differences without hiding a blow-up.
            node_ceiling=3000,
        )

        self.assertTrue(result['coverage']['complete'])
        self.assertLessEqual(stats['completed_plan_count'], 3)
        self.assertEqual(
            sum(len(item['slot_footprint']) > 1 for item in occurrences),
            16,
        )
        self.assertEqual(max(self.slot_demands(occurrences).values()), 5)

    def test_incomplete_three_group_near_capacity_shape_remains_bounded(self):
        instructors = self.create_instructors(2)
        occurrences = [
            self.occurrence(
                f'incomplete-main-{group_index}-{slot_index}',
                500 + group_index * 10 + slot_index,
                group_index,
                slot_key,
            )
            for group_index in range(3)
            for slot_index, slot_key in enumerate((
                'tue_am1',
                'tue_am2',
                'tue_pm1',
                'tue_pm2',
                'tue_night',
            ))
        ]
        occurrences.append(self.occurrence(
            'incomplete-main-multi',
            599,
            0,
            'wed_am1',
            'wed_am2',
        ))
        scarce_activity_id = 524
        certifications = {
            instructors[0].pk: {self.SCARCE_CERTIFICATION},
            instructors[1].pk: set(),
        }
        requirements = {
            scarce_activity_id: {self.SCARCE_CERTIFICATION},
        }
        availability = (
            self.unavailable(instructors[1], 'tue_pm2'),
        )

        result, stats, _runtime = self.assert_deterministic_benchmark(
            occurrences,
            instructors,
            certifications=certifications,
            requirements=requirements,
            availability=availability,
            expected_coverage=9,
            # Repeated characterization is near 183k nodes. The ceiling allows
            # over 2x headroom but detects a return to dangerous growth.
            node_ceiling=400000,
        )

        self.assertFalse(result['coverage']['complete'])
        self.assertEqual(result['coverage']['unstaffed_occurrence_count'], 7)
        self.assertEqual(stats['completed_plan_count'], 1)
        self.assertGreater(stats['explored_node_count'], 100000)
        self.assertEqual(max(self.slot_demands(occurrences).values()), 3)

    def test_reduced_difficult_incomplete_shape_remains_bounded(self):
        instructors = self.create_instructors(2)
        footprint_by_group = (
            (
                ('tue_am1', 'tue_am2'),
                ('tue_pm1',),
                ('tue_pm2',),
                ('tue_night',),
            ),
            (
                ('tue_am1',),
                ('tue_am2',),
                ('tue_pm1',),
                ('tue_pm2',),
                ('tue_night',),
            ),
            (
                ('tue_am1',),
                ('tue_pm1',),
                ('wed_am1', 'wed_am2'),
            ),
        )
        occurrences = [
            self.occurrence(
                f'incomplete-reduced-{group_index}-{occurrence_index}',
                700 + group_index * 10 + occurrence_index,
                group_index,
                *slot_keys,
            )
            for group_index, footprints in enumerate(footprint_by_group)
            for occurrence_index, slot_keys in enumerate(footprints)
        ]
        scarce_activity_id = 722
        certifications = {
            instructors[0].pk: {self.SCARCE_CERTIFICATION},
            instructors[1].pk: set(),
        }
        requirements = {
            scarce_activity_id: {self.SCARCE_CERTIFICATION},
        }
        availability = (
            self.unavailable(instructors[1], 'tue_pm2'),
        )

        result, stats, _runtime = self.assert_deterministic_benchmark(
            occurrences,
            instructors,
            certifications=certifications,
            requirements=requirements,
            availability=availability,
            expected_coverage=9,
            # This smaller derivative should require real backtracking without
            # approaching the main 3/16 fixture's cost.
            node_ceiling=50000,
        )

        self.assertFalse(result['coverage']['complete'])
        self.assertEqual(result['coverage']['unstaffed_occurrence_count'], 3)
        self.assertLessEqual(stats['completed_plan_count'], 3)
        self.assertGreater(stats['explored_node_count'], 1000)
        self.assertEqual(max(self.slot_demands(occurrences).values()), 3)
