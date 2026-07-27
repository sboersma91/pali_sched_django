from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from members.models import DemoSession, Organization, OrganizationMembership

from .demo_mode_switching import DemoModeSwitchError, switch_demo_mode
from .demo_scaffolding import DEMO_SCENARIO, PREPARED_DEMO_SCENARIO_VERSION
from .demo_session_provisioning import provision_clean_demo_session
from .models import (
    Course, Instructor, InstructorScheduleAvailability,
    InstructorScheduleParticipation, Locations, Schools, TheSched,
)
from .prepared_demo_provisioning import provision_prepared_demo_session
from .prepared_demo_reset import reset_prepared_demo_session
from .working_demo_scenario import (
    EXPECTED_COUNTS, WORKING_DEMO_SCENARIO_VERSION,
)


class DemoModeSwitchingTests(TestCase):
    def setUp(self):
        self.result = provision_prepared_demo_session()
        self.client.force_login(self.result.user)
        self.user_id = self.result.user.pk
        self.organization_id = self.result.organization.pk
        self.session_id = self.result.demo_session.pk

    def post_prepared(self, version):
        return self.client.post(
            reverse('demo-start-prepared'),
            {'scenario_version': version},
        )

    def assert_ownership_preserved(self):
        self.assertEqual(get_user_model().objects.filter(pk=self.user_id).count(), 1)
        self.assertEqual(
            Organization.objects.filter(pk=self.organization_id).count(), 1
        )
        session = DemoSession.objects.get(pk=self.session_id)
        self.assertEqual(session.user_id, self.user_id)
        self.assertEqual(session.organization_id, self.organization_id)
        self.assertEqual(
            OrganizationMembership.objects.get(user_id=self.user_id).organization_id,
            self.organization_id,
        )
        self.assertEqual(
            Organization.objects.filter(
                purpose=Organization.Purpose.TEMPORARY_DEMO
            ).count(),
            1,
        )

    def assert_clean(self):
        organization = Organization.objects.get(pk=self.organization_id)
        self.assertFalse(Locations.objects.filter(organization=organization).exists())
        self.assertFalse(Course.objects.filter(organization=organization).exists())
        self.assertFalse(
            Schools._default_manager.filter(organization=organization).exists()
        )
        self.assertFalse(TheSched.objects.filter(organization=organization).exists())
        self.assertFalse(Instructor.objects.filter(organization=organization).exists())
        self.assertFalse(
            InstructorScheduleParticipation.objects.filter(
                organization=organization
            ).exists()
        )
        self.assertFalse(
            InstructorScheduleAvailability.objects.filter(
                organization=organization
            ).exists()
        )

    def test_canonical_switches_to_working_then_clean_then_canonical(self):
        working = self.post_prepared(WORKING_DEMO_SCENARIO_VERSION)
        self.assertEqual(working.status_code, 302)
        organization = Organization.objects.get(pk=self.organization_id)
        self.assertEqual(
            Locations.objects.filter(organization=organization).count(),
            EXPECTED_COUNTS['locations'],
        )
        self.assertFalse(
            Course.objects.filter(
                organization=organization,
                course_name=DEMO_SCENARIO['activities'][0]['name'],
            ).exists()
        )

        clean = self.client.post(reverse('demo-start-clean'))
        self.assertRedirects(clean, reverse('home-paid'))
        self.assert_clean()

        canonical = self.post_prepared(PREPARED_DEMO_SCENARIO_VERSION)
        self.assertEqual(canonical.status_code, 302)
        self.assertEqual(
            TheSched.objects.get(organization_id=self.organization_id).sched_name,
            DEMO_SCENARIO['schedule']['name'],
        )
        self.assert_ownership_preserved()

    def test_working_and_clean_switch_in_both_directions(self):
        self.post_prepared(WORKING_DEMO_SCENARIO_VERSION)
        self.client.post(reverse('demo-start-clean'))
        self.assert_clean()
        response = self.post_prepared(WORKING_DEMO_SCENARIO_VERSION)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            TheSched.objects.filter(organization_id=self.organization_id).count(),
            EXPECTED_COUNTS['schedules'],
        )
        self.assert_ownership_preserved()

    def test_failed_switch_rolls_back_mode_and_complete_prior_graph(self):
        before = {
            'locations': set(Locations.objects.filter(
                organization_id=self.organization_id
            ).values_list('loc_name', flat=True)),
            'courses': set(Course.objects.filter(
                organization_id=self.organization_id
            ).values_list('course_name', flat=True)),
            'schedules': set(TheSched.objects.filter(
                organization_id=self.organization_id
            ).values_list('sched_name', flat=True)),
        }
        with patch(
            'scheduler_app.demo_mode_switching.build_working_scenario',
            side_effect=RuntimeError('injected failure'),
        ):
            with self.assertRaises(DemoModeSwitchError):
                switch_demo_mode(
                    demo_session=self.result.demo_session,
                    mode=DemoSession.Mode.PREPARED,
                    scenario_version=WORKING_DEMO_SCENARIO_VERSION,
                )

        session = DemoSession.objects.get(pk=self.session_id)
        self.assertEqual(session.mode, DemoSession.Mode.PREPARED)
        self.assertEqual(session.scenario_version, PREPARED_DEMO_SCENARIO_VERSION)
        self.assertEqual(
            set(Locations.objects.filter(
                organization_id=self.organization_id
            ).values_list('loc_name', flat=True)),
            before['locations'],
        )
        self.assertEqual(
            set(Course.objects.filter(
                organization_id=self.organization_id
            ).values_list('course_name', flat=True)),
            before['courses'],
        )
        self.assertEqual(
            set(TheSched.objects.filter(
                organization_id=self.organization_id
            ).values_list('sched_name', flat=True)),
            before['schedules'],
        )

    def test_switch_does_not_touch_other_organization(self):
        other = Organization.objects.create(name='Other customer')
        marker = Locations.objects.create(
            organization=other, loc_name='Other marker', loc_short='OTHER'
        )

        self.client.post(reverse('demo-start-clean'))

        self.assertTrue(Locations.objects.filter(pk=marker.pk).exists())

    def test_prepared_reset_remains_valid_after_switching(self):
        self.post_prepared(WORKING_DEMO_SCENARIO_VERSION)
        Locations.objects.create(
            organization_id=self.organization_id,
            loc_name='Visitor-created location',
            loc_short='VIS',
        )

        reset = reset_prepared_demo_session(
            demo_session=DemoSession.objects.get(pk=self.session_id)
        )

        self.assertTrue(reset.completed)
        self.assertFalse(
            Locations.objects.filter(
                organization_id=self.organization_id,
                loc_name='Visitor-created location',
            ).exists()
        )
        self.assertEqual(
            Locations.objects.filter(organization_id=self.organization_id).count(),
            EXPECTED_COUNTS['locations'],
        )

    def test_permanent_accounts_remain_blocked(self):
        organization = Organization.objects.create(
            name='Permanent account',
            purpose=Organization.Purpose.CUSTOMER,
        )
        user = get_user_model().objects.create_user(username='permanent')
        OrganizationMembership.objects.create(user=user, organization=organization)
        self.client.force_login(user)

        clean = self.client.post(reverse('demo-start-clean'))
        prepared = self.post_prepared(WORKING_DEMO_SCENARIO_VERSION)

        self.assertEqual(clean.status_code, 403)
        self.assertEqual(prepared.status_code, 403)
        self.assertFalse(
            Organization.objects.filter(
                purpose=Organization.Purpose.TEMPORARY_DEMO
            ).exclude(pk=self.organization_id).exists()
        )
