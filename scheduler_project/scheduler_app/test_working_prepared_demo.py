from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse

from members.models import Organization, OrganizationMembership

from .demo_scaffolding import PREPARED_DEMO_SCENARIO_VERSION
from .models import (
    Course, Instructor, InstructorScheduleAvailability,
    InstructorScheduleParticipation, Locations, Schools, TheSched,
)
from .prepared_demo_provisioning import provision_prepared_demo_session
from .prepared_demo_reset import reset_prepared_demo_session
from .prepared_scenarios import PREPARED_SCENARIOS
from .working_demo_scenario import (
    EXPECTED_COUNTS, WORKING_DEMO_SCENARIO, WORKING_DEMO_SCENARIO_VERSION,
)


class WorkingPreparedDemoTests(TestCase):
    def test_registry_preserves_canonical_and_registers_working(self):
        self.assertIn(PREPARED_DEMO_SCENARIO_VERSION, PREPARED_SCENARIOS)
        self.assertIn(WORKING_DEMO_SCENARIO_VERSION, PREPARED_SCENARIOS)
        canonical = provision_prepared_demo_session()
        self.assertEqual(
            canonical.demo_session.scenario_version,
            PREPARED_DEMO_SCENARIO_VERSION,
        )

    def test_landing_offers_guided_working_and_clean_choices(self):
        response = self.client.get(reverse('demo-landing'))
        self.assertContains(response, 'Guided prepared demo')
        self.assertContains(response, 'Realistic working demo')
        self.assertContains(response, 'Build from a clean workspace')
        self.assertContains(response, 'value="canonical-v1"')
        self.assertContains(response, 'value="working-v1"')
        self.assertEqual(response.content.count(b'<form'), 3)

    def test_working_provisions_without_source_or_default_reads(self):
        source_names = {'Realistic Demo Source', 'Default Organization'}
        with CaptureQueriesContext(connection) as queries:
            result = provision_prepared_demo_session(
                scenario_version=WORKING_DEMO_SCENARIO_VERSION
            )
        queried_sql = ' '.join(query['sql'] for query in queries.captured_queries)
        for source_name in source_names:
            self.assertNotIn(source_name, queried_sql)
        self.assertFalse(Organization.objects.filter(
            name='Realistic Demo Source'
        ).exists())
        self._assert_graph(result.organization)

    def test_expected_schedule_outcomes_are_controlled(self):
        result = provision_prepared_demo_session(
            scenario_version=WORKING_DEMO_SCENARIO_VERSION
        )
        schedules = {
            item.sched_name: item
            for item in TheSched.objects.filter(organization=result.organization)
        }
        for name, _schools, expectation in WORKING_DEMO_SCENARIO['schedules']:
            stored = schedules[name].get_stored_generation_result()
            self.assertEqual(
                stored['generation_complete'], expectation == 'complete'
            )
            self.assertTrue(stored['has_generated_schedule'])
            if expectation == 'infeasible':
                self.assertTrue(stored['generation_runtime_diagnostics'])

    def test_reset_restores_working_without_affecting_other_organization(self):
        result = provision_prepared_demo_session(
            scenario_version=WORKING_DEMO_SCENARIO_VERSION
        )
        other = Organization.objects.create(name='Unrelated customer')
        marker = Locations.objects.create(
            organization=other, loc_name='Keep me', loc_short='KEEP'
        )
        Locations.objects.filter(
            organization=result.organization, loc_name='Acct'
        ).delete()
        Locations.objects.create(
            organization=result.organization, loc_name='Visitor location',
            loc_short='VIS',
        )

        reset = reset_prepared_demo_session(demo_session=result.demo_session)

        self.assertTrue(reset.completed)
        self._assert_graph(result.organization)
        self.assertTrue(Locations.objects.filter(pk=marker.pk).exists())

    def _assert_graph(self, organization):
        self.assertEqual(
            Locations.objects.filter(organization=organization).count(),
            EXPECTED_COUNTS['locations'],
        )
        self.assertEqual(
            Course.objects.filter(organization=organization).count(),
            EXPECTED_COUNTS['activities'],
        )
        self.assertEqual(
            Schools._default_manager.filter(organization=organization).count(),
            EXPECTED_COUNTS['schools'],
        )
        self.assertEqual(
            TheSched.objects.filter(organization=organization).count(),
            EXPECTED_COUNTS['schedules'],
        )
        self.assertEqual(
            Instructor.objects.filter(organization=organization).count(),
            EXPECTED_COUNTS['instructors'],
        )
        self.assertEqual(
            InstructorScheduleParticipation.objects.filter(
                organization=organization
            ).count(),
            EXPECTED_COUNTS['participation'],
        )
        self.assertEqual(
            InstructorScheduleAvailability.objects.filter(
                organization=organization
            ).count(),
            EXPECTED_COUNTS['availability'],
        )
        self.assertFalse(OrganizationMembership.objects.filter(
            organization=organization
        ).exclude(user=organization.demo_session.user).exists())
        self.assertEqual(
            get_user_model().objects.filter(
                organization_membership__organization=organization
            ).count(),
            1,
        )
        for schedule in TheSched.objects.filter(organization=organization):
            self.assertEqual(schedule.organization_id, organization.pk)
