from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth import login as django_login
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from members.models import DemoSession, Organization, OrganizationMembership

from .demo_scaffolding import DEMO_SCENARIO, PREPARED_DEMO_SCENARIO_VERSION
from .demo_session_provisioning import CleanDemoProvisioningError
from .prepared_demo_provisioning import provision_prepared_demo_session


class PreparedDemoLandingAndCsrfTests(TestCase):
    def test_landing_has_three_read_only_csrf_post_choices(self):
        before = DemoSession.objects.count()

        response = self.client.get(reverse('demo-landing'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.count(b'<form'), 3)
        self.assertContains(response, reverse('demo-start-prepared'))
        self.assertContains(response, reverse('demo-start-clean'))
        self.assertEqual(response.content.count(b'csrfmiddlewaretoken'), 3)
        self.assertContains(response, 'ready to explore')
        self.assertContains(response, 'starts empty')
        self.assertContains(response, PREPARED_DEMO_SCENARIO_VERSION)
        self.assertEqual(DemoSession.objects.count(), before)

    def test_prepared_get_is_rejected_and_missing_csrf_post_is_forbidden(self):
        before = DemoSession.objects.count()
        self.assertEqual(
            self.client.get(reverse('demo-start-prepared')).status_code,
            405,
        )
        csrf_client = Client(enforce_csrf_checks=True)
        self.assertEqual(
            csrf_client.post(reverse('demo-start-prepared')).status_code,
            403,
        )
        self.assertEqual(DemoSession.objects.count(), before)

    def test_valid_csrf_post_reaches_prepared_provisioning(self):
        client = Client(enforce_csrf_checks=True)
        landing = client.get(reverse('demo-landing'))
        token = landing.cookies['csrftoken'].value

        with patch(
            'scheduler_app.demo_entry.provision_prepared_demo_session',
            wraps=provision_prepared_demo_session,
        ) as provision:
            response = client.post(
                reverse('demo-start-prepared'),
                {'csrfmiddlewaretoken': token},
                HTTP_X_CSRFTOKEN=token,
            )

        self.assertEqual(response.status_code, 302)
        provision.assert_called_once()
        self.assertIn('admission', provision.call_args.kwargs)


class PreparedDemoEntrySuccessTests(TestCase):
    def test_entry_commits_then_logs_in_rotates_and_redirects_to_own_schedule(self):
        session = self.client.session
        session['before'] = True
        session.save()
        old_key = session.session_key
        events = []

        def provision(*, admission):
            result = provision_prepared_demo_session(admission=admission)
            events.append('provisioned')
            return result

        def login(request, user, backend):
            events.append(('login', backend))
            return django_login(request, user, backend=backend)

        with (
            patch(
                'scheduler_app.demo_entry.provision_prepared_demo_session',
                side_effect=provision,
            ) as provision_mock,
            patch('scheduler_app.demo_entry.login', side_effect=login),
            patch('django.contrib.auth.authenticate') as authenticate,
        ):
            response = self.client.post(reverse('demo-start-prepared'))

        result_session = DemoSession.objects.get()
        schedule = result_session.organization.schedules.get()
        self.assertRedirects(response, reverse('sched-detail', args=[schedule.pk]))
        self.assertEqual(events[0], 'provisioned')
        self.assertEqual(
            events[1],
            ('login', 'django.contrib.auth.backends.ModelBackend'),
        )
        provision_mock.assert_called_once()
        self.assertIn('admission', provision_mock.call_args.kwargs)
        authenticate.assert_not_called()
        self.assertNotEqual(self.client.session.session_key, old_key)
        self.assertEqual(
            int(self.client.session['_auth_user_id']),
            result_session.user_id,
        )
        self.assertNotIn('organization_id', self.client.session)
        self.assertEqual(result_session.mode, DemoSession.Mode.PREPARED)
        self.assertEqual(result_session.status, DemoSession.Status.ACTIVE)
        self.assertGreater(result_session.expires_at, timezone.now())
        self.assertEqual(
            result_session.scenario_version,
            PREPARED_DEMO_SCENARIO_VERSION,
        )
        self.assertTrue(schedule.sched_data['generation_complete'])
        self.assertTrue(schedule.sched_data['generated_schedule'])

    def test_prepared_user_can_access_workspace_assignment_and_crud(self):
        response = self.client.post(reverse('demo-start-prepared'))
        schedule = DemoSession.objects.get().organization.schedules.get()

        self.assertRedirects(response, reverse('sched-detail', args=[schedule.pk]))
        self.assertEqual(
            self.client.get(reverse('sched-detail', args=[schedule.pk])).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse('instructor-assignment-schedule', args=[schedule.pk])
            ).status_code,
            200,
        )
        self.assertEqual(self.client.get(reverse('location-list')).status_code, 200)

    def test_prepared_user_cannot_access_foreign_schedule(self):
        foreign = provision_prepared_demo_session()
        self.client.post(reverse('demo-start-prepared'))

        response = self.client.get(
            reverse('sched-detail', args=[foreign.schedule.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_repeat_post_rebuilds_same_scenario_without_new_ownership(self):
        first = self.client.post(reverse('demo-start-prepared'))
        demo_session = DemoSession.objects.get()
        original_schedule = demo_session.organization.schedules.get()
        counts = (
            get_user_model().objects.count(),
            Organization.objects.count(),
            OrganizationMembership.objects.count(),
            DemoSession.objects.count(),
        )

        with patch(
            'scheduler_app.demo_entry.provision_prepared_demo_session'
        ) as provision:
            repeated = self.client.post(reverse('demo-start-prepared'))

        rebuilt_schedule = demo_session.organization.schedules.get()
        self.assertEqual(first.status_code, 302)
        self.assertEqual(
            repeated.url,
            reverse('sched-detail', args=[rebuilt_schedule.pk]),
        )
        self.assertNotEqual(original_schedule.pk, rebuilt_schedule.pk)
        provision.assert_not_called()
        self.assertEqual(
            counts,
            (
                get_user_model().objects.count(),
                Organization.objects.count(),
                OrganizationMembership.objects.count(),
                DemoSession.objects.count(),
            ),
        )


class PreparedDemoExistingUserTests(TestCase):
    def make_member(self, name, purpose, **flags):
        organization = Organization.objects.create(
            name=f'{name} Organization',
            purpose=purpose,
        )
        user = get_user_model().objects.create_user(username=name, **flags)
        OrganizationMembership.objects.create(user=user, organization=organization)
        return user

    def test_permanent_canonical_staff_and_superuser_are_not_replaced(self):
        cases = (
            ('customer', Organization.Purpose.CUSTOMER, {}),
            ('canonical', Organization.Purpose.CANONICAL_DEMO, {}),
            ('staff', Organization.Purpose.CUSTOMER, {'is_staff': True}),
            ('superuser', Organization.Purpose.CUSTOMER, {'is_superuser': True}),
        )
        for name, purpose, flags in cases:
            with self.subTest(name=name):
                user = self.make_member(name, purpose, **flags)
                client = Client()
                client.force_login(user)
                before = DemoSession.objects.count()
                with patch(
                    'scheduler_app.demo_entry.provision_prepared_demo_session'
                ) as provision:
                    response = client.post(reverse('demo-start-prepared'))
                self.assertEqual(response.status_code, 403)
                provision.assert_not_called()
                self.assertEqual(DemoSession.objects.count(), before)
                self.assertEqual(int(client.session['_auth_user_id']), user.pk)

    def test_active_clean_user_switches_using_same_ownership(self):
        from .demo_session_provisioning import provision_clean_demo_session
        clean = provision_clean_demo_session()
        self.client.force_login(clean.user)

        with patch(
            'scheduler_app.demo_entry.provision_prepared_demo_session'
        ) as provision:
            response = self.client.post(reverse('demo-start-prepared'))

        self.assertEqual(response.status_code, 302)
        provision.assert_not_called()
        clean.demo_session.refresh_from_db()
        self.assertEqual(clean.demo_session.mode, DemoSession.Mode.PREPARED)
        self.assertEqual(
            clean.demo_session.scenario_version,
            PREPARED_DEMO_SCENARIO_VERSION,
        )
        self.assertEqual(clean.demo_session.user_id, clean.user.pk)
        self.assertEqual(
            clean.demo_session.organization_id,
            clean.organization.pk,
        )

    def test_expired_or_invalid_prepared_user_fails_closed(self):
        result = provision_prepared_demo_session()
        DemoSession.objects.filter(pk=result.demo_session.pk).update(
            expires_at=result.demo_session.created_at + timedelta(microseconds=1)
        )
        self.client.force_login(result.user)

        with patch(
            'scheduler_app.demo_entry.provision_prepared_demo_session'
        ) as provision:
            response = self.client.post(reverse('demo-start-prepared'))

        self.assertEqual(response.status_code, 403)
        provision.assert_not_called()


class PreparedDemoFailureTests(TestCase):
    def test_provisioning_failure_is_safe_and_does_not_authenticate(self):
        with patch(
            'scheduler_app.demo_entry.provision_prepared_demo_session',
            side_effect=CleanDemoProvisioningError('private identifier 987'),
        ):
            response = self.client.post(reverse('demo-start-prepared'))

        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, 'private identifier 987', status_code=503)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(DemoSession.objects.count(), 0)

    def test_login_failure_preserves_committed_prepared_unit(self):
        with patch(
            'scheduler_app.demo_entry.login',
            side_effect=RuntimeError('login failure'),
        ):
            response = self.client.post(reverse('demo-start-prepared'))

        self.assertEqual(response.status_code, 503)
        self.assertNotIn('_auth_user_id', self.client.session)
        demo_session = DemoSession.objects.get()
        self.assertEqual(demo_session.status, DemoSession.Status.ACTIVE)
        self.assertTrue(demo_session.organization.schedules.exists())


class PreparedDemoVerificationFailureTests(TestCase):
    def assert_mutation_fails_closed(self, mutation):
        result = provision_prepared_demo_session()
        expected_schedule_pk = result.schedule.pk
        client = Client()

        def login_then_mutate(request, user, backend):
            django_login(request, user, backend=backend)
            mutation(result)

        with (
            patch(
                'scheduler_app.demo_entry.provision_prepared_demo_session',
                return_value=result,
            ),
            patch(
                'scheduler_app.demo_entry.login',
                side_effect=login_then_mutate,
            ),
        ):
            response = client.post(reverse('demo-start-prepared'))

        self.assertEqual(response.status_code, 503)
        self.assertNotIn('_auth_user_id', client.session)
        self.assertNotEqual(
            response.get('Location'),
            reverse('sched-detail', args=[expected_schedule_pk]),
        )

    def test_core_ownership_mode_status_version_and_expiration_failures(self):
        mutations = (
            lambda result: result.membership.delete(),
            lambda result: OrganizationMembership.objects.filter(
                pk=result.membership.pk
            ).update(
                organization=Organization.objects.create(
                    name=f'Wrong Membership {Organization.objects.count()}'
                )
            ),
            lambda result: Organization.objects.filter(
                pk=result.organization.pk
            ).update(purpose=Organization.Purpose.CUSTOMER),
            lambda result: result.demo_session.delete(),
            lambda result: DemoSession.objects.filter(
                pk=result.demo_session.pk
            ).update(mode=DemoSession.Mode.CLEAN, scenario_version=''),
            lambda result: DemoSession.objects.filter(
                pk=result.demo_session.pk
            ).update(status=DemoSession.Status.FAILED),
            lambda result: DemoSession.objects.filter(
                pk=result.demo_session.pk
            ).update(scenario_version='wrong-version'),
            lambda result: DemoSession.objects.filter(
                pk=result.demo_session.pk
            ).update(
                expires_at=result.demo_session.created_at
                + timedelta(microseconds=1)
            ),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assert_mutation_fails_closed(mutation)

    def test_schedule_and_generated_state_failures(self):
        mutations = (
            lambda result: result.schedule.delete(),
            lambda result: TheSched.objects.filter(pk=result.schedule.pk).update(
                organization=Organization.objects.create(
                    name=f'Foreign {Organization.objects.count()}'
                )
            ),
            lambda result: self._mutate_data(result, 'generated_schedule', {}),
            lambda result: self._mutate_data(result, 'generation_complete', False),
            lambda result: self._mutate_data(result, 'manual_moves', [{'dirty': True}]),
            lambda result: self._mutate_data(
                result,
                'manual_instructor_overrides',
                [{'dirty': True}],
            ),
            lambda result: self._mutate_data(
                result,
                'instructor_override_revision',
                1,
            ),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assert_mutation_fails_closed(mutation)

    @staticmethod
    def _mutate_data(result, key, value):
        data = dict(result.schedule.sched_data)
        data[key] = value
        TheSched.objects.filter(pk=result.schedule.pk).update(sched_data=data)

    def test_wrong_authenticated_user_and_mismatched_expected_schedule_fail(self):
        result = provision_prepared_demo_session()
        other = get_user_model().objects.create_user(username='wrong-user')
        client = Client()

        def login_wrong(request, user, backend):
            django_login(request, other, backend=backend)

        with (
            patch(
                'scheduler_app.demo_entry.provision_prepared_demo_session',
                return_value=result,
            ),
            patch('scheduler_app.demo_entry.login', side_effect=login_wrong),
        ):
            response = client.post(reverse('demo-start-prepared'))
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('_auth_user_id', client.session)

        other_result = provision_prepared_demo_session()
        mismatched = replace(result, schedule=other_result.schedule)
        with patch(
            'scheduler_app.demo_entry.provision_prepared_demo_session',
            return_value=mismatched,
        ):
            response = client.post(reverse('demo-start-prepared'))
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('_auth_user_id', client.session)
