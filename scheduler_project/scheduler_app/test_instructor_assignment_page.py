from copy import deepcopy
from dataclasses import asdict
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.template.loader import get_template
from django.test import TestCase, override_settings
from django.urls import reverse

from members.models import Organization, OrganizationMembership

from .instructor_assignment import run_instructor_assignment
from .instructor_assignment_presentation import (
    ASSIGNMENT_CELL_ADMIN_TIME,
    ASSIGNMENT_CELL_ASSIGNED,
    ASSIGNMENT_CELL_OFF,
    ASSIGNMENT_CELL_STATES,
    ASSIGNMENT_CELL_UNAVAILABLE,
    build_instructor_assignment_presentation,
)
from .group_colors import (
    DEFAULT_GROUP_ACCENT_CLASS,
    group_accent_class,
)
from .models import (
    Course,
    Instructor,
    InstructorScheduleAvailability,
    InstructorScheduleParticipation,
    TheSched,
)
from .schedule_blocks import SCHEDULE_SLOT_KEYS


class AssignmentPageTestMixin:
    def create_instructor(self, first, last='Instructor', organization=None):
        return Instructor.objects.create(
            organization=organization or self.organization,
            fname=first,
            lname=last,
            ropes_lead=False,
            school_lead=False,
            cpr=True,
            firstaid='yes',
        )

    def create_schedule(self, generated=None, name='Assignment Display Week'):
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
            organization=self.organization,
            sched_name=name,
            sched_data=sched_data,
        )

    def participate(self, instructor, schedule=None, state='participating'):
        return InstructorScheduleParticipation.objects.create(
            organization=self.organization,
            instructor=instructor,
            schedule=schedule or self.schedule,
            state=state,
        )

    def occurrence(
        self,
        activity_name,
        occurrence_id,
        *slot_keys,
        group='School 0',
        group_index=0,
    ):
        return {
            'schedule_id': self.schedule.pk,
            'organization_id': self.organization.pk,
            'activity_id': 1,
            'activity_display_name': activity_name,
            'group_index': group_index,
            'group_label': group,
            'occurrence_id': occurrence_id,
            'slot_footprint': [
                {
                    'block_id': f'{group_index}:{slot_key}',
                    'slot_key': slot_key,
                    'slot_label': slot_key.upper(),
                    'position': position,
                }
                for position, slot_key in enumerate(slot_keys, start=1)
            ],
        }


class GroupAccentClassTests(TestCase):
    def test_group_indexes_map_to_repeating_four_color_palette(self):
        expected = {
            0: 'schedule-row-accent-1',
            1: 'schedule-row-accent-2',
            2: 'schedule-row-accent-3',
            3: 'schedule-row-accent-4',
            4: 'schedule-row-accent-1',
            17: 'schedule-row-accent-2',
        }

        for group_index, accent_class in expected.items():
            with self.subTest(group_index=group_index):
                self.assertEqual(group_accent_class(group_index), accent_class)

    def test_missing_or_invalid_group_indexes_use_safe_default(self):
        for invalid_index in (None, '', '1', -1, 1.5, True):
            with self.subTest(group_index=invalid_index):
                self.assertEqual(
                    group_accent_class(invalid_index),
                    DEFAULT_GROUP_ACCENT_CLASS,
                )

    def test_mapping_has_no_group_label_input(self):
        self.assertEqual(group_accent_class(0), group_accent_class(4))

    def test_both_schedule_templates_consume_shared_palette(self):
        activity_source = get_template(
            'pay_end/sched_detail.html'
        ).template.source
        instructor_source = get_template(
            'pay_end/instructor_assignment_schedule.html'
        ).template.source

        for source in (activity_source, instructor_source):
            self.assertIn("{% include 'pay_end/_group_accent_palette.html' %}", source)
        self.assertNotIn('{% cycle', activity_source)
        self.assertIn('{{ row.group_accent_class }}', activity_source)
        self.assertIn('{{ cell.group_accent_class }}', instructor_source)


class InstructorAssignmentPresentationTests(AssignmentPageTestMixin, TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Presentation Org')
        self.schedule = self.create_schedule()
        self.first = self.create_instructor('Avery')
        self.second = self.create_instructor('Blake')

    def result(self, assignments=(), candidates=None, **planning_fields):
        result = {
            'schedule_id': self.schedule.pk,
            'schedule_name': self.schedule.sched_name,
            'organization_id': self.organization.pk,
            'candidate_instructors': tuple(candidates or (self.first, self.second)),
            'occurrences': [assignment['occurrence'] for assignment in assignments],
            'assignments': list(assignments),
        }
        result.update(planning_fields)
        return result

    def assignment(
        self,
        instructor,
        occurrence,
        status='assigned',
        reason=None,
        rejections=(),
        planning_diagnostics=(),
    ):
        return {
            'occurrence': occurrence,
            'assigned_instructor': instructor,
            'status': status,
            'reason': reason,
            'constraint_rejections': list(rejections),
            'planning_diagnostics': tuple(planning_diagnostics),
        }

    def test_all_candidates_receive_complete_canonical_rows(self):
        presentation = build_instructor_assignment_presentation(self.result())

        self.assertEqual(
            [row.instructor_id for row in presentation.instructor_rows],
            [self.first.pk, self.second.pk],
        )
        self.assertEqual(
            [slot.key for slot in presentation.slot_headers],
            list(SCHEDULE_SLOT_KEYS),
        )
        self.assertTrue(all(
            len(row.cells) == len(SCHEDULE_SLOT_KEYS)
            and all(cell.is_empty for cell in row.cells)
            for row in presentation.instructor_rows
        ))
        self.assertTrue(all(
            cell.state == ASSIGNMENT_CELL_ADMIN_TIME
            for row in presentation.instructor_rows
            for cell in row.cells
        ))

    def test_single_slot_assignment_appears_in_correct_cell(self):
        occurrence = self.occurrence('Archery', 'occurrence:0:mon_pm1', 'mon_pm1')
        presentation = build_instructor_assignment_presentation(self.result([
            self.assignment(self.first, occurrence)
        ]))
        cells = {cell.slot_key: cell for cell in presentation.instructor_rows[0].cells}

        self.assertEqual(cells['mon_pm1'].activity_name, 'Archery')
        self.assertEqual(cells['mon_pm1'].group_index, 0)
        self.assertEqual(cells['mon_pm1'].group_label, 'School 0')
        self.assertEqual(
            cells['mon_pm1'].group_accent_class,
            'schedule-row-accent-1',
        )
        self.assertFalse(cells['mon_pm1'].is_empty)
        self.assertEqual(cells['mon_pm1'].state, ASSIGNMENT_CELL_ASSIGNED)
        self.assertTrue(cells['mon_pm2'].is_empty)
        self.assertEqual(cells['mon_pm2'].state, ASSIGNMENT_CELL_ADMIN_TIME)
        self.assertIsNone(cells['mon_pm2'].group_index)
        self.assertIsNone(cells['mon_pm2'].group_accent_class)

    def test_group_index_controls_accent_when_labels_are_identical(self):
        first_occurrence = self.occurrence(
            'Archery',
            'occurrence:0:mon_pm1',
            'mon_pm1',
            group='Duplicate Label',
            group_index=0,
        )
        second_occurrence = self.occurrence(
            'Canoeing',
            'occurrence:1:mon_pm2',
            'mon_pm2',
            group='Duplicate Label',
            group_index=1,
        )
        presentation = build_instructor_assignment_presentation(self.result([
            self.assignment(self.first, first_occurrence),
            self.assignment(self.first, second_occurrence),
        ]))
        cells = {cell.slot_key: cell for cell in presentation.instructor_rows[0].cells}

        self.assertEqual(cells['mon_pm1'].group_accent_class, 'schedule-row-accent-1')
        self.assertEqual(cells['mon_pm2'].group_accent_class, 'schedule-row-accent-2')

    def test_multi_slot_assignment_repeats_with_shared_occurrence_identity(self):
        occurrence = self.occurrence(
            'Climbing', 'occurrence:0:tue_am1', 'tue_am1', 'tue_am2'
        )
        presentation = build_instructor_assignment_presentation(self.result([
            self.assignment(self.first, occurrence)
        ]))
        cells = {cell.slot_key: cell for cell in presentation.instructor_rows[0].cells}

        for slot_key in ('tue_am1', 'tue_am2'):
            self.assertEqual(cells[slot_key].activity_name, 'Climbing')
            self.assertEqual(cells[slot_key].occurrence_id, 'occurrence:0:tue_am1')
            self.assertTrue(cells[slot_key].is_multi_slot)
            self.assertEqual(
                cells[slot_key].group_accent_class,
                'schedule-row-accent-1',
            )
        self.assertEqual(cells['tue_am1'].footprint_position, 1)
        self.assertEqual(cells['tue_am2'].footprint_position, 2)

    def test_four_states_are_explicit_and_nonactivity_states_have_no_activity_metadata(self):
        occurrence = self.occurrence('Archery', 'occurrence:0:mon_pm1', 'mon_pm1')
        presentation = build_instructor_assignment_presentation(self.result(
            [self.assignment(self.first, occurrence)],
            off_reservations=({
                'instructor_id': self.first.pk,
                'day_key': 'Tue',
                'slot_key': 'tue_night',
            },),
            unavailable_slot_keys_by_instructor={
                self.first.pk: ('wed_am1', 'fri_am1'),
                self.second.pk: (),
            },
        ))
        cells = {cell.slot_key: cell for cell in presentation.instructor_rows[0].cells}

        self.assertEqual(cells['mon_pm1'].state, ASSIGNMENT_CELL_ASSIGNED)
        self.assertEqual(cells['tue_night'].state, ASSIGNMENT_CELL_OFF)
        self.assertEqual(cells['wed_am1'].state, ASSIGNMENT_CELL_UNAVAILABLE)
        self.assertEqual(cells['mon_pm2'].state, ASSIGNMENT_CELL_ADMIN_TIME)
        self.assertEqual(
            {cell.state for row in presentation.instructor_rows for cell in row.cells},
            ASSIGNMENT_CELL_STATES,
        )
        for slot_key in ('tue_night', 'wed_am1', 'mon_pm2'):
            cell = cells[slot_key]
            self.assertIsNone(cell.activity_name)
            self.assertIsNone(cell.group_index)
            self.assertIsNone(cell.group_label)
            self.assertIsNone(cell.group_accent_class)
            self.assertIsNone(cell.occurrence_id)

    def test_unavailability_satisfaction_has_no_implicit_off_cell(self):
        presentation = build_instructor_assignment_presentation(self.result(
            off_reservations=(),
            unavailable_slot_keys_by_instructor={
                self.first.pk: ('tue_am1', 'tue_pm2'),
                self.second.pk: (),
            },
        ))
        cells = presentation.instructor_rows[0].cells

        self.assertEqual(
            [cell.slot_key for cell in cells if cell.state == ASSIGNMENT_CELL_UNAVAILABLE],
            ['tue_am1', 'tue_pm2'],
        )
        self.assertFalse(any(cell.state == ASSIGNMENT_CELL_OFF for cell in cells))

    def test_multiple_instructors_may_share_off_slot_without_counting_as_work(self):
        third = self.create_instructor('Casey')
        occurrence = self.occurrence('Archery', 'occurrence:0:mon_pm1', 'mon_pm1')
        presentation = build_instructor_assignment_presentation(self.result(
            [self.assignment(third, occurrence)],
            candidates=(self.first, self.second, third),
            off_reservations=(
                {'instructor_id': self.first.pk, 'day_key': 'Tue', 'slot_key': 'tue_am1'},
                {'instructor_id': self.second.pk, 'day_key': 'Tue', 'slot_key': 'tue_am1'},
            ),
        ))

        self.assertEqual(
            [row.instructor_id for row in presentation.instructor_rows],
            [third.pk, self.first.pk, self.second.pk],
        )
        for row in presentation.instructor_rows[1:]:
            self.assertEqual(
                next(cell for cell in row.cells if cell.slot_key == 'tue_am1').state,
                ASSIGNMENT_CELL_OFF,
            )

    def test_completely_unassigned_participant_has_off_unavailable_and_admin_time(self):
        with self.assertNumQueries(0):
            presentation = build_instructor_assignment_presentation(self.result(
                candidates=(self.first,),
                off_reservations=(
                    {'instructor_id': self.first.pk, 'day_key': 'Tue', 'slot_key': 'tue_night'},
                    {'instructor_id': self.first.pk, 'day_key': 'Thur', 'slot_key': 'thur_am1'},
                ),
                unavailable_slot_keys_by_instructor={
                    self.first.pk: ('mon_pm2', 'wed_pm1'),
                },
            ))
        cells = {cell.slot_key: cell for cell in presentation.instructor_rows[0].cells}

        self.assertEqual(cells['tue_night'].state, ASSIGNMENT_CELL_OFF)
        self.assertEqual(cells['thur_am1'].state, ASSIGNMENT_CELL_OFF)
        self.assertEqual(cells['mon_pm2'].state, ASSIGNMENT_CELL_UNAVAILABLE)
        self.assertEqual(cells['wed_pm1'].state, ASSIGNMENT_CELL_UNAVAILABLE)
        self.assertEqual(cells['mon_pm1'].state, ASSIGNMENT_CELL_ADMIN_TIME)
        self.assertFalse(any(
            cell.state == ASSIGNMENT_CELL_ASSIGNED for cell in cells.values()
        ))

    def test_contradictory_operational_states_raise_validation_error(self):
        occurrence = self.occurrence('Archery', 'occurrence:0:tue_am1', 'tue_am1')
        assignment = self.assignment(self.first, occurrence)
        conflicts = (
            {
                'assignments': [assignment],
                'off_reservations': ({
                    'instructor_id': self.first.pk,
                    'day_key': 'Tue',
                    'slot_key': 'tue_am1',
                },),
            },
            {
                'assignments': [assignment],
                'unavailable_slot_keys_by_instructor': {
                    self.first.pk: ('tue_am1',),
                },
            },
            {
                'off_reservations': ({
                    'instructor_id': self.first.pk,
                    'day_key': 'Tue',
                    'slot_key': 'tue_am1',
                },),
                'unavailable_slot_keys_by_instructor': {
                    self.first.pk: ('tue_am1',),
                },
            },
            {
                'off_reservations': (
                    {'instructor_id': self.first.pk, 'day_key': 'Tue', 'slot_key': 'tue_am1'},
                    {'instructor_id': self.first.pk, 'day_key': 'Tue', 'slot_key': 'tue_am1'},
                ),
            },
        )

        for planning_fields in conflicts:
            with self.subTest(planning_fields=planning_fields), self.assertRaises(
                ValidationError
            ):
                assignments = planning_fields.pop('assignments', ())
                build_instructor_assignment_presentation(self.result(
                    assignments,
                    **planning_fields,
                ))

    def test_off_and_global_planning_diagnostics_are_operator_safe(self):
        occurrence = self.occurrence('Canoeing', 'occurrence:0:tue_night', 'tue_night')
        off_rejection = {
            'instructor': self.first,
            'reasons': ({
                'code': 'daily_off_requirement',
                'details': {
                    'day_key': 'Tue',
                    'affected_slot_keys': ('tue_night',),
                },
            },),
        }
        result = self.result([
            self.assignment(
                None,
                occurrence,
                status='unstaffed',
                reason='Planner result.',
                rejections=[off_rejection],
                planning_diagnostics=({'code': 'global_planning_choice'},),
            )
        ])

        details = build_instructor_assignment_presentation(
            result
        ).unstaffed_occurrences[0].rejection_details

        self.assertIn('final eligible Tue OFF slot', details[0].reason)
        self.assertEqual(details[0].affected_slots, ('Tuesday Night',))
        self.assertIn('maximum-coverage plan', details[1].reason)

    def test_unstaffed_summary_and_diagnostics_are_template_safe(self):
        occurrence = self.occurrence(
            'Canoeing', 'occurrence:0:wed_pm1', 'wed_pm1', group='School Blue'
        )
        rejection = {
            'instructor': self.first,
            'reasons': [{
                'passes': False,
                'code': 'overlapping_assignment',
                'message': 'Internal message.',
                'severity': 'blocking',
                'rule': 'no_overlapping_assignments',
                'details': {
                    'conflicting_occurrence_id': 'existing-occurrence',
                    'overlapping_slot_keys': ('wed_pm1',),
                },
            }],
        }
        presentation = build_instructor_assignment_presentation(self.result([
            self.assignment(
                None,
                occurrence,
                status='unstaffed',
                reason='No eligible instructors available.',
                rejections=[rejection],
            )
        ]))
        summary = presentation.unstaffed_occurrences[0]
        detail = summary.rejection_details[0]

        self.assertEqual(summary.activity_name, 'Canoeing')
        self.assertEqual(summary.group_label, 'School Blue')
        self.assertEqual(summary.occupied_slots, ('Wednesday PM1',))
        self.assertEqual(summary.reason, 'No eligible instructors available.')
        self.assertEqual(detail.instructor_name, str(self.first))
        self.assertEqual(detail.affected_slots, ('Wednesday PM1',))
        self.assertEqual(detail.conflicting_occurrence_id, 'existing-occurrence')
        serialized = repr(asdict(presentation))
        self.assertNotIn('Instructor:', serialized)
        self.assertNotIn('no_overlapping_assignments', serialized)
        self.assertNotIn('blocking', serialized)
        self.assertNotIn('Internal message', serialized)

    def test_adapter_is_deterministic(self):
        occurrence = self.occurrence('Archery', 'occurrence:0:mon_pm1', 'mon_pm1')
        result = self.result([self.assignment(self.first, occurrence)])

        self.assertEqual(
            build_instructor_assignment_presentation(result),
            build_instructor_assignment_presentation(result),
        )

    def test_orchestration_includes_defaults_and_excludes_opt_out_and_foreign(self):
        other_organization = Organization.objects.create(name='Foreign Presentation Org')
        activity = Course.objects.create(
            organization=self.organization,
            course_name='Archery',
            abriviation='ARCH',
            course_len=1,
        )
        self.schedule.sched_data = {
            'version': 1,
            'generated_schedule': {
                'ags': ['School 0'], 'mon_pm1': [activity.course_name]
            },
            'manual_moves': [],
            'generation_diagnostics': [],
            'generation_runtime_diagnostics': [],
            'generation_complete': True,
        }
        self.schedule.save(update_fields=['sched_data'])
        self.participate(self.first)
        self.participate(self.second, state='not_participating')
        default_participant = self.create_instructor('Default')
        self.create_instructor('Foreign', organization=other_organization)

        presentation = build_instructor_assignment_presentation(
            run_instructor_assignment(self.schedule)
        )

        self.assertEqual(
            [row.instructor_id for row in presentation.instructor_rows],
            [self.first.pk, default_participant.pk],
        )

    def test_rows_with_assignments_sort_before_empty_rows_stably(self):
        third = self.create_instructor('Casey')
        occurrence = self.occurrence('Archery', 'occurrence:0:mon_pm1', 'mon_pm1')
        result = self.result(
            [self.assignment(self.second, occurrence)],
            candidates=(self.first, self.second, third),
        )

        presentation = build_instructor_assignment_presentation(result)

        self.assertEqual(
            [row.instructor_id for row in presentation.instructor_rows],
            [self.second.pk, self.first.pk, third.pk],
        )
        self.assertTrue(all(
            cell.is_empty for cell in presentation.instructor_rows[1].cells
        ))
        self.assertTrue(all(
            cell.is_empty for cell in presentation.instructor_rows[2].cells
        ))


@override_settings(ALLOWED_HOSTS=['testserver'])
class InstructorAssignmentScheduleViewTests(AssignmentPageTestMixin, TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Assignment Page Org')
        self.other_organization = Organization.objects.create(name='Foreign Page Org')
        self.user = get_user_model().objects.create_user(
            username='assignment-viewer', password='password'
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.organization
        )
        self.course = Course.objects.create(
            organization=self.organization,
            course_name='Archery',
            abriviation='ARCH',
            course_len=1,
        )
        self.schedule = self.create_schedule({
            'ags': ['School 0'],
            'mon_pm1': [self.course.course_name],
        })
        self.foreign_schedule = TheSched.objects.create(
            organization=self.other_organization,
            sched_name='Foreign Assignment Week',
            sched_data={},
        )
        self.instructor = self.create_instructor('Avery')
        self.participate(self.instructor)
        self.url = reverse('instructor-assignment-schedule', args=[self.schedule.pk])

    def login(self):
        self.client.force_login(self.user)

    def test_authentication_and_foreign_schedule_isolation(self):
        anonymous = self.client.get(self.url)
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn(reverse('login'), anonymous['Location'])

        self.login()
        self.assertEqual(self.client.get(self.url).status_code, 200)
        foreign = self.client.get(reverse(
            'instructor-assignment-schedule', args=[self.foreign_schedule.pk]
        ))
        self.assertEqual(foreign.status_code, 404)

    def test_get_invokes_schedule_bound_orchestration(self):
        self.login()
        with patch(
            'scheduler_app.views.run_instructor_assignment',
            wraps=run_instructor_assignment,
        ) as orchestration:
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        orchestration.assert_called_once()
        self.assertEqual(orchestration.call_args.args[0].pk, self.schedule.pk)

    def test_get_is_read_only_and_does_not_persist_results(self):
        availability = InstructorScheduleAvailability.objects.create(
            organization=self.organization,
            instructor=self.instructor,
            schedule=self.schedule,
            slot_key='tue_am1',
            state='unavailable',
        )
        stored_before = deepcopy(self.schedule.sched_data)
        participation_before = list(
            InstructorScheduleParticipation.objects.values_list('pk', 'state')
        )
        availability_before = list(
            InstructorScheduleAvailability.objects.values_list('pk', 'state')
        )
        self.login()

        response = self.client.get(self.url)
        repeated_response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(repeated_response.status_code, 200)
        self.assertEqual(
            response.context['assignment_schedule'],
            repeated_response.context['assignment_schedule'],
        )
        self.schedule.refresh_from_db()
        availability.refresh_from_db()
        self.assertEqual(self.schedule.sched_data, stored_before)
        self.assertNotIn('instructor_assignments', self.schedule.sched_data)
        self.assertEqual(
            list(InstructorScheduleParticipation.objects.values_list('pk', 'state')),
            participation_before,
        )
        self.assertEqual(
            list(InstructorScheduleAvailability.objects.values_list('pk', 'state')),
            availability_before,
        )

    def test_assignment_grid_navigation_and_accessible_edit_controls_render(self):
        InstructorScheduleAvailability.objects.create(
            organization=self.organization,
            instructor=self.instructor,
            schedule=self.schedule,
            slot_key='mon_pm2',
            state='unavailable',
        )
        self.login()
        response = self.client.get(self.url)

        self.assertContains(response, 'Instructor Assignment Schedule')
        self.assertContains(response, self.schedule.sched_name)
        self.assertContains(
            response,
            '<th scope="col" rowspan="2" class="instructor-sticky-column">Instructor</th>',
            html=False,
        )
        self.assertContains(
            response,
            '<th scope="row" class="instructor-sticky-column">',
            html=False,
        )
        self.assertContains(
            response,
            '.instructor-assignment-table .instructor-sticky-column',
            html=False,
        )
        self.assertContains(response, 'position: sticky', html=False)
        self.assertContains(response, 'left: 0', html=False)
        self.assertContains(response, 'Refreshing this page recalculates assignments')
        self.assertContains(response, str(self.instructor))
        self.assertContains(response, 'Monday')
        self.assertContains(response, 'PM1')
        self.assertContains(response, self.course.course_name)
        self.assertContains(response, 'School 0')
        self.assertContains(response, '>OFF<', html=False)
        self.assertContains(response, '>Admin Time<', html=False)
        self.assertContains(response, '>Unavailable<', html=False)
        self.assertContains(response, 'data-cell-state="assigned"', html=False)
        self.assertContains(response, 'data-cell-state="off"', html=False)
        self.assertContains(response, 'data-cell-state="admin_time"', html=False)
        self.assertContains(response, 'data-cell-state="unavailable"', html=False)
        self.assertContains(
            response,
            'class="group-accent schedule-row-accent-1 instructor-assignment-target"',
            html=False,
        )
        self.assertContains(response, '--group-accent: #0d6efd', html=False)
        self.assertContains(response, reverse('sched-detail', args=[self.schedule.pk]))
        self.assertContains(response, reverse(
            'instructor-participation', args=[self.schedule.pk]
        ))
        self.assertContains(response, reverse(
            'instructor-availability', args=[self.schedule.pk]
        ))
        self.assertContains(response, 'Set a Manual Instructor Assignment')
        self.assertContains(response, 'Current staffing')
        summary = response.context['assignment_schedule'].staffing_summary
        self.assertEqual(summary.total_occurrence_count, 1)
        self.assertEqual(summary.staffed_occurrence_count, 1)
        self.assertEqual(summary.unstaffed_occurrence_count, 0)
        self.assertEqual(summary.manual_occurrence_count, 0)
        self.assertContains(
            response,
            'for="instructor-override-occurrence"',
            html=False,
        )
        self.assertContains(
            response,
            'for="instructor-override-instructor"',
            html=False,
        )
        self.assertContains(response, '<select', count=3, html=False)
        self.assertContains(response, 'name="expected_revision"', html=False)
        self.assertContains(
            response,
            reverse('instructor-override-set', args=[self.schedule.pk]),
        )
        self.assertContains(response, '>Automatic<', html=False)
        self.assertContains(
            response,
            'data-instructor-drag-source="true"',
            html=False,
        )
        occurrence = response.context['instructor_override_occurrences'][0]
        self.assertContains(
            response,
            f'id="{occurrence["anchor"]}"',
            html=False,
        )
        self.assertContains(response, 'Change instructor')
        self.assertContains(
            response,
            f'name="occurrence_token" value="{occurrence["token"]}"',
            html=False,
        )
        self.assertNotContains(response, 'data-draggable-activity', html=False)

    def test_unstaffed_section_and_expandable_diagnostics_render(self):
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
        self.login()

        response = self.client.get(self.url)

        self.assertContains(response, 'Unstaffed Occurrences')
        self.assertContains(response, second_course.course_name)
        self.assertContains(response, 'School 1')
        self.assertContains(response, 'No eligible instructors available.')
        self.assertContains(response, '<details', html=False)
        self.assertContains(response, 'Already assigned during an overlapping schedule slot')
        unstaffed = response.context[
            'assignment_schedule'
        ].unstaffed_occurrences[0]
        self.assertContains(
            response,
            f'id="{unstaffed.occurrence_anchor}"',
            html=False,
        )
        self.assertContains(
            response,
            f'name="occurrence_token" value="{unstaffed.occurrence_token}"',
            html=False,
        )
        self.assertContains(response, 'Assign instructor')

    def test_empty_schedule_and_explicit_zero_participants_are_safe(self):
        empty_schedule = self.create_schedule(name='Empty Assignment Week')
        no_participant_schedule = self.create_schedule(
            {'ags': ['School 0'], 'mon_pm1': [self.course.course_name]},
            name='No Participant Week',
        )
        InstructorScheduleParticipation.objects.create(
            organization=self.organization,
            instructor=self.instructor,
            schedule=no_participant_schedule,
            state='not_participating',
        )
        self.login()

        empty_response = self.client.get(reverse(
            'instructor-assignment-schedule', args=[empty_schedule.pk]
        ))
        no_participant_response = self.client.get(reverse(
            'instructor-assignment-schedule', args=[no_participant_schedule.pk]
        ))

        self.assertEqual(empty_response.status_code, 200)
        self.assertEqual(no_participant_response.status_code, 200)
        self.assertContains(empty_response, str(self.instructor))
        self.assertContains(no_participant_response, 'Unstaffed Occurrences')
        self.assertContains(
            no_participant_response,
            'No instructors are currently marked as participating',
        )

    def test_post_is_not_allowed(self):
        self.login()
        self.assertEqual(self.client.post(self.url).status_code, 405)

    def test_schedule_detail_links_to_assignment_page(self):
        self.login()
        response = self.client.get(reverse('sched-detail', args=[self.schedule.pk]))

        self.assertContains(response, 'View Instructor Assignment Schedule')
        self.assertContains(response, self.url)
