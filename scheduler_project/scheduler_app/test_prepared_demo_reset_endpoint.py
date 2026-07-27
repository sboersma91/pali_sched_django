from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse

from members.models import DemoSession, Organization, OrganizationMembership

from .demo_session_provisioning import provision_clean_demo_session
from .models import Locations, TheSched
from .prepared_demo_provisioning import provision_prepared_demo_session
from .prepared_demo_reset import (
    PreparedDemoResetError,
    reset_prepared_demo_session,
)


class PreparedDemoResetEndpointTests(TestCase):
    def setUp(self):
        self.url = reverse('demo-reset-prepared')

    def login(self, result, *, client=None):
        client = client or self.client
        client.force_login(result.user)
        return client

    def test_route_is_post_only_and_csrf_protected(self):
        result = provision_prepared_demo_session()
        client = self.login(result)
        before = deepcopy(result.schedule.sched_data)

        response = client.get(self.url)

        self.assertEqual(response.status_code, 405)
        result.schedule.refresh_from_db()
        self.assertEqual(result.schedule.sched_data, before)

        csrf_client = self.login(
            result,
            client=Client(enforce_csrf_checks=True),
        )
        response = csrf_client.post(self.url, {'confirm_reset': 'yes'})
        self.assertEqual(response.status_code, 403)

    def test_prepared_workspace_shows_confirmed_post_form_only_to_target(self):
        prepared = provision_prepared_demo_session()
        client = self.login(prepared)

        response = client.get(reverse('sched-detail', args=[prepared.schedule.pk]))

        self.assertContains(response, 'Reset prepared demo')
        self.assertContains(response, f'action="{self.url}"')
        self.assertContains(response, 'method="post"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'name="confirm_reset"')
        self.assertContains(response, 'value="yes"')
        self.assertContains(response, 'required')
        self.assertNotContains(response, str(prepared.demo_session.identifier))

        clean = provision_clean_demo_session()
        clean_client = self.login(clean, client=Client())
        response = clean_client.get(
            reverse('sched-detail', args=[prepared.schedule.pk])
        )
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, 'Reset prepared demo', status_code=404)

        anonymous = Client().get(
            reverse('sched-detail', args=[prepared.schedule.pk])
        )
        self.assertEqual(anonymous.status_code, 302)

        for purpose in (
            Organization.Purpose.CUSTOMER,
            Organization.Purpose.CANONICAL_DEMO,
        ):
            with self.subTest(purpose=purpose):
                organization = Organization.objects.create(
                    name=f'Visibility {purpose}',
                    purpose=purpose,
                )
                user = get_user_model().objects.create_user(
                    username=f'visibility_{purpose}'
                )
                OrganizationMembership.objects.create(
                    user=user,
                    organization=organization,
                )
                schedule = TheSched.objects.create(
                    organization=organization,
                    sched_name=f'Visibility {purpose}',
                    sched_data={'version': 1},
                )
                permanent_client = Client()
                permanent_client.force_login(user)
                response = permanent_client.get(
                    reverse('sched-detail', args=[schedule.pk])
                )
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'Reset prepared demo')

    def test_confirmed_post_resets_once_preserves_login_and_redirects(self):
        result = provision_prepared_demo_session()
        ownership = (
            result.user.pk,
            result.organization.pk,
            result.membership.pk,
            result.demo_session.pk,
            result.demo_session.expires_at,
        )
        extra = Locations.objects.create(
            organization=result.organization,
            loc_name='Endpoint Extra',
            loc_short='EE',
        )
        dirty = deepcopy(result.schedule.sched_data)
        dirty['manual_moves'] = [{'event': 'move'}]
        dirty['manual_instructor_overrides'] = [{'event': 'assign'}]
        dirty['instructor_override_revision'] = 4
        result.schedule.sched_data = dirty
        result.schedule.save(update_fields=('sched_data',))
        client = self.login(result)

        with patch(
            'scheduler_app.views.reset_prepared_demo_session',
            wraps=reset_prepared_demo_session,
        ) as reset:
            response = client.post(
                self.url,
                {'confirm_reset': 'yes'},
            )

        reset.assert_called_once()
        self.assertRedirects(
            response,
            reverse('sched-detail', args=[result.schedule.pk]),
            fetch_redirect_response=False,
        )
        self.assertEqual(
            int(client.session['_auth_user_id']),
            result.user.pk,
        )
        result.demo_session.refresh_from_db()
        result.schedule.refresh_from_db()
        self.assertEqual(
            (
                result.user.pk,
                result.organization.pk,
                result.membership.pk,
                result.demo_session.pk,
                result.demo_session.expires_at,
            ),
            ownership,
        )
        self.assertFalse(Locations.objects.filter(pk=extra.pk).exists())
        self.assertEqual(result.schedule.sched_data['manual_moves'], [])
        self.assertEqual(result.schedule.sched_data['manual_instructor_overrides'], [])
        self.assertEqual(result.schedule.sched_data['instructor_override_revision'], 0)
        self.assertIn(
            'restored to its starting state',
            ' '.join(str(message) for message in get_messages(response.wsgi_request)),
        )

    def test_no_op_succeeds_without_generation(self):
        result = provision_prepared_demo_session()
        client = self.login(result)
        before = deepcopy(result.schedule.sched_data)

        with patch.object(
            TheSched,
            'generate_and_store_schedule',
            autospec=True,
        ) as generate:
            response = client.post(self.url, {'confirm_reset': 'yes'})

        self.assertEqual(response.status_code, 302)
        generate.assert_not_called()
        result.schedule.refresh_from_db()
        self.assertEqual(result.schedule.sched_data, before)
        self.assertIn(
            'already at its starting state',
            ' '.join(str(message) for message in get_messages(response.wsgi_request)),
        )

    def test_confirmation_is_exact_and_refusal_calls_no_service(self):
        result = provision_prepared_demo_session()
        client = self.login(result)
        for data in ({}, {'confirm_reset': 'no'}, {'confirm_reset': 'YES'}):
            with self.subTest(data=data):
                with patch(
                    'scheduler_app.views.reset_prepared_demo_session'
                ) as reset:
                    response = client.post(self.url, data)
                self.assertEqual(response.status_code, 400)
                reset.assert_not_called()

    def test_invalid_ownership_never_calls_service(self):
        customer_org = Organization.objects.create(name='Endpoint Customer')
        customer = get_user_model().objects.create_user(username='endpoint_customer')
        OrganizationMembership.objects.create(
            user=customer,
            organization=customer_org,
        )
        clean = provision_clean_demo_session()
        prepared = provision_prepared_demo_session()
        invalid_targets = (customer, clean.user)
        for user in invalid_targets:
            with self.subTest(user=user.pk):
                client = Client()
                client.force_login(user)
                with patch(
                    'scheduler_app.views.reset_prepared_demo_session'
                ) as reset:
                    response = client.post(self.url, {'confirm_reset': 'yes'})
                self.assertEqual(response.status_code, 403)
                reset.assert_not_called()

        DemoSession.objects.filter(pk=prepared.demo_session.pk).update(
            status=DemoSession.Status.FAILED
        )
        client = self.login(prepared, client=Client())
        with patch('scheduler_app.views.reset_prepared_demo_session') as reset:
            response = client.post(self.url, {'confirm_reset': 'yes'})
        self.assertIn(response.status_code, (302, 403))
        reset.assert_not_called()

    def test_posted_identifiers_are_ignored_and_cannot_select_another_visitor(self):
        target = provision_prepared_demo_session()
        other = provision_prepared_demo_session()
        target_extra = Locations.objects.create(
            organization=target.organization,
            loc_name='Target Extra',
            loc_short='TE',
        )
        other_extra = Locations.objects.create(
            organization=other.organization,
            loc_name='Other Extra',
            loc_short='OE',
        )
        client = self.login(target)

        response = client.post(
            self.url,
            {
                'confirm_reset': 'yes',
                'organization_id': other.organization.pk,
                'user_id': other.user.pk,
                'demo_session_id': other.demo_session.pk,
                'schedule_id': other.schedule.pk,
                'scenario_version': 'foreign',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Locations.objects.filter(pk=target_extra.pk).exists())
        self.assertTrue(Locations.objects.filter(pk=other_extra.pk).exists())

    def test_expected_failure_is_controlled_and_does_not_invoke_cleanup(self):
        result = provision_prepared_demo_session()
        client = self.login(result)
        with (
            patch(
                'scheduler_app.views.reset_prepared_demo_session',
                side_effect=PreparedDemoResetError('private internal identifier 123'),
            ),
            patch(
                'scheduler_app.demo_session_cleanup.cleanup_demo_session'
            ) as cleanup,
        ):
            response = client.post(self.url, {'confirm_reset': 'yes'})

        self.assertEqual(response.status_code, 503)
        self.assertNotContains(
            response,
            'private internal identifier 123',
            status_code=503,
        )
        self.assertEqual(int(client.session['_auth_user_id']), result.user.pk)
        cleanup.assert_not_called()

    def test_foreign_returned_schedule_fails_closed(self):
        target = provision_prepared_demo_session()
        foreign = provision_prepared_demo_session()
        valid_result = reset_prepared_demo_session(demo_session=target.demo_session)
        forged_result = replace(valid_result, schedule=foreign.schedule)
        client = self.login(target)

        with patch(
            'scheduler_app.views.reset_prepared_demo_session',
            return_value=forged_result,
        ):
            response = client.post(self.url, {'confirm_reset': 'yes'})

        self.assertEqual(response.status_code, 503)
        self.assertNotIn('Location', response.get('Location', ''))
        self.assertEqual(int(client.session['_auth_user_id']), target.user.pk)
