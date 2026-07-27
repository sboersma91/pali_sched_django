from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.test import TestCase, override_settings
from django.urls import reverse

from members.models import DemoSession, Organization, OrganizationMembership


NOTICE_HEADING = 'Temporary demo data'
NOTICE_PARTS = (
    'temporary demo workspace',
    'testing and evaluation',
    'real names',
    'contact details',
    'confidential school information',
    'participant data',
    'other sensitive information',
    'Demo data expires',
    'removed through scheduled maintenance',
)


class DemoPrivacyDisclosureTests(TestCase):
    def ownership_counts(self):
        return (
            get_user_model().objects.count(),
            Organization.objects.count(),
            OrganizationMembership.objects.count(),
            DemoSession.objects.count(),
        )

    def assert_notice(self, response):
        self.assertContains(response, NOTICE_HEADING)
        normalized_content = ' '.join(response.content.decode().split())
        for part in NOTICE_PARTS:
            with self.subTest(part=part):
                self.assertIn(part, normalized_content)

    @override_settings(DEMO_ENTRY_ENABLED=True)
    def test_anonymous_enabled_landing_shows_notice_before_all_forms(self):
        before = self.ownership_counts()

        response = self.client.get(reverse('demo-landing'))

        self.assertEqual(response.status_code, 200)
        self.assert_notice(response)
        content = response.content.decode()
        notice_position = content.index(NOTICE_HEADING)
        prepared_action = f'action="{reverse("demo-start-prepared")}"'
        clean_action = f'action="{reverse("demo-start-clean")}"'
        self.assertLess(notice_position, content.index(prepared_action))
        self.assertLess(notice_position, content.index(clean_action))
        self.assertEqual(content.count('<form'), 3)
        self.assertEqual(content.count('csrfmiddlewaretoken'), 3)
        self.assertEqual(self.ownership_counts(), before)

    @override_settings(DEMO_ENTRY_ENABLED=False)
    def test_disabled_landing_keeps_notice_and_unavailability_without_forms(self):
        response = self.client.get(reverse('demo-landing'))

        self.assertEqual(response.status_code, 200)
        self.assert_notice(response)
        self.assertContains(
            response,
            'New demo sessions are temporarily unavailable',
        )
        self.assertNotContains(response, reverse('demo-start-clean'))
        self.assertNotContains(response, reverse('demo-start-prepared'))
        self.assertEqual(response.content.count(b'<form'), 0)

    @override_settings(DEMO_ENTRY_ENABLED=False)
    def test_disabled_posts_remain_unavailable_after_viewing_notice(self):
        self.client.get(reverse('demo-landing'))
        before = self.ownership_counts()

        for route in ('demo-start-clean', 'demo-start-prepared'):
            with self.subTest(route=route):
                response = self.client.post(reverse(route))
                self.assertEqual(response.status_code, 503)
                self.assertEqual(self.ownership_counts(), before)

    @override_settings(DEMO_ENTRY_ENABLED=True)
    def test_notice_adds_no_acceptance_field_cookie_session_or_record(self):
        before = self.ownership_counts()
        session_count = Session.objects.count()

        response = self.client.get(reverse('demo-landing'))

        content = response.content.decode()
        self.assertNotIn('consent', content.lower())
        self.assertNotIn('acknowledg', content.lower())
        self.assertNotIn('acceptance', content.lower())
        self.assertNotIn('checkbox', content.lower())
        self.assertNotIn('sessionid', response.cookies)
        self.assertTrue(
            all('consent' not in name.lower() for name in response.cookies)
        )
        self.assertEqual(Session.objects.count(), session_count)
        self.assertEqual(self.ownership_counts(), before)

    def test_notice_avoids_inaccurate_retention_or_compliance_claims(self):
        response = self.client.get(reverse('demo-landing'))
        content = response.content.decode().lower()

        for inaccurate in (
            'deleted immediately',
            'guaranteed deleted',
            'deleted within 15 minutes',
            'compliance certified',
            'regulatory compliance',
            'encryption guaranteed',
            'no logs',
            'legally anonymous',
            'permanently anonymous',
        ):
            with self.subTest(inaccurate=inaccurate):
                self.assertNotIn(inaccurate, content)


class DemoPrivacyDocumentationTests(TestCase):
    def test_runbooks_record_implemented_notice_and_keep_deployment_gate(self):
        repository_root = Path(__file__).resolve().parents[2]
        launch = (
            repository_root / 'docs/private-beta-launch-runbook.md'
        ).read_text()
        operations = (
            repository_root / 'docs/demo-environment-runbook.md'
        ).read_text()

        self.assertNotIn('The required notice is not present', launch)
        self.assertIn('The required notice is implemented', launch)
        self.assertIn('Deployment verification remains mandatory', launch)
        self.assertIn('Required privacy notice is visible', launch)
        self.assertIn('## Temporary-data disclosure', operations)
        self.assertIn('remains visible when new demo entry is disabled', operations)
        self.assertNotIn('@example.', launch + operations)
        self.assertNotIn('postgresql://', launch + operations)
