from copy import deepcopy

from django.test import SimpleTestCase

from .schedule_blocks import SCHEDULE_BLOCK_KEYS
from .schedule_cells import generated_schedule_cell
from .schedule_move_application import apply_schedule_move


class ScheduleMoveApplicationTests(SimpleTestCase):
    def schedule(self):
        return {key: ['empty'] for key in SCHEDULE_BLOCK_KEYS}

    def assignment_cell(self, assignment_id='assignment-1', part=1, span=1, source='generated'):
        cell = generated_schedule_cell(
            activity_name='Archery',
            activity_id=12,
            location_name='Range',
            location_id=4,
            assignment_id=assignment_id,
            assignment_part=part,
            assignment_span=span,
        )
        cell['source'] = source
        cell['future_metadata'] = 'preserved'
        return cell

    def test_valid_one_block_move_applies_to_copy(self):
        schedule = self.schedule()
        schedule['mon_pm1'][0] = self.assignment_cell()
        original = deepcopy(schedule)

        result = apply_schedule_move(schedule, 'mon_pm1', 0, 'tue_am1', 0)

        self.assertTrue(result['applied'])
        self.assertEqual(result['errors'], [])
        self.assertIsNot(result['schedule'], schedule)
        self.assertEqual(schedule, original)
        self.assertEqual(result['schedule']['mon_pm1'][0], 'empty')
        moved_cell = result['schedule']['tue_am1'][0]
        self.assertEqual(moved_cell['source'], 'manual')
        self.assertEqual(moved_cell['assignment_part'], 1)
        self.assertEqual(moved_cell['assignment_span'], 1)
        self.assertEqual(moved_cell['assignment_id'], 'assignment-1')
        self.assertEqual(moved_cell['activity_id'], 12)
        self.assertEqual(moved_cell['location_id'], 4)
        self.assertEqual(moved_cell['future_metadata'], 'preserved')

    def test_valid_two_block_move_applies_all_linked_parts(self):
        schedule = self.schedule()
        schedule['mon_pm1'][0] = self.assignment_cell(part=1, span=2)
        schedule['mon_pm2'][0] = self.assignment_cell(part=2, span=2)

        result = apply_schedule_move(schedule, 'mon_pm2', 0, 'tue_pm1', 0)

        self.assertTrue(result['applied'])
        moved_schedule = result['schedule']
        self.assertEqual(moved_schedule['mon_pm1'][0], 'empty')
        self.assertEqual(moved_schedule['mon_pm2'][0], 'empty')
        first = moved_schedule['tue_pm1'][0]
        second = moved_schedule['tue_pm2'][0]
        self.assertEqual(first['assignment_id'], second['assignment_id'])
        self.assertEqual((first['assignment_part'], second['assignment_part']), (1, 2))
        self.assertEqual((first['assignment_span'], second['assignment_span']), (2, 2))
        self.assertEqual((first['source'], second['source']), ('manual', 'manual'))

    def test_invalid_move_returns_exact_original_payload_unchanged(self):
        schedule = self.schedule()
        schedule['mon_pm1'][0] = self.assignment_cell()
        schedule['tue_am1'][0] = 'g_box'
        original = deepcopy(schedule)

        result = apply_schedule_move(schedule, 'mon_pm1', 0, 'tue_am1', 0)

        self.assertFalse(result['applied'])
        self.assertIs(result['schedule'], schedule)
        self.assertEqual(schedule, original)
        self.assertIn('Destination cell tue_am1 is unavailable for this group.', result['errors'])

    def test_legacy_string_assignment_move_is_rejected_without_mutation(self):
        schedule = self.schedule()
        schedule['mon_pm1'][0] = 'Archery'
        original = deepcopy(schedule)

        result = apply_schedule_move(schedule, 'mon_pm1', 0, 'tue_am1', 0)

        self.assertFalse(result['applied'])
        self.assertEqual(schedule, original)
        self.assertIn('Legacy string assignment cells cannot be moved.', result['errors'])

    def test_g_box_cells_outside_move_remain_unchanged(self):
        schedule = self.schedule()
        schedule['mon_pm1'][0] = self.assignment_cell()
        schedule['wed_am1'][0] = 'g_box'

        result = apply_schedule_move(schedule, 'mon_pm1', 0, 'tue_am1', 0)

        self.assertTrue(result['applied'])
        self.assertEqual(result['schedule']['wed_am1'][0], 'g_box')
