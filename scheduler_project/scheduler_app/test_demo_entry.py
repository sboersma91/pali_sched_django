from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth import login as django_login
from django.contrib.sessions.models import Session
from django.middleware.csrf import _get_new_csrf_string
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from members.models import DemoSession, Organization, OrganizationMembership

from .demo_session_provisioning import (
    CleanDemoProvisioningError,
    provision_clean_demo_session,
)
from .models import (
    Certification,
    Course,
    Instructor,
    InstructorScheduleAvailability,
    InstructorScheduleParticipation,
    Locations,
    Schools,
    TheSched,
)


class DemoLandingTests(TestCase):
    def test_public_landing_is_read_only_and_describes_clean_temporary_mode(self):
        counts = (
            get_user_model().objects.count(),
            Organization.objects.count(),
            OrganizationMembership.objects.count(),
            DemoSession.objects.count(),
        )

        response = self.client.get(reverse('demo-landing'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'temporary isolated demo')
        self.assertContains(response, 'starts empty')
        self.assertContains(response, 'may expire')
        self.assertContains(response, 'Guided prepared demo')
        self.assertContains(response, 'Realistic working demo')
        self.assertEqual(response.content.count(b'<form'), 3)
        self.assertContains(
            response,
            f'action="{reverse("demo-start-clean")}"',
        )
        self.assertContains(response, 'csrfmiddlewaretoken')
        self.assertEqual(
            counts,
            (
                get_user_model().objects.count(),
                Organization.objects.count(),
                OrganizationMembership.objects.count(),
                DemoSession.objects.count(),
            ),
        )

    def test_clean_start_rejects_get_without_creating_records(self):
        before = DemoSession.objects.count()

        response = self.client.get(reverse('demo-start-clean'))

        self.assertEqual(response.status_code, 405)
        self.assertEqual(DemoSession.objects.count(), before)

    def test_operational_dashboard_remains_login_protected(self):
        response = self.client.get(reverse('home-paid'))

        self.assertRedirects(
            response,
            f'{reverse("login")}?next={reverse("home-paid")}',
        )

    def test_landing_is_not_cached_and_refreshes_back_forward_restores(self):
        response = self.client.get(reverse('demo-landing'))

        self.assertIn('no-cache', response['Cache-Control'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('must-revalidate', response['Cache-Control'])
        self.assertContains(response, 'event.persisted')
        self.assertContains(response, "'back_forward'")
        self.assertContains(response, 'window.location.reload()')


class DemoEntryCsrfTests(TestCase):
    def test_missing_csrf_is_rejected_and_valid_csrf_reaches_provisioning(self):
        client = Client(enforce_csrf_checks=True)
        rejected = client.post(reverse('demo-start-clean'))
        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(DemoSession.objects.count(), 0)

        landing = client.get(reverse('demo-landing'))
        token = landing.cookies['csrftoken'].value
        with patch(
            'scheduler_app.demo_entry.provision_clean_demo_session',
            wraps=provision_clean_demo_session,
        ) as provision:
            accepted = client.post(
                reverse('demo-start-clean'),
                {'csrfmiddlewaretoken': token},
                HTTP_X_CSRFTOKEN=token,
            )

        self.assertEqual(accepted.status_code, 302)
        provision.assert_called_once()
        self.assertIn('admission', provision.call_args.kwargs)

    def test_rotated_token_is_rejected_until_landing_is_refreshed(self):
        client = Client(enforce_csrf_checks=True)
        landing = client.get(reverse('demo-landing'))
        stale_token = landing.cookies['csrftoken'].value
        client.cookies['csrftoken'] = _get_new_csrf_string()

        stale = client.post(
            reverse('demo-start-clean'),
            {'csrfmiddlewaretoken': stale_token},
        )
        self.assertEqual(stale.status_code, 403)

        refreshed = client.get(reverse('demo-landing'))
        self.assertEqual(refreshed.status_code, 200)
        fresh_token = client.cookies['csrftoken'].value
        with patch(
            'scheduler_app.demo_entry.provision_clean_demo_session',
            side_effect=CleanDemoProvisioningError('controlled'),
        ):
            accepted = client.post(
                reverse('demo-start-clean'),
                {'csrfmiddlewaretoken': fresh_token},
            )
        self.assertEqual(accepted.status_code, 503)


class DemoEntrySuccessTests(TestCase):
    def test_anonymous_post_provisions_commits_logs_in_and_redirects(self):
        existing_session = self.client.session
        existing_session['pre_demo'] = 'present'
        existing_session.save()
        old_session_key = existing_session.session_key

        with (
            patch(
                'scheduler_app.demo_entry.provision_clean_demo_session',
                wraps=provision_clean_demo_session,
            ) as provision,
            patch(
                'scheduler_app.demo_entry.login',
                wraps=django_login,
            ) as login,
            patch('django.contrib.auth.authenticate') as authenticate,
        ):
            response = self.client.post(reverse('demo-start-clean'))

        self.assertRedirects(response, reverse('home-paid'))
        provision.assert_called_once()
        self.assertIn('admission', provision.call_args.kwargs)
        login.assert_called_once()
        authenticate.assert_not_called()
        session = self.client.session
        self.assertNotEqual(session.session_key, old_session_key)
        self.assertNotIn('organization_id', session)

        demo_session = DemoSession.objects.select_related(
            'user',
            'organization',
        ).get()
        self.assertEqual(int(session['_auth_user_id']), demo_session.user_id)
        self.assertFalse(demo_session.user.has_usable_password())
        self.assertFalse(demo_session.user.is_staff)
        self.assertFalse(demo_session.user.is_superuser)
        self.assertFalse(demo_session.user.groups.exists())
        self.assertFalse(demo_session.user.user_permissions.exists())
        self.assertEqual(
            demo_session.user.organization_membership.organization,
            demo_session.organization,
        )
        self.assertEqual(
            demo_session.organization.purpose,
            Organization.Purpose.TEMPORARY_DEMO,
        )
        self.assertEqual(demo_session.mode, DemoSession.Mode.CLEAN)
        self.assertEqual(demo_session.status, DemoSession.Status.ACTIVE)
        self.assertGreater(demo_session.expires_at, timezone.now())

        follow_up = self.client.get(reverse('home-paid'))
        self.assertEqual(follow_up.status_code, 200)
        self.assertEqual(follow_up.wsgi_request.user, demo_session.user)

    def test_login_occurs_only_after_provisioning_returns(self):
        events = []

        def provision(*, admission):
            result = provision_clean_demo_session(admission=admission)
            events.append('provisioned')
            return result

        def login(request, user, backend):
            events.append('login')
            return django_login(request, user, backend=backend)

        with (
            patch(
                'scheduler_app.demo_entry.provision_clean_demo_session',
                side_effect=provision,
            ),
            patch('scheduler_app.demo_entry.login', side_effect=login),
        ):
            self.client.post(reverse('demo-start-clean'))

        self.assertEqual(events, ['provisioned', 'login'])

    def test_clean_workspace_has_no_operational_records(self):
        self.client.post(reverse('demo-start-clean'))
        organization = DemoSession.objects.get().organization

        self.assertFalse(Locations.objects.filter(organization=organization).exists())
        self.assertFalse(Course.objects.filter(organization=organization).exists())
        self.assertFalse(
            Certification.objects.filter(organization=organization).exists()
        )
        self.assertFalse(
            Schools.schools_list.filter(organization=organization).exists()
        )
        self.assertFalse(Instructor.objects.filter(organization=organization).exists())
        self.assertFalse(TheSched.objects.filter(organization=organization).exists())
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

    def test_repeat_post_reuses_authenticated_temporary_workspace(self):
        first = self.client.post(reverse('demo-start-clean'))
        counts = (
            get_user_model().objects.count(),
            Organization.objects.count(),
            OrganizationMembership.objects.count(),
            DemoSession.objects.count(),
        )

        with patch(
            'scheduler_app.demo_entry.provision_clean_demo_session'
        ) as provision:
            repeated = self.client.post(reverse('demo-start-clean'))

        self.assertRedirects(first, reverse('home-paid'))
        self.assertRedirects(repeated, reverse('home-paid'))
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


class ExistingAuthenticatedDemoEntryTests(TestCase):
    def make_user(self, name, purpose, **user_flags):
        organization = Organization.objects.create(
            name=f'{name} Organization',
            purpose=purpose,
        )
        user = get_user_model().objects.create_user(username=name, **user_flags)
        OrganizationMembership.objects.create(user=user, organization=organization)
        return user, organization

    def test_customer_canonical_staff_and_superuser_sessions_are_not_replaced(self):
        cases = (
            ('customer', Organization.Purpose.CUSTOMER, {}),
            ('canonical', Organization.Purpose.CANONICAL_DEMO, {}),
            ('staff', Organization.Purpose.CUSTOMER, {'is_staff': True}),
            ('superuser', Organization.Purpose.CUSTOMER, {'is_superuser': True}),
        )
        for name, purpose, flags in cases:
            with self.subTest(name=name):
                user, organization = self.make_user(name, purpose, **flags)
                client = Client()
                client.force_login(user)
                before = DemoSession.objects.count()
                with patch(
                    'scheduler_app.demo_entry.provision_clean_demo_session'
                ) as provision:
                    response = client.post(reverse('demo-start-clean'))

                self.assertEqual(response.status_code, 403)
                provision.assert_not_called()
                self.assertEqual(DemoSession.objects.count(), before)
                self.assertEqual(
                    int(client.session['_auth_user_id']),
                    user.pk,
                )
                organization.refresh_from_db()
                self.assertEqual(organization.purpose, purpose)

    def test_invalid_temporary_ownership_fails_closed(self):
        user, organization = self.make_user(
            'invalid-temp',
            Organization.Purpose.TEMPORARY_DEMO,
        )
        client = Client()
        client.force_login(user)

        with patch(
            'scheduler_app.demo_entry.provision_clean_demo_session'
        ) as provision:
            response = client.post(reverse('demo-start-clean'))

        self.assertEqual(response.status_code, 403)
        provision.assert_not_called()
        self.assertEqual(DemoSession.objects.count(), 0)
        self.assertEqual(int(client.session['_auth_user_id']), user.pk)
        organization.refresh_from_db()
        self.assertEqual(
            organization.purpose,
            Organization.Purpose.TEMPORARY_DEMO,
        )


class DemoEntryFailureTests(TestCase):
    def test_controlled_provisioning_failure_is_safe_and_unauthenticated(self):
        with patch(
            'scheduler_app.demo_entry.provision_clean_demo_session',
            side_effect=CleanDemoProvisioningError('secret internal detail 481'),
        ):
            response = self.client.post(reverse('demo-start-clean'))

        self.assertEqual(response.status_code, 503)
        self.assertContains(
            response,
            'could not be started',
            status_code=503,
        )
        self.assertNotContains(
            response,
            'secret internal detail 481',
            status_code=503,
        )
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(DemoSession.objects.count(), 0)

    def test_login_failure_leaves_consistent_unit_but_no_authenticated_browser(self):
        with patch(
            'scheduler_app.demo_entry.login',
            side_effect=RuntimeError('injected login failure'),
        ):
            response = self.client.post(reverse('demo-start-clean'))

        self.assertEqual(response.status_code, 503)
        self.assertNotIn('_auth_user_id', self.client.session)
        demo_session = DemoSession.objects.get()
        self.assertEqual(
            demo_session.user.organization_membership.organization,
            demo_session.organization,
        )
        self.assertEqual(demo_session.status, DemoSession.Status.ACTIVE)
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertEqual(OrganizationMembership.objects.count(), 1)


class DemoEntryPostLoginVerificationTests(TestCase):
    def assert_mutation_fails_closed(self, mutation):
        result = provision_clean_demo_session()
        client = Client()

        def login_then_mutate(request, user, backend):
            django_login(request, user, backend=backend)
            mutation(result)

        with (
            patch(
                'scheduler_app.demo_entry.provision_clean_demo_session',
                return_value=result,
            ),
            patch(
                'scheduler_app.demo_entry.login',
                side_effect=login_then_mutate,
            ),
        ):
            response = client.post(reverse('demo-start-clean'))

        self.assertEqual(response.status_code, 503)
        self.assertNotIn('_auth_user_id', client.session)
        self.assertNotEqual(response.get('Location'), reverse('home-paid'))

    def test_wrong_authenticated_user_fails_closed(self):
        other = get_user_model().objects.create_user(username='other')

        def wrong_user(result):
            return None

        result = provision_clean_demo_session()
        client = Client()

        def login_other(request, user, backend):
            django_login(request, other, backend=backend)

        with (
            patch(
                'scheduler_app.demo_entry.provision_clean_demo_session',
                return_value=result,
            ),
            patch('scheduler_app.demo_entry.login', side_effect=login_other),
        ):
            response = client.post(reverse('demo-start-clean'))

        self.assertEqual(response.status_code, 503)
        self.assertNotIn('_auth_user_id', client.session)

    def test_missing_membership_fails_closed(self):
        self.assert_mutation_fails_closed(
            lambda result: result.membership.delete()
        )

    def test_wrong_membership_organization_fails_closed(self):
        other = Organization.objects.create(name='Wrong Organization')

        def mutate(result):
            result.membership.organization = other
            result.membership.save()

        self.assert_mutation_fails_closed(mutate)

    def test_non_temporary_purpose_fails_closed(self):
        self.assert_mutation_fails_closed(
            lambda result: Organization.objects.filter(
                pk=result.organization.pk
            ).update(purpose=Organization.Purpose.CUSTOMER)
        )

    def test_missing_demo_session_fails_closed(self):
        self.assert_mutation_fails_closed(
            lambda result: result.demo_session.delete()
        )

    def test_wrong_mode_fails_closed(self):
        self.assert_mutation_fails_closed(
            lambda result: DemoSession.objects.filter(
                pk=result.demo_session.pk
            ).update(mode=DemoSession.Mode.PREPARED, scenario_version='v1')
        )

    def test_inactive_status_fails_closed(self):
        self.assert_mutation_fails_closed(
            lambda result: DemoSession.objects.filter(
                pk=result.demo_session.pk
            ).update(status=DemoSession.Status.FAILED)
        )

    def test_expired_session_fails_closed(self):
        self.assert_mutation_fails_closed(
            lambda result: DemoSession.objects.filter(
                pk=result.demo_session.pk
            ).update(
                expires_at=result.demo_session.created_at + timedelta(microseconds=1)
            )
        )

    def test_mismatched_expected_session_ownership_fails_closed(self):
        other = provision_clean_demo_session()
        original = provision_clean_demo_session()
        mismatched = replace(original, demo_session=other.demo_session)
        client = Client()

        with patch(
            'scheduler_app.demo_entry.provision_clean_demo_session',
            return_value=mismatched,
        ):
            response = client.post(reverse('demo-start-clean'))

        self.assertEqual(response.status_code, 503)
        self.assertNotIn('_auth_user_id', client.session)


class DemoEntryIsolationTests(TestCase):
    def test_entry_does_not_mutate_foreign_organizations_or_call_canonical_services(self):
        default, _created = Organization.objects.get_or_create(
            name='Default Organization'
        )
        customer = Organization.objects.create(name='Customer')
        canonical = Organization.objects.create(
            name='Canonical',
            purpose=Organization.Purpose.CANONICAL_DEMO,
        )
        existing_temporary = provision_clean_demo_session()
        state = {
            organization.pk: (
                organization.name,
                organization.purpose,
                organization.updated_at,
            )
            for organization in (
                default,
                customer,
                canonical,
                existing_temporary.organization,
            )
        }

        with (
            patch(
                'scheduler_app.demo_scaffolding.inspect_demo_environment'
            ) as inspect,
            patch('scheduler_app.demo_scaffolding.apply_demo_reference_data') as apply,
            patch('scheduler_app.demo_scaffolding.reset_demo_environment') as reset,
        ):
            response = self.client.post(reverse('demo-start-clean'))

        self.assertRedirects(response, reverse('home-paid'))
        inspect.assert_not_called()
        apply.assert_not_called()
        reset.assert_not_called()
        for organization_id, expected in state.items():
            organization = Organization.objects.get(pk=organization_id)
            self.assertEqual(
                (
                    organization.name,
                    organization.purpose,
                    organization.updated_at,
                ),
                expected,
            )
