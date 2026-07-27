from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from members.models import DemoSession, Organization, OrganizationMembership

from .demo_session_provisioning import provision_clean_demo_session
from .demo_session_access import (
    ACCESS_INVALID,
    validate_temporary_demo_access,
)
from .middleware import DEMO_ACTIVITY_UPDATE_INTERVAL
from .prepared_demo_provisioning import provision_prepared_demo_session


class SessionQueryStub:
    def __init__(self, records, count=None):
        self.records = records
        self.forced_count = count

    def select_related(self, *_fields):
        return self

    def count(self):
        return self.forced_count if self.forced_count is not None else len(self.records)

    def first(self):
        return self.records[0] if self.records else None


class TemporaryDemoMiddlewarePlacementTests(TestCase):
    def test_middleware_is_after_authentication_middleware(self):
        from django.conf import settings

        authentication = settings.MIDDLEWARE.index(
            'django.contrib.auth.middleware.AuthenticationMiddleware'
        )
        enforcement = settings.MIDDLEWARE.index(
            'scheduler_app.middleware.TemporaryDemoSessionMiddleware'
        )
        self.assertEqual(enforcement, authentication + 1)

    def test_anonymous_customer_and_canonical_requests_continue_normally(self):
        self.assertEqual(self.client.get(reverse('home')).status_code, 200)
        for name, purpose in (
            ('customer', Organization.Purpose.CUSTOMER),
            ('canonical', Organization.Purpose.CANONICAL_DEMO),
        ):
            organization = Organization.objects.create(
                name=f'{name} Organization',
                purpose=purpose,
            )
            user = get_user_model().objects.create_user(username=name)
            OrganizationMembership.objects.create(
                user=user,
                organization=organization,
            )
            client = Client()
            client.force_login(user)
            self.assertEqual(client.get(reverse('home-paid')).status_code, 200)
            self.assertIn('_auth_user_id', client.session)

    def test_valid_clean_and_prepared_requests_continue_with_normal_scope(self):
        clean = provision_clean_demo_session()
        prepared = provision_prepared_demo_session()

        clean_client = Client()
        clean_client.force_login(clean.user)
        self.assertEqual(clean_client.get(reverse('location-list')).status_code, 200)
        self.assertNotContains(
            clean_client.get(reverse('location-list')),
            'Demo Commons',
        )

        prepared_client = Client()
        prepared_client.force_login(prepared.user)
        self.assertEqual(
            prepared_client.get(
                reverse('sched-detail', args=[prepared.schedule.pk])
            ).status_code,
            200,
        )


class TemporaryDemoExpirationTests(TestCase):
    def assert_expired_logout(self, result, route):
        before = {
            'users': get_user_model().objects.count(),
            'organizations': Organization.objects.count(),
            'memberships': OrganizationMembership.objects.count(),
            'sessions': DemoSession.objects.count(),
            'schedules': result.organization.schedules.count(),
        }
        DemoSession.objects.filter(pk=result.demo_session.pk).update(
            expires_at=result.demo_session.created_at + timedelta(microseconds=1)
        )
        client = Client()
        client.force_login(result.user)

        response = client.get(route)

        self.assertRedirects(response, f'{reverse("demo-landing")}?expired=1')
        self.assertNotIn('_auth_user_id', client.session)
        result.demo_session.refresh_from_db()
        self.assertEqual(result.demo_session.status, DemoSession.Status.EXPIRING)
        self.assertEqual(
            before,
            {
                'users': get_user_model().objects.count(),
                'organizations': Organization.objects.count(),
                'memberships': OrganizationMembership.objects.count(),
                'sessions': DemoSession.objects.count(),
                'schedules': result.organization.schedules.count(),
            },
        )
        landing = client.get(response['Location'])
        self.assertContains(landing, 'temporary demo session expired')
        self.assertEqual(client.get(route).status_code, 302)

    def test_expired_clean_session_is_logged_out_without_deletion(self):
        result = provision_clean_demo_session()
        self.assert_expired_logout(result, reverse('home-paid'))

    def test_expired_prepared_session_is_logged_out_without_deletion(self):
        result = provision_prepared_demo_session()
        self.assert_expired_logout(
            result,
            reverse('sched-detail', args=[result.schedule.pk]),
        )

    def test_exact_expiration_boundary(self):
        for offset, should_expire in (
            (timedelta(microseconds=1), False),
            (timedelta(0), True),
            (timedelta(microseconds=-1), True),
        ):
            with self.subTest(offset=offset):
                result = provision_clean_demo_session()
                now = result.demo_session.created_at + timedelta(minutes=10)
                DemoSession.objects.filter(pk=result.demo_session.pk).update(
                    expires_at=now + offset
                )
                client = Client()
                client.force_login(result.user)
                with patch(
                    'scheduler_app.middleware.timezone.now',
                    return_value=now,
                ):
                    response = client.get(reverse('home-paid'))
                self.assertEqual(response.status_code, 302 if should_expire else 200)
                self.assertEqual(
                    '_auth_user_id' in client.session,
                    not should_expire,
                )

    def test_expiration_of_one_visitor_does_not_affect_another(self):
        expired = provision_clean_demo_session()
        valid = provision_prepared_demo_session()
        DemoSession.objects.filter(pk=expired.demo_session.pk).update(
            expires_at=expired.demo_session.created_at + timedelta(microseconds=1)
        )
        expired_client = Client()
        expired_client.force_login(expired.user)
        valid_client = Client()
        valid_client.force_login(valid.user)

        expired_client.get(reverse('home-paid'))
        valid_response = valid_client.get(
            reverse('sched-detail', args=[valid.schedule.pk])
        )

        self.assertEqual(valid_response.status_code, 200)
        valid.demo_session.refresh_from_db()
        self.assertEqual(valid.demo_session.status, DemoSession.Status.ACTIVE)


class TemporaryDemoInvalidOwnershipTests(TestCase):
    def assert_invalid_logout(self, result, mutation):
        mutation(result)
        client = Client()
        client.force_login(result.user)
        counts = (
            get_user_model().objects.count(),
            Organization.objects.count(),
            DemoSession.objects.count(),
        )

        response = client.get(reverse('home-paid'))

        self.assertRedirects(
            response,
            f'{reverse("demo-landing")}?unavailable=1',
        )
        self.assertNotIn('_auth_user_id', client.session)
        self.assertEqual(
            counts,
            (
                get_user_model().objects.count(),
                Organization.objects.count(),
                DemoSession.objects.count(),
            ),
        )

    def test_membership_organization_purpose_and_session_mismatches_fail_closed(self):
        mutations = (
            lambda result: result.membership.delete(),
            lambda result: OrganizationMembership.objects.filter(
                pk=result.membership.pk
            ).update(
                organization=Organization.objects.create(
                    name=f'Foreign {Organization.objects.count()}'
                )
            ),
            lambda result: Organization.objects.filter(
                pk=result.organization.pk
            ).update(purpose=Organization.Purpose.CUSTOMER),
            lambda result: result.demo_session.delete(),
            lambda result: DemoSession.objects.filter(
                pk=result.demo_session.pk
            ).update(
                organization=Organization.objects.create(
                    name=f'Wrong Session {Organization.objects.count()}',
                    purpose=Organization.Purpose.TEMPORARY_DEMO,
                )
            ),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assert_invalid_logout(provision_clean_demo_session(), mutation)

    def test_all_inactive_statuses_fail_closed_without_activity_write(self):
        for status in (
            DemoSession.Status.PROVISIONING,
            DemoSession.Status.EXPIRING,
            DemoSession.Status.DELETING,
            DemoSession.Status.FAILED,
        ):
            with self.subTest(status=status):
                result = provision_clean_demo_session()
                original_activity = result.demo_session.last_activity_at
                self.assert_invalid_logout(
                    result,
                    lambda item, value=status: DemoSession.objects.filter(
                        pk=item.demo_session.pk
                    ).update(status=value),
                )
                result.demo_session.refresh_from_db()
                self.assertEqual(
                    result.demo_session.last_activity_at,
                    original_activity,
                )

    def test_wrong_prepared_version_and_privilege_promotion_fail_closed(self):
        prepared = provision_prepared_demo_session()
        self.assert_invalid_logout(
            prepared,
            lambda result: DemoSession.objects.filter(
                pk=result.demo_session.pk
            ).update(scenario_version='wrong'),
        )
        for flag in ('is_staff', 'is_superuser'):
            with self.subTest(flag=flag):
                result = provision_clean_demo_session()
                self.assert_invalid_logout(
                    result,
                    lambda item, field=flag: get_user_model().objects.filter(
                        pk=item.user.pk
                    ).update(**{field: True}),
                )


class TemporaryDemoStructuralValidationTests(TestCase):
    def assert_in_memory_session_invalid(self, mutate):
        result = provision_clean_demo_session()
        mutate(result.demo_session)
        result.user._state.fields_cache['demo_session'] = result.demo_session
        result.membership.user = result.user
        stub = SessionQueryStub([result.membership])
        with patch.object(
            OrganizationMembership.objects,
            'filter',
            return_value=stub,
        ):
            access = validate_temporary_demo_access(result.user)
        self.assertEqual(access.category, ACCESS_INVALID)

    def test_database_impossible_mode_user_expiration_and_ambiguity_fail_closed(self):
        other = get_user_model().objects.create_user(username='other')
        cases = (
            lambda session: setattr(session, 'mode', 'invalid'),
            lambda session: setattr(session, 'user_id', other.pk),
            lambda session: setattr(
                session,
                'expires_at',
                session.expires_at.replace(tzinfo=None),
            ),
        )
        for mutate in cases:
            with self.subTest(mutate=mutate):
                self.assert_in_memory_session_invalid(mutate)


class TemporaryDemoActivityTests(TestCase):
    def test_stale_activity_updates_once_without_extending_expiration(self):
        result = provision_clean_demo_session()
        now = timezone.now()
        stale = now - DEMO_ACTIVITY_UPDATE_INTERVAL - timedelta(seconds=1)
        DemoSession.objects.filter(pk=result.demo_session.pk).update(
            last_activity_at=stale
        )
        expiration = result.demo_session.expires_at
        self.client.force_login(result.user)

        with patch('scheduler_app.middleware.timezone.now', return_value=now):
            self.client.get(reverse('home-paid'))
            self.client.get(reverse('home-paid'))

        result.demo_session.refresh_from_db()
        self.assertEqual(result.demo_session.last_activity_at, now)
        self.assertEqual(result.demo_session.expires_at, expiration)

    def test_recent_activity_has_no_timestamp_churn(self):
        result = provision_prepared_demo_session()
        original = result.demo_session.last_activity_at
        self.client.force_login(result.user)

        self.client.get(reverse('home-paid'))

        result.demo_session.refresh_from_db()
        self.assertEqual(result.demo_session.last_activity_at, original)

    def test_permanent_users_and_public_routes_do_not_update_demo_sessions(self):
        result = provision_clean_demo_session()
        original = result.demo_session.last_activity_at
        customer_org = Organization.objects.create(name='Customer')
        customer = get_user_model().objects.create_user(username='customer')
        OrganizationMembership.objects.create(
            user=customer,
            organization=customer_org,
        )
        self.client.force_login(customer)

        self.client.get(reverse('home-paid'))
        self.client.get(reverse('demo-landing'))

        result.demo_session.refresh_from_db()
        self.assertEqual(result.demo_session.last_activity_at, original)


class TemporaryDemoExemptRouteTests(TestCase):
    def test_expired_landing_login_logout_and_start_routes_avoid_loops(self):
        result = provision_clean_demo_session()
        DemoSession.objects.filter(pk=result.demo_session.pk).update(
            expires_at=result.demo_session.created_at + timedelta(microseconds=1)
        )
        self.client.force_login(result.user)

        self.assertEqual(self.client.get(reverse('demo-landing')).status_code, 200)
        self.assertEqual(self.client.get(reverse('login')).status_code, 200)
        with patch(
            'scheduler_app.demo_entry.provision_clean_demo_session'
        ) as clean:
            response = self.client.post(reverse('demo-start-clean'))
        self.assertEqual(response.status_code, 403)
        clean.assert_not_called()

    def test_middleware_does_not_run_generation_assignment_or_canonical_services(self):
        result = provision_prepared_demo_session()
        self.client.force_login(result.user)

        with (
            patch('scheduler_app.models.TheSched.generate_and_store_schedule') as generate,
            patch('scheduler_app.instructor_assignment.run_instructor_assignment') as assign,
            patch('scheduler_app.demo_scaffolding.inspect_demo_environment') as inspect,
            patch('scheduler_app.demo_scaffolding.reset_demo_environment') as reset,
        ):
            response = self.client.get(reverse('home-paid'))

        self.assertEqual(response.status_code, 200)
        generate.assert_not_called()
        assign.assert_not_called()
        inspect.assert_not_called()
        reset.assert_not_called()
