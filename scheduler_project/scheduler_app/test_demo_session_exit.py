from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from members.models import (
    DemoCapacityReservation,
    DemoOperationLease,
    DemoProvisioningAttempt,
    DemoSession,
    Organization,
    OrganizationMembership,
)

from .demo_session_cleanup import (
    cleanup_expired_demo_sessions,
    plan_demo_session_cleanup,
)
from .demo_session_exit import exit_temporary_demo_session
from .demo_session_provisioning import provision_clean_demo_session
from .models import Locations, TheSched
from .prepared_demo_provisioning import provision_prepared_demo_session


class DemoSessionExitServiceTests(TestCase):
    def test_active_clean_exit_transitions_and_shortens_expiration(self):
        result = provision_clean_demo_session()
        original_expiration = result.demo_session.expires_at
        last_activity = result.demo_session.last_activity_at
        now = timezone.now()

        exited = exit_temporary_demo_session(
            demo_session=result.demo_session,
            clock=lambda: now,
        )

        self.assertTrue(exited.transitioned)
        self.assertFalse(exited.already_exiting)
        self.assertTrue(exited.expiration_shortened)
        self.assertEqual(exited.original_status, DemoSession.Status.ACTIVE)
        self.assertEqual(exited.final_status, DemoSession.Status.EXPIRING)
        self.assertEqual(exited.demo_session.expires_at, now)
        self.assertLess(exited.demo_session.expires_at, original_expiration)
        self.assertEqual(exited.demo_session.last_activity_at, last_activity)
        self.assertTrue(Organization.objects.filter(pk=result.organization.pk).exists())
        self.assertTrue(OrganizationMembership.objects.filter(pk=result.membership.pk).exists())

    def test_expiring_exit_is_idempotent_and_never_extends_expiration(self):
        result = provision_clean_demo_session()
        earlier = result.demo_session.created_at + timedelta(microseconds=1)
        DemoSession.objects.filter(pk=result.demo_session.pk).update(
            status=DemoSession.Status.EXPIRING,
            expires_at=earlier,
        )

        exited = exit_temporary_demo_session(
            demo_session=result.demo_session,
            clock=lambda: timezone.now(),
        )

        self.assertFalse(exited.transitioned)
        self.assertTrue(exited.already_exiting)
        self.assertEqual(exited.demo_session.expires_at, earlier)

    def test_unavailable_statuses_are_not_reclassified(self):
        for status in (
            DemoSession.Status.FAILED,
            DemoSession.Status.PROVISIONING,
            DemoSession.Status.DELETING,
        ):
            with self.subTest(status=status):
                result = provision_clean_demo_session()
                original_expiration = result.demo_session.expires_at
                DemoSession.objects.filter(pk=result.demo_session.pk).update(
                    status=status
                )

                exited = exit_temporary_demo_session(
                    demo_session=result.demo_session
                )

                self.assertEqual(exited.completion_status, 'unavailable')
                self.assertEqual(exited.final_status, status)
                self.assertEqual(
                    exited.demo_session.expires_at,
                    original_expiration,
                )

    def test_prepared_exit_preserves_scenario_and_schedule(self):
        result = provision_prepared_demo_session()
        schedule_data = result.schedule.sched_data

        exited = exit_temporary_demo_session(
            demo_session=result.demo_session
        )

        result.schedule.refresh_from_db()
        self.assertEqual(exited.final_status, DemoSession.Status.EXPIRING)
        self.assertEqual(
            exited.demo_session.scenario_version,
            result.demo_session.scenario_version,
        )
        self.assertEqual(result.schedule.sched_data, schedule_data)


class DemoSessionExitEndpointTests(TestCase):
    def setUp(self):
        self.url = reverse('demo-exit')

    def login(self, result, *, client=None):
        client = client or self.client
        client.force_login(result.user)
        return client

    def test_route_is_post_only_and_csrf_protected(self):
        result = provision_clean_demo_session()
        client = self.login(result)

        response = client.get(self.url)
        self.assertEqual(response.status_code, 405)
        result.demo_session.refresh_from_db()
        self.assertEqual(result.demo_session.status, DemoSession.Status.ACTIVE)

        csrf_client = self.login(
            result,
            client=Client(enforce_csrf_checks=True),
        )
        response = csrf_client.post(self.url, {'confirm_exit': 'yes'})
        self.assertEqual(response.status_code, 403)

    def test_workspace_control_is_visible_only_to_valid_temporary_visitors(self):
        for provision in (
            provision_clean_demo_session,
            provision_prepared_demo_session,
        ):
            with self.subTest(provision=provision.__name__):
                result = provision()
                response = self.login(result, client=Client()).get(
                    reverse('home-paid')
                )
                self.assertContains(response, 'Exit demo')
                self.assertContains(response, f'action="{self.url}"')
                self.assertContains(response, 'method="post"')
                self.assertContains(response, 'name="csrfmiddlewaretoken"')
                self.assertContains(response, 'name="confirm_exit"')
                self.assertContains(response, 'value="yes"')
                self.assertContains(response, 'required')
                self.assertNotContains(
                    response,
                    str(result.demo_session.identifier),
                )

        anonymous = Client().get(reverse('home-paid'))
        self.assertEqual(anonymous.status_code, 302)

    def test_customer_canonical_and_privileged_users_are_refused_and_stay_logged_in(self):
        cases = (
            (Organization.Purpose.CUSTOMER, False, False),
            (Organization.Purpose.CANONICAL_DEMO, False, False),
            (Organization.Purpose.CUSTOMER, True, False),
            (Organization.Purpose.CUSTOMER, True, True),
        )
        for index, (purpose, is_staff, is_superuser) in enumerate(cases):
            with self.subTest(index=index):
                organization = Organization.objects.create(
                    name=f'Permanent exit refusal {index}',
                    purpose=purpose,
                )
                user = get_user_model().objects.create_user(
                    username=f'exit_refusal_{index}',
                    is_staff=is_staff,
                    is_superuser=is_superuser,
                )
                OrganizationMembership.objects.create(
                    user=user,
                    organization=organization,
                )
                client = Client()
                client.force_login(user)

                response = client.post(self.url, {'confirm_exit': 'yes'})

                self.assertEqual(response.status_code, 403)
                self.assertEqual(int(client.session['_auth_user_id']), user.pk)
                response = client.get(reverse('home-paid'))
                self.assertNotContains(response, 'Exit demo')

    def test_confirmation_is_exact_and_refusal_preserves_authentication(self):
        for confirmation in (None, 'no', 'YES', ''):
            with self.subTest(confirmation=confirmation):
                result = provision_clean_demo_session()
                client = self.login(result, client=Client())
                data = {} if confirmation is None else {'confirm_exit': confirmation}

                response = client.post(self.url, data)

                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    int(client.session['_auth_user_id']),
                    result.user.pk,
                )
                result.demo_session.refresh_from_db()
                self.assertEqual(
                    result.demo_session.status,
                    DemoSession.Status.ACTIVE,
                )

    def test_successful_exit_logs_out_redirects_and_preserves_records(self):
        result = provision_clean_demo_session()
        location = Locations.objects.create(
            organization=result.organization,
            loc_name='Exit-preserved location',
            loc_short='EPL',
        )
        client = self.login(result)

        with patch('scheduler_app.views.logout', wraps=__import__(
            'django.contrib.auth', fromlist=['logout']
        ).logout) as logout_mock:
            response = client.post(self.url, {'confirm_exit': 'yes'})

        logout_mock.assert_called_once()
        self.assertRedirects(
            response,
            reverse('demo-landing'),
            fetch_redirect_response=False,
        )
        self.assertNotIn('_auth_user_id', client.session)
        result.demo_session.refresh_from_db()
        self.assertEqual(result.demo_session.status, DemoSession.Status.EXPIRING)
        self.assertTrue(Locations.objects.filter(pk=location.pk).exists())
        self.assertTrue(get_user_model().objects.filter(pk=result.user.pk).exists())
        self.assertIn(
            'data will be removed shortly',
            ' '.join(
                str(message)
                for message in get_messages(response.wsgi_request)
            ),
        )

    def test_invalid_temporary_ownership_logs_out_without_mutation(self):
        result = provision_clean_demo_session()
        original_expiration = result.demo_session.expires_at
        OrganizationMembership.objects.filter(pk=result.membership.pk).delete()
        client = self.login(result)

        response = client.post(self.url, {'confirm_exit': 'yes'})

        self.assertEqual(response.status_code, 302)
        self.assertNotIn('_auth_user_id', client.session)
        result.demo_session.refresh_from_db()
        self.assertEqual(result.demo_session.status, DemoSession.Status.ACTIVE)
        self.assertEqual(result.demo_session.expires_at, original_expiration)

    def test_unavailable_lifecycle_statuses_log_out_without_reclassification(self):
        for status in (
            DemoSession.Status.FAILED,
            DemoSession.Status.PROVISIONING,
            DemoSession.Status.DELETING,
        ):
            with self.subTest(status=status):
                result = provision_clean_demo_session()
                DemoSession.objects.filter(pk=result.demo_session.pk).update(
                    status=status
                )
                client = self.login(result, client=Client())

                response = client.post(self.url, {'confirm_exit': 'yes'})

                self.assertEqual(response.status_code, 302)
                self.assertNotIn('_auth_user_id', client.session)
                result.demo_session.refresh_from_db()
                self.assertEqual(result.demo_session.status, status)

    def test_missing_session_for_temporary_membership_logs_out_safely(self):
        result = provision_clean_demo_session()
        DemoSession.objects.filter(pk=result.demo_session.pk).delete()
        client = self.login(result)

        response = client.post(self.url, {'confirm_exit': 'yes'})

        self.assertEqual(response.status_code, 302)
        self.assertNotIn('_auth_user_id', client.session)
        self.assertTrue(Organization.objects.filter(pk=result.organization.pk).exists())
        self.assertTrue(get_user_model().objects.filter(pk=result.user.pk).exists())

    def test_posted_foreign_identifiers_cannot_change_exit_target(self):
        target = provision_clean_demo_session()
        other = provision_prepared_demo_session()
        client = self.login(target)

        client.post(self.url, {
            'confirm_exit': 'yes',
            'user_id': other.user.pk,
            'organization_id': other.organization.pk,
            'membership_id': other.membership.pk,
            'demo_session_id': other.demo_session.pk,
            'schedule_id': other.schedule.pk,
            'mode': DemoSession.Mode.PREPARED,
            'scenario_version': other.demo_session.scenario_version,
        })

        target.demo_session.refresh_from_db()
        other.demo_session.refresh_from_db()
        self.assertEqual(target.demo_session.status, DemoSession.Status.EXPIRING)
        self.assertEqual(other.demo_session.status, DemoSession.Status.ACTIVE)

    @override_settings(DEMO_ENTRY_ENABLED=False)
    def test_exit_remains_available_when_entry_is_disabled(self):
        result = provision_clean_demo_session()
        client = self.login(result)

        response = client.post(
            self.url,
            {'confirm_exit': 'yes'},
            follow=True,
        )

        self.assertContains(response, 'data will be removed shortly')
        self.assertContains(response, 'New demo sessions are temporarily unavailable')
        self.assertNotIn('_auth_user_id', client.session)

    def test_exit_does_not_consume_capacity_throttle_or_lease_resources(self):
        result = provision_prepared_demo_session()
        counts = (
            DemoProvisioningAttempt.objects.count(),
            DemoCapacityReservation.objects.count(),
            DemoOperationLease.objects.count(),
        )
        client = self.login(result)

        client.post(self.url, {'confirm_exit': 'yes'})

        self.assertEqual(
            (
                DemoProvisioningAttempt.objects.count(),
                DemoCapacityReservation.objects.count(),
                DemoOperationLease.objects.count(),
            ),
            counts,
        )

    def test_exit_is_cleanup_eligible_but_does_not_delete_synchronously(self):
        result = provision_clean_demo_session()
        other = provision_clean_demo_session()
        client = self.login(result)

        client.post(self.url, {'confirm_exit': 'yes'})

        result.demo_session.refresh_from_db()
        cutoff = result.demo_session.expires_at
        plan = plan_demo_session_cleanup(cutoff=cutoff)
        item = next(
            item for item in plan.items
            if item.session_id == str(result.demo_session.identifier)
        )
        self.assertEqual(item.category, 'eligible')
        self.assertTrue(Organization.objects.filter(pk=result.organization.pk).exists())

        cleanup_expired_demo_sessions(plan=plan)

        self.assertFalse(Organization.objects.filter(pk=result.organization.pk).exists())
        self.assertTrue(Organization.objects.filter(pk=other.organization.pk).exists())
        other.demo_session.refresh_from_db()
        self.assertEqual(other.demo_session.status, DemoSession.Status.ACTIVE)

    def test_repeated_exit_does_not_recreate_authentication_or_records(self):
        result = provision_clean_demo_session()
        counts = (
            get_user_model().objects.count(),
            Organization.objects.count(),
            OrganizationMembership.objects.count(),
            DemoSession.objects.count(),
        )
        client = self.login(result)

        first = client.post(self.url, {'confirm_exit': 'yes'})
        second = client.post(self.url, {'confirm_exit': 'yes'})

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertNotIn('_auth_user_id', client.session)
        self.assertEqual(
            (
                get_user_model().objects.count(),
                Organization.objects.count(),
                OrganizationMembership.objects.count(),
                DemoSession.objects.count(),
            ),
            counts,
        )
