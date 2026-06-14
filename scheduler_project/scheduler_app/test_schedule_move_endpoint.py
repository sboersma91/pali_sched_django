import csv
from copy import deepcopy
from io import StringIO
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from .models import TheSched
from .schedule_blocks import SCHEDULE_BLOCK_KEYS
from .schedule_cells import generated_schedule_cell


class ScheduleMoveEndpointTests(TestCase):
    def setUp(self):
        self.schedule = TheSched.objects.create(sched_name='Move Endpoint Schedule', sched_data={})
        self.move_url = reverse('sched-move', args=[self.schedule.id])
        self.detail_url = reverse('sched-detail', args=[self.schedule.id])
        self.export_url = reverse('sched-export', args=[self.schedule.id])

    def payload(self, destination='empty'):
        payload = {key: ['g_box'] for key in SCHEDULE_BLOCK_KEYS}
        payload['ags'] = ['Move Group 0']
        payload['mon_pm1'][0] = generated_schedule_cell(
            activity_name='Archery', activity_id=12, location_name='Range', location_id=4,
            assignment_id='assignment-1', assignment_part=1, assignment_span=1,
        )
        payload['tue_am1'][0] = destination
        return payload

    def two_block_payload(self):
        payload = self.payload()
        payload['mon_pm1'][0] = generated_schedule_cell(
            activity_name='Ropes', activity_id=13, location_name='Course', location_id=5,
            assignment_id='assignment-2', assignment_part=1, assignment_span=2,
        )
        payload['mon_pm2'][0] = generated_schedule_cell(
            activity_name='Ropes', activity_id=13, location_name='Course', location_id=5,
            assignment_id='assignment-2', assignment_part=2, assignment_span=2,
        )
        payload['tue_am2'][0] = 'empty'
        return payload

    def persist_payload(self, payload=None, generation_complete=True):
        self.schedule.sched_data = {
            'mode': 'persisted', 'schedule': payload or self.payload(),
            'generation_complete': generation_complete,
        }
        self.schedule.save(update_fields=['sched_data'])

    def move_data(self):
        return {
            'source_block_key': 'mon_pm1', 'source_row_index': '0',
            'destination_block_key': 'tue_am1', 'destination_row_index': '0',
        }

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.move_url).status_code, 405)

    def test_malformed_post_does_not_mutate_sched_data(self):
        self.persist_payload()
        original = deepcopy(self.schedule.sched_data)
        response = self.client.post(self.move_url, {'source_block_key': 'mon_pm1'})

        self.assertRedirects(response, self.detail_url)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data, original)
        self.assertIn('Schedule move requires source and destination block and row values.', self.message_text(response))

    def test_non_integer_row_post_does_not_mutate_sched_data(self):
        self.persist_payload()
        original = deepcopy(self.schedule.sched_data)
        data = self.move_data()
        data['source_row_index'] = 'not-an-integer'
        response = self.client.post(self.move_url, data)

        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data, original)
        self.assertIn('Schedule move row values must be integers.', self.message_text(response))

    def test_invalid_move_does_not_mutate_sched_data(self):
        self.persist_payload(self.payload(destination='g_box'))
        original = deepcopy(self.schedule.sched_data)
        response = self.client.post(self.move_url, self.move_data())

        self.assertRedirects(response, self.detail_url)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data, original)
        self.assertIn('Schedule move was not applied:', self.message_text(response))

    def test_cross_row_move_does_not_mutate_schedule_and_displays_error(self):
        payload = self.payload()
        payload['ags'].append('Move Group 1')
        for block_key in SCHEDULE_BLOCK_KEYS:
            payload[block_key].append('g_box')
        payload['tue_am1'][1] = 'empty'
        self.persist_payload(payload)
        original = deepcopy(self.schedule.sched_data)
        data = self.move_data()
        data['destination_row_index'] = '1'

        response = self.client.post(self.move_url, data, follow=True)

        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data, original)
        self.assertContains(response, 'Assignments must remain within their activity-group row.')
        self.assertContains(response, 'class="alert alert-danger"', html=False)

    def test_detail_renders_move_controls_only_for_structured_assignments(self):
        self.persist_payload()

        response = self.client.get(self.detail_url)

        self.assertContains(response, 'class="schedule-move-form', count=1, html=False)
        self.assertContains(response, 'name="source_block_key" value="mon_pm1"', html=False)
        self.assertContains(response, 'name="destination_block_key"', count=1, html=False)
        self.assertNotContains(response, 'name="source_block_key" value="tue_am1"', html=False)

    def test_detail_move_destinations_keep_the_assignment_row(self):
        self.persist_payload()

        response = self.client.get(self.detail_url)
        movable_cell = next(
            cell
            for row in response.context['schedule_rows']
            for cell in row['cells']
            if cell['destinations']
        )

        self.assertEqual(movable_cell['row_index'], 0)
        self.assertEqual(movable_cell['destinations'], [{'key': 'tue_am1', 'label': 'Tuesday AM1'}])
        self.assertContains(response, 'name="source_row_index" value="0"', html=False)
        self.assertContains(response, 'name="destination_row_index" value="0"', html=False)

    def test_detail_exposes_schedule_cell_and_move_destination_hooks(self):
        self.persist_payload()

        response = self.client.get(self.detail_url)

        self.assertContains(response, f'data-schedule-move-url="{self.move_url}"', html=False)
        self.assertContains(response, 'data-schedule-cell', count=len(SCHEDULE_BLOCK_KEYS), html=False)
        self.assertContains(response, 'data-block-key="mon_pm1"', html=False)
        self.assertContains(response, 'data-row-index="0"', count=len(SCHEDULE_BLOCK_KEYS), html=False)
        self.assertContains(response, 'data-cell-state="assignment"', count=1, html=False)
        self.assertContains(response, 'data-assignment-id="assignment-1"', count=1, html=False)
        self.assertContains(response, 'data-assignment-span="1"', count=1, html=False)
        self.assertContains(response, 'data-assignment-part="1"', count=1, html=False)
        self.assertContains(response, 'data-cell-state="empty"', count=1, html=False)
        self.assertContains(response, 'data-cell-state="unavailable"', count=len(SCHEDULE_BLOCK_KEYS) - 2, html=False)
        self.assertContains(response, 'data-schedule-move-form', count=1, html=False)
        self.assertContains(response, 'data-move-source-block="mon_pm1"', html=False)
        self.assertContains(response, 'data-move-source-row="0"', html=False)
        self.assertContains(response, 'data-valid-destination-block="tue_am1"', html=False)
        self.assertContains(response, 'data-valid-destination-row="0"', html=False)
        self.assertContains(response, 'data-schedule-move-selection-status', count=1, html=False)
        self.assertContains(response, 'data-schedule-move-selection-message', count=1, html=False)
        self.assertContains(response, 'data-schedule-move-cancel', count=1, html=False)
        self.assertContains(response, 'Cancel selection')

    def test_detail_loads_progressive_move_enhancement_assets(self):
        self.persist_payload()

        response = self.client.get(self.detail_url)

        self.assertContains(response, 'scheduler_app/schedule_move_enhancement.css')
        self.assertContains(response, 'scheduler_app/schedule_move_enhancement.js')
        self.assertContains(response, 'class="schedule-move-form', count=1, html=False)

    def test_structured_assignment_without_destinations_shows_explanation(self):
        self.persist_payload(self.payload(destination='g_box'))

        response = self.client.get(self.detail_url)

        self.assertContains(response, 'No valid destinations', count=1)
        self.assertNotContains(response, 'class="schedule-move-form', html=False)

    def test_legacy_assignment_cell_exposes_legacy_state_hook(self):
        payload = self.payload()
        payload['wed_am1'][0] = 'Legacy Activity'
        self.persist_payload(payload)

        response = self.client.get(self.detail_url)

        self.assertContains(response, 'data-block-key="wed_am1"', html=False)
        self.assertContains(response, 'data-cell-state="legacy"', count=1, html=False)
        self.assertContains(response, 'Legacy Activity')

    def test_linked_assignment_renders_grouped_indicators_and_one_move_control(self):
        self.persist_payload(self.two_block_payload())

        response = self.client.get(self.detail_url)

        self.assertContains(response, 'class="table-primary linked-assignment-cell"', count=2, html=False)
        self.assertContains(response, 'data-assignment-id="assignment-2"', count=2, html=False)
        self.assertContains(response, 'data-assignment-span="2"', count=2, html=False)
        self.assertContains(response, 'data-assignment-part="1"', count=1, html=False)
        self.assertContains(response, 'data-assignment-part="2"', count=1, html=False)
        self.assertContains(response, 'Linked assignment · part 1 of 2')
        self.assertContains(response, 'Linked assignment · part 2 of 2')
        self.assertContains(response, 'Move linked assignment', count=1)
        self.assertContains(response, 'class="schedule-move-form', count=1, html=False)
        self.assertContains(response, 'data-schedule-move-form', count=1, html=False)
        self.assertContains(response, 'name="source_block_key" value="mon_pm1"', html=False)
        self.assertNotContains(response, 'name="source_block_key" value="mon_pm2"', html=False)
        self.assertNotContains(response, 'No valid destinations')

    def test_one_block_assignment_and_placeholders_keep_existing_rendering(self):
        self.persist_payload()

        response = self.client.get(self.detail_url)

        self.assertContains(response, '>Move</summary>', count=1, html=False)
        self.assertNotContains(response, 'linked-assignment-indicator', html=False)
        self.assertContains(response, '/////')
        self.assertContains(response, '****')

    def test_valid_move_persists_manual_payload_and_preserves_completion(self):
        self.persist_payload(generation_complete=False)
        response = self.client.post(self.move_url, self.move_data())

        self.assertRedirects(response, self.detail_url)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data['mode'], 'persisted')
        self.assertFalse(self.schedule.sched_data['generation_complete'])
        moved_schedule = self.schedule.sched_data['schedule']
        self.assertEqual(moved_schedule['mon_pm1'][0], 'empty')
        self.assertEqual(moved_schedule['tue_am1'][0]['source'], 'manual')
        self.assertEqual(moved_schedule['tue_am1'][0]['assignment_id'], 'assignment-1')
        self.assertIn('Schedule move saved.', self.message_text(response))

    def test_success_message_is_visible_after_redirect(self):
        self.persist_payload()

        response = self.client.post(self.move_url, self.move_data(), follow=True)

        self.assertContains(response, 'Schedule move saved.')
        self.assertContains(response, 'class="alert alert-success"', html=False)

    def test_error_message_is_visible_after_redirect(self):
        self.persist_payload(self.payload(destination='g_box'))

        response = self.client.post(self.move_url, self.move_data(), follow=True)

        self.assertContains(response, 'Schedule move was not applied:')
        self.assertContains(response, 'class="alert alert-danger"', html=False)

    def test_valid_move_from_live_output_creates_persisted_manual_contract(self):
        live_payload = self.payload()
        self.assertEqual(self.schedule.schedule_mode, 'generated')

        with patch.object(TheSched, 'get_schedule_output', return_value=live_payload):
            response = self.client.post(self.move_url, self.move_data())

        self.assertRedirects(response, self.detail_url)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.schedule_mode, 'persisted')
        self.assertEqual(self.schedule.sched_data['schedule']['tue_am1'][0]['source'], 'manual')

    def test_detail_and_csv_export_use_persisted_moved_result(self):
        self.persist_payload()
        self.client.post(self.move_url, self.move_data())

        detail_response = self.client.get(self.detail_url)
        export_response = self.client.get(self.export_url)
        rows = list(csv.DictReader(StringIO(export_response.content.decode())))
        moved_row = next(row for row in rows if row['Day'] == 'Tuesday' and row['Time Block'] == 'AM1')
        source_row = next(row for row in rows if row['Day'] == 'Monday' and row['Time Block'] == 'PM1')

        self.assertContains(detail_response, 'Persisted Schedule')
        self.assertContains(detail_response, 'Archery')
        self.assertEqual(moved_row['Activity'], 'Archery')
        self.assertEqual(moved_row['Location'], 'Range')
        self.assertEqual(source_row['Activity'], 'Unassigned')

    @staticmethod
    def message_text(response):
        return ' '.join(str(message) for message in get_messages(response.wsgi_request))
