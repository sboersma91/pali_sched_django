from datetime import timedelta
from unittest.mock import patch
import uuid

from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from members.models import (
    DemoCapacityReservation,
    DemoOperationLease,
    DemoProvisioningAttempt,
    DemoSession,
)
from scheduler_project.settings_validation import validate_demo_capacity_settings

from .demo_capacity import (
    DemoCapacityDenied,
    DemoPreparedOperationBusy,
    DemoThrottleDenied,
    acquire_prepared_reset_operation,
    client_key_from_request,
    release_demo_provisioning,
    release_prepared_operation,
    reserve_demo_provisioning_capacity,
)
from .demo_session_provisioning import provision_clean_demo_session
from .prepared_demo_provisioning import provision_prepared_demo_session


CAPACITY_SETTINGS = {
    'DEMO_MAX_ACTIVE_SESSIONS': 10,
    'DEMO_MAX_ACTIVE_PREPARED_SESSIONS': 4,
    'DEMO_MAX_ACTIVE_CLEAN_SESSIONS': 6,
    'DEMO_GLOBAL_START_LIMIT': 12,
    'DEMO_GLOBAL_START_WINDOW_SECONDS': 3600,
    'DEMO_CLIENT_START_LIMIT': 3,
    'DEMO_CLIENT_START_WINDOW_SECONDS': 900,
    'DEMO_MAX_CONCURRENT_PREPARED_OPERATIONS': 1,
    'DEMO_PREPARED_RESET_LIMIT': 6,
    'DEMO_PREPARED_RESET_WINDOW_SECONDS': 3600,
    'DEMO_CAPACITY_RESERVATION_SECONDS': 600,
    'DEMO_PREPARED_OPERATION_LEASE_SECONDS': 600,
}


class DemoCapacitySettingsTests(TestCase):
    def test_defaults_and_valid_relationships(self):
        self.assertEqual(
            validate_demo_capacity_settings(dict(CAPACITY_SETTINGS)),
            CAPACITY_SETTINGS,
        )

    def test_nonpositive_noninteger_and_mode_caps_fail_closed(self):
        for name, value in (
            ('DEMO_MAX_ACTIVE_SESSIONS', 0),
            ('DEMO_GLOBAL_START_LIMIT', -1),
            ('DEMO_CLIENT_START_LIMIT', '3'),
            ('DEMO_MAX_CONCURRENT_PREPARED_OPERATIONS', False),
        ):
            with self.subTest(name=name):
                values = dict(CAPACITY_SETTINGS)
                values[name] = value
                with self.assertRaisesMessage(ImproperlyConfigured, name):
                    validate_demo_capacity_settings(values)
        for name in (
            'DEMO_MAX_ACTIVE_PREPARED_SESSIONS',
            'DEMO_MAX_ACTIVE_CLEAN_SESSIONS',
        ):
            values = dict(CAPACITY_SETTINGS)
            values[name] = values['DEMO_MAX_ACTIVE_SESSIONS'] + 1
            with self.assertRaisesMessage(ImproperlyConfigured, name):
                validate_demo_capacity_settings(values)


@override_settings(**CAPACITY_SETTINGS)
class DemoCapacityAdmissionTests(TestCase):
    def test_global_and_mode_caps_count_provisioning_and_active_only(self):
        active = provision_clean_demo_session()
        DemoSession.objects.filter(pk=active.demo_session.pk).update(
            status=DemoSession.Status.PROVISIONING
        )
        with override_settings(
            DEMO_MAX_ACTIVE_SESSIONS=1,
            DEMO_MAX_ACTIVE_PREPARED_SESSIONS=1,
            DEMO_MAX_ACTIVE_CLEAN_SESSIONS=1,
        ):
            with self.assertRaises(DemoCapacityDenied):
                reserve_demo_provisioning_capacity(
                    requested_mode=DemoSession.Mode.CLEAN,
                    client_key='a' * 64,
                )

        for status in (
            DemoSession.Status.EXPIRING,
            DemoSession.Status.DELETING,
            DemoSession.Status.FAILED,
        ):
            DemoSession.objects.filter(pk=active.demo_session.pk).update(status=status)
            with override_settings(
                DEMO_MAX_ACTIVE_SESSIONS=1,
                DEMO_MAX_ACTIVE_PREPARED_SESSIONS=1,
                DEMO_MAX_ACTIVE_CLEAN_SESSIONS=1,
            ):
                admission = reserve_demo_provisioning_capacity(
                    requested_mode=DemoSession.Mode.CLEAN,
                    client_key=(status[0] * 64),
                )
                release_demo_provisioning(admission)

    def test_expired_active_session_does_not_count(self):
        result = provision_clean_demo_session()
        DemoSession.objects.filter(pk=result.demo_session.pk).update(
            expires_at=timezone.now()
        )
        with override_settings(
            DEMO_MAX_ACTIVE_SESSIONS=1,
            DEMO_MAX_ACTIVE_PREPARED_SESSIONS=1,
            DEMO_MAX_ACTIVE_CLEAN_SESSIONS=1,
        ):
            admission = reserve_demo_provisioning_capacity(
                requested_mode=DemoSession.Mode.CLEAN,
                client_key='e' * 64,
            )
        self.assertIsNotNone(admission)
        release_demo_provisioning(admission)

    def test_reservation_counts_and_expired_reservation_is_reclaimed(self):
        with override_settings(
            DEMO_MAX_ACTIVE_SESSIONS=1,
            DEMO_MAX_ACTIVE_PREPARED_SESSIONS=1,
            DEMO_MAX_ACTIVE_CLEAN_SESSIONS=1,
        ):
            first = reserve_demo_provisioning_capacity(
                requested_mode=DemoSession.Mode.CLEAN,
                client_key='1' * 64,
            )
            with self.assertRaises(DemoCapacityDenied):
                reserve_demo_provisioning_capacity(
                    requested_mode=DemoSession.Mode.CLEAN,
                    client_key='2' * 64,
                )
            now = timezone.now()
            DemoCapacityReservation.objects.filter(
                token=first.reservation_token
            ).update(
                created_at=now - timedelta(minutes=2),
                expires_at=now - timedelta(minutes=1),
            )
            second = reserve_demo_provisioning_capacity(
                requested_mode=DemoSession.Mode.CLEAN,
                client_key='2' * 64,
            )
        self.assertFalse(
            DemoCapacityReservation.objects.filter(
                token=first.reservation_token
            ).exists()
        )
        release_demo_provisioning(second)

    def test_release_requires_exact_reservation_token(self):
        admission = reserve_demo_provisioning_capacity(
            requested_mode=DemoSession.Mode.CLEAN,
            client_key='r' * 64,
        )
        wrong = admission.__class__(
            reservation_token=uuid.uuid4(),
            attempt_id=admission.attempt_id,
            mode=admission.mode,
        )
        self.assertFalse(release_demo_provisioning(wrong))
        self.assertTrue(
            DemoCapacityReservation.objects.filter(
                token=admission.reservation_token
            ).exists()
        )
        self.assertTrue(release_demo_provisioning(admission))

    def test_successful_and_failed_direct_provisioning_leave_no_reservation(self):
        result = provision_clean_demo_session()
        self.assertFalse(DemoCapacityReservation.objects.exists())
        self.assertEqual(
            DemoProvisioningAttempt.objects.filter(
                demo_session=result.demo_session,
                outcome=DemoProvisioningAttempt.Outcome.SUCCEEDED,
            ).count(),
            1,
        )
        with patch(
            'scheduler_app.demo_session_provisioning._create_temporary_ownership',
            side_effect=RuntimeError('controlled failure'),
        ):
            with self.assertRaises(RuntimeError):
                provision_clean_demo_session()
        self.assertFalse(DemoCapacityReservation.objects.exists())
        self.assertTrue(
            DemoProvisioningAttempt.objects.filter(
                outcome=DemoProvisioningAttempt.Outcome.FAILED
            ).exists()
        )

    def test_direct_provisioning_cannot_bypass_active_capacity(self):
        existing = provision_clean_demo_session()
        with override_settings(
            DEMO_MAX_ACTIVE_SESSIONS=1,
            DEMO_MAX_ACTIVE_PREPARED_SESSIONS=1,
            DEMO_MAX_ACTIVE_CLEAN_SESSIONS=1,
        ):
            with self.assertRaises(DemoCapacityDenied):
                provision_clean_demo_session()
        self.assertEqual(DemoSession.objects.count(), 1)
        self.assertTrue(
            DemoSession.objects.filter(pk=existing.demo_session.pk).exists()
        )


@override_settings(**CAPACITY_SETTINGS)
class DemoThrottleAndClientKeyTests(TestCase):
    def test_global_and_per_client_limits_and_window_recovery(self):
        with override_settings(DEMO_CLIENT_START_LIMIT=2):
            for _index in range(2):
                admission = reserve_demo_provisioning_capacity(
                    requested_mode=DemoSession.Mode.CLEAN,
                    client_key='same-client',
                )
                release_demo_provisioning(admission)
            with self.assertRaises(DemoThrottleDenied):
                reserve_demo_provisioning_capacity(
                    requested_mode=DemoSession.Mode.CLEAN,
                    client_key='same-client',
                )
            different = reserve_demo_provisioning_capacity(
                requested_mode=DemoSession.Mode.CLEAN,
                client_key='different-client',
            )
            release_demo_provisioning(different)

            DemoProvisioningAttempt.objects.filter(
                client_key='same-client'
            ).update(
                created_at=timezone.now() - timedelta(hours=2)
            )
            recovered = reserve_demo_provisioning_capacity(
                requested_mode=DemoSession.Mode.CLEAN,
                client_key='same-client',
            )
            release_demo_provisioning(recovered)

        with override_settings(DEMO_GLOBAL_START_LIMIT=1):
            DemoProvisioningAttempt.objects.all().delete()
            admitted = reserve_demo_provisioning_capacity(
                requested_mode=DemoSession.Mode.CLEAN,
                client_key='global-one',
            )
            release_demo_provisioning(admitted)
            with self.assertRaises(DemoThrottleDenied):
                reserve_demo_provisioning_capacity(
                    requested_mode=DemoSession.Mode.CLEAN,
                    client_key='global-two',
                )

    def test_client_address_is_normalized_hashed_and_proxy_is_explicit(self):
        factory = RequestFactory()
        ipv4 = factory.post('/demo/start/clean/', REMOTE_ADDR='192.0.2.4')
        equivalent = factory.post(
            '/demo/start/clean/',
            REMOTE_ADDR='192.0.2.4',
            HTTP_X_FORWARDED_FOR='198.51.100.8',
        )
        self.assertEqual(
            client_key_from_request(ipv4),
            client_key_from_request(equivalent),
        )
        self.assertNotIn('192.0.2.4', client_key_from_request(ipv4))
        self.assertEqual(len(client_key_from_request(ipv4)), 64)

        with override_settings(
            SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https')
        ):
            trusted = client_key_from_request(equivalent)
        self.assertNotEqual(trusted, client_key_from_request(ipv4))

        ipv6_a = factory.post('/demo/start/clean/', REMOTE_ADDR='2001:db8::1')
        ipv6_b = factory.post(
            '/demo/start/clean/',
            REMOTE_ADDR='2001:0db8:0:0:0:0:0:1',
        )
        self.assertEqual(
            client_key_from_request(ipv6_a),
            client_key_from_request(ipv6_b),
        )
        fallback = factory.post('/demo/start/clean/')
        self.assertEqual(len(client_key_from_request(fallback)), 64)


@override_settings(**CAPACITY_SETTINGS)
class PreparedOperationLeaseTests(TestCase):
    def test_prepared_lease_blocks_prepared_but_not_clean_and_reclaims_expired(self):
        prepared = reserve_demo_provisioning_capacity(
            requested_mode=DemoSession.Mode.PREPARED,
            client_key='prepared-one',
        )
        with self.assertRaises(DemoPreparedOperationBusy):
            reserve_demo_provisioning_capacity(
                requested_mode=DemoSession.Mode.PREPARED,
                client_key='prepared-two',
            )
        clean = reserve_demo_provisioning_capacity(
            requested_mode=DemoSession.Mode.CLEAN,
            client_key='clean',
        )
        release_demo_provisioning(clean)

        now = timezone.now()
        DemoOperationLease.objects.filter(token=prepared.lease_token).update(
            acquired_at=now - timedelta(minutes=11),
            expires_at=now - timedelta(minutes=1),
        )
        replacement = reserve_demo_provisioning_capacity(
            requested_mode=DemoSession.Mode.PREPARED,
            client_key='prepared-two',
        )
        self.assertFalse(release_prepared_operation(prepared.lease_token))
        self.assertTrue(release_demo_provisioning(replacement))
        release_demo_provisioning(prepared)

    def test_prepared_reset_shares_lease_and_wrong_token_cannot_release(self):
        result = provision_prepared_demo_session()
        lease = acquire_prepared_reset_operation(result.demo_session)
        with self.assertRaises(DemoPreparedOperationBusy):
            reserve_demo_provisioning_capacity(
                requested_mode=DemoSession.Mode.PREPARED,
                client_key='blocked-provision',
            )
        self.assertFalse(release_prepared_operation('00000000-0000-0000-0000-000000000001'))
        self.assertTrue(release_prepared_operation(lease.lease_token))

    def test_prepared_reset_window_limit_is_independent_of_start_throttle(self):
        result = provision_prepared_demo_session()
        with override_settings(DEMO_PREPARED_RESET_LIMIT=1):
            lease = acquire_prepared_reset_operation(result.demo_session)
            release_prepared_operation(lease.lease_token)
            with self.assertRaises(DemoThrottleDenied):
                acquire_prepared_reset_operation(result.demo_session)

    def test_busy_reset_response_preserves_authentication_and_skips_service(self):
        result = provision_prepared_demo_session()
        lease = acquire_prepared_reset_operation(result.demo_session)
        self.client.force_login(result.user)
        before = result.schedule.sched_data

        with patch('scheduler_app.views.reset_prepared_demo_session') as reset:
            response = self.client.post(
                reverse('demo-reset-prepared'),
                {'confirm_reset': 'yes'},
            )

        self.assertEqual(response.status_code, 503)
        reset.assert_not_called()
        self.assertEqual(int(self.client.session['_auth_user_id']), result.user.pk)
        result.schedule.refresh_from_db()
        self.assertEqual(result.schedule.sched_data, before)
        release_prepared_operation(lease.lease_token)


@override_settings(**CAPACITY_SETTINGS)
class DemoCapacityEntryTests(TestCase):
    def test_entry_denial_precedes_provisioning_and_creates_no_ownership(self):
        existing = provision_clean_demo_session()
        before = DemoSession.objects.count()
        with override_settings(
            DEMO_MAX_ACTIVE_SESSIONS=1,
            DEMO_MAX_ACTIVE_PREPARED_SESSIONS=1,
            DEMO_MAX_ACTIVE_CLEAN_SESSIONS=1,
        ):
            with patch(
                'scheduler_app.demo_entry.provision_clean_demo_session'
            ) as provision:
                response = self.client.post(
                    reverse('demo-start-clean'),
                    REMOTE_ADDR='192.0.2.10',
                )
        self.assertEqual(response.status_code, 503)
        provision.assert_not_called()
        self.assertEqual(DemoSession.objects.count(), before)
        self.assertTrue(
            DemoSession.objects.filter(pk=existing.demo_session.pk).exists()
        )

    def test_throttle_response_is_429_with_retry_after(self):
        with override_settings(DEMO_CLIENT_START_LIMIT=1):
            admission = reserve_demo_provisioning_capacity(
                requested_mode=DemoSession.Mode.CLEAN,
                client_key=client_key_from_request(
                    RequestFactory().post('/', REMOTE_ADDR='192.0.2.20')
                ),
            )
            release_demo_provisioning(admission)
            response = self.client.post(
                reverse('demo-start-clean'),
                REMOTE_ADDR='192.0.2.20',
            )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response['Retry-After'], '900')
        self.assertNotContains(response, '192.0.2.20', status_code=429)
