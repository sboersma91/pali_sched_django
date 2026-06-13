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
