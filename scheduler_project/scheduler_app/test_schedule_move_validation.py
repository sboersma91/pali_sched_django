from copy import deepcopy

from django.test import SimpleTestCase

from .schedule_blocks import SCHEDULE_BLOCK_KEYS
from .schedule_cells import generated_schedule_cell
from .schedule_move_validation import validate_schedule_move


class ScheduleMoveValidationTests(SimpleTestCase):
    def schedule(self):
        return {key: ['empty'] for key in SCHEDULE_BLOCK_KEYS}

    def assignment_cell(self, assignment_id='assignment-1', part=1, span=1, activity_name='Archery'):
        return generated_schedule_cell(
            activity_name=activity_name,
            activity_id=12,
            location_name='Range',
            location_id=4,
            assignment_id=assignment_id,
            assignment_part=part,
            assignment_span=span,
        )

    def test_valid_one_block_move_is_pure(self):
        schedule = self.schedule()
        schedule['mon_pm1'][0] = self.assignment_cell()
        original = deepcopy(schedule)

        result = validate_schedule_move(schedule, 'mon_pm1', 0, 'tue_am1', 0)

        self.assertTrue(result['valid'])
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['assignment_id'], 'assignment-1')
        self.assertEqual(result['destination_cells'], [{'block_key': 'tue_am1', 'row_index': 0}])
        self.assertEqual(schedule, original)

    def test_valid_two_block_move_links_both_parts(self):
        schedule = self.schedule()
        schedule['mon_pm1'][0] = self.assignment_cell(part=1, span=2)
        schedule['mon_pm2'][0] = self.assignment_cell(part=2, span=2)

        result = validate_schedule_move(schedule, 'mon_pm2', 0, 'tue_am1', 0)

        self.assertTrue(result['valid'])
        self.assertEqual([cell['assignment_part'] for cell in result['source_cells']], [1, 2])
        self.assertEqual(
            result['destination_cells'],
            [{'block_key': 'tue_am1', 'row_index': 0}, {'block_key': 'tue_am2', 'row_index': 0}],
        )

    def test_missing_source_cell_is_rejected(self):
        result = validate_schedule_move(self.schedule(), 'missing', 0, 'tue_am1', 0)
        self.assertIn('Source cell does not exist.', result['errors'])

    def test_placeholder_and_legacy_string_sources_are_rejected(self):
        schedule = self.schedule()
        empty_result = validate_schedule_move(schedule, 'mon_pm1', 0, 'tue_am1', 0)
        schedule['mon_pm1'][0] = 'g_box'
        unavailable_result = validate_schedule_move(schedule, 'mon_pm1', 0, 'tue_am1', 0)
        schedule['mon_pm1'][0] = 'Archery'
        legacy_result = validate_schedule_move(schedule, 'mon_pm1', 0, 'tue_am1', 0)

        self.assertIn('Placeholder cells cannot be moved as assignments.', empty_result['errors'])
        self.assertIn('Placeholder cells cannot be moved as assignments.', unavailable_result['errors'])
        self.assertIn('Legacy string assignment cells cannot be moved.', legacy_result['errors'])

    def test_incomplete_or_invalid_linked_assignment_is_rejected(self):
        schedule = self.schedule()
        schedule['mon_pm1'][0] = self.assignment_cell(part=1, span=2)
        result = validate_schedule_move(schedule, 'mon_pm1', 0, 'tue_am1', 0)
        self.assertIn('All linked assignment parts must move together and have valid assignment parts.', result['errors'])

    def test_mismatched_linked_assignment_span_is_rejected(self):
        schedule = self.schedule()
        schedule['mon_pm1'][0] = self.assignment_cell(part=1, span=2)
        schedule['mon_pm2'][0] = self.assignment_cell(part=2, span=1)
        result = validate_schedule_move(schedule, 'mon_pm1', 0, 'tue_am1', 0)
        self.assertIn('Linked assignment cells must have the same assignment_span.', result['errors'])

    def test_destination_must_exist_and_be_empty(self):
        missing_schedule = self.schedule()
        missing_schedule['mon_pm1'][0] = self.assignment_cell()
        del missing_schedule['tue_am1']
        missing_result = validate_schedule_move(missing_schedule, 'mon_pm1', 0, 'tue_am1', 0)

        occupied_schedule = self.schedule()
        occupied_schedule['mon_pm1'][0] = self.assignment_cell()
        occupied_schedule['tue_am1'][0] = self.assignment_cell('other')
        occupied_result = validate_schedule_move(occupied_schedule, 'mon_pm1', 0, 'tue_am1', 0)

        self.assertIn('Destination cell tue_am1 does not exist.', missing_result['errors'])
        self.assertIn('Destination cell tue_am1 is not empty.', occupied_result['errors'])

    def test_unavailable_destination_is_rejected(self):
        schedule = self.schedule()
        schedule['mon_pm1'][0] = self.assignment_cell()
        schedule['tue_am1'][0] = 'g_box'
        result = validate_schedule_move(schedule, 'mon_pm1', 0, 'tue_am1', 0)
        self.assertIn('Destination cell tue_am1 is unavailable for this group.', result['errors'])

    def test_assignment_span_must_fit_supported_destination_blocks(self):
        schedule = self.schedule()
        schedule['mon_pm1'][0] = self.assignment_cell(part=1, span=2)
        schedule['mon_pm2'][0] = self.assignment_cell(part=2, span=2)
        result = validate_schedule_move(schedule, 'mon_pm1', 0, 'tue_am2', 0)
        self.assertIn('Destination blocks do not support this assignment span.', result['errors'])

    def test_daytime_and_night_assignments_cannot_cross_block_kinds(self):
        daytime_schedule = self.schedule()
        daytime_schedule['mon_pm1'][0] = self.assignment_cell()
        daytime_result = validate_schedule_move(daytime_schedule, 'mon_pm1', 0, 'tue_night', 0)

        night_schedule = self.schedule()
        night_schedule['mon_night'][0] = self.assignment_cell(activity_name='Night Hike')
        night_result = validate_schedule_move(night_schedule, 'mon_night', 0, 'tue_am1', 0)

        self.assertIn('Daytime assignments must remain in daytime blocks.', daytime_result['errors'])
        self.assertIn('Night assignments must remain in night blocks.', night_result['errors'])
