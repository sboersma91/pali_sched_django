from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from io import StringIO

from members.models import DemoSession, Organization, OrganizationMembership

from .prepared_demo_provisioning import provision_prepared_demo_session
from .demo_session_provisioning import provision_clean_demo_session


@override_settings(DEMO_ENTRY_ENABLED=False)
class DisabledDemoEntryTests(TestCase):
    def ownership_counts(self):
        return (
            get_user_model().objects.count(),
            Organization.objects.count(),
            OrganizationMembership.objects.count(),
            DemoSession.objects.count(),
        )

    def test_landing_remains_public_and_hides_both_start_forms(self):
        response = self.client.get(reverse('demo-landing'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'New demo sessions are temporarily unavailable')
        self.assertContains(response, 'Existing active')
        self.assertContains(response, 'sessions may continue')
        self.assertNotContains(response, reverse('demo-start-clean'))
        self.assertNotContains(response, reverse('demo-start-prepared'))
        self.assertEqual(response.content.count(b'<form'), 0)

    def test_disabled_clean_and_prepared_posts_do_not_provision_or_write(self):
        before = self.ownership_counts()
        for route, service in (
            ('demo-start-clean', 'scheduler_app.demo_entry.provision_clean_demo_session'),
            (
                'demo-start-prepared',
                'scheduler_app.demo_entry.provision_prepared_demo_session',
            ),
        ):
            with self.subTest(route=route):
                with (
                    patch(service) as provision,
                    patch(
                        'scheduler_app.demo_entry.reserve_demo_provisioning_capacity'
                    ) as reserve,
                ):
                    response = self.client.post(reverse(route))
                self.assertEqual(response.status_code, 503)
                provision.assert_not_called()
                reserve.assert_not_called()
                self.assertEqual(self.ownership_counts(), before)

    def test_existing_prepared_visitor_continues_and_can_reset(self):
        with override_settings(DEMO_ENTRY_ENABLED=True):
            result = provision_prepared_demo_session()
        self.client.force_login(result.user)

        workspace = self.client.get(
            reverse('sched-detail', args=[result.schedule.pk])
        )
        reset = self.client.post(
            reverse('demo-reset-prepared'),
            {'confirm_reset': 'yes'},
        )

        self.assertEqual(workspace.status_code, 200)
        self.assertRedirects(
            reset,
            reverse('sched-detail', args=[result.schedule.pk]),
            fetch_redirect_response=False,
        )
        self.assertEqual(
            int(self.client.session['_auth_user_id']),
            result.user.pk,
        )

    def test_existing_clean_visitor_continues(self):
        with override_settings(DEMO_ENTRY_ENABLED=True):
            result = provision_clean_demo_session()
        self.client.force_login(result.user)

        response = self.client.get(reverse('home-paid'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(int(self.client.session['_auth_user_id']), result.user.pk)

    def test_expiration_and_cleanup_remain_available(self):
        with override_settings(DEMO_ENTRY_ENABLED=True):
            result = provision_clean_demo_session()
        DemoSession.objects.filter(pk=result.demo_session.pk).update(
            expires_at=timezone.now()
        )
        self.client.force_login(result.user)

        response = self.client.get(reverse('home-paid'))
        output = StringIO()
        call_command('cleanup_demo_sessions', stdout=output)

        self.assertRedirects(response, '/demo/?expired=1')
        self.assertIn(str(result.demo_session.identifier), output.getvalue())
        self.assertTrue(
            DemoSession.objects.filter(pk=result.demo_session.pk).exists()
        )

    def test_customer_access_is_not_disabled(self):
        organization = Organization.objects.create(name='Entry Switch Customer')
        user = get_user_model().objects.create_user(username='entry_switch_customer')
        OrganizationMembership.objects.create(
            user=user,
            organization=organization,
        )
        self.client.force_login(user)

        response = self.client.get(reverse('home-paid'))

        self.assertEqual(response.status_code, 200)
