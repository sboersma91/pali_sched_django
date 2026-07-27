from datetime import timedelta
from io import StringIO
import uuid
from unittest.mock import patch

from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.management import load_command_class
from django.test import TestCase, override_settings
from django.utils import timezone

from members.models import (
    DemoCapacityReservation,
    DemoOperationLease,
    DemoPreparedResetAttempt,
    DemoProvisioningAttempt,
    DemoSession,
    Organization,
)

from .demo_maintenance import (
    acquire_maintenance_lease,
    plan_demo_maintenance,
    release_maintenance_lease,
    run_demo_maintenance,
)
from .demo_session_exit import exit_temporary_demo_session
from .demo_session_provisioning import provision_clean_demo_session
from .prepared_demo_provisioning import provision_prepared_demo_session


class DemoMaintenanceFixtures:
    def age(self, model, pk, *, days):
        timestamp = timezone.now() - timedelta(days=days)
        model.objects.filter(pk=pk).update(created_at=timestamp)
        return timestamp

    def reservation(self, *, expired):
        reservation = DemoCapacityReservation.objects.create(
            mode=DemoSession.Mode.CLEAN,
            client_key='a' * 64,
            expires_at=timezone.now() + timedelta(days=2),
        )
        created = timezone.now() - timedelta(days=2)
        expiration = (
            timezone.now() - timedelta(days=1)
            if expired
            else timezone.now() + timedelta(days=1)
        )
        DemoCapacityReservation.objects.filter(pk=reservation.pk).update(
            created_at=created,
            expires_at=expiration,
        )
        reservation.refresh_from_db()
        return reservation

    def lease(self, operation, *, expired):
        acquired = timezone.now() - timedelta(days=2)
        return DemoOperationLease.objects.create(
            operation=operation,
            acquired_at=acquired,
            expires_at=(
                timezone.now() - timedelta(days=1)
                if expired
                else timezone.now() + timedelta(days=1)
            ),
        )

    def provisioning_attempt(self, *, days, session=None):
        attempt = DemoProvisioningAttempt.objects.create(
            mode=DemoSession.Mode.CLEAN,
            outcome=DemoProvisioningAttempt.Outcome.SUCCEEDED,
            client_key='b' * 64,
            demo_session=session,
        )
        self.age(DemoProvisioningAttempt, attempt.pk, days=days)
        attempt.refresh_from_db()
        return attempt

    def reset_attempt(self, *, days, session=None):
        attempt = DemoPreparedResetAttempt.objects.create(
            demo_session=session
        )
        self.age(DemoPreparedResetAttempt, attempt.pk, days=days)
        attempt.refresh_from_db()
        return attempt


class DemoMaintenanceDryRunTests(DemoMaintenanceFixtures, TestCase):
    def test_default_dry_run_reports_without_any_mutation_or_secret_output(self):
        exited = provision_clean_demo_session()
        exit_temporary_demo_session(demo_session=exited.demo_session)
        reservation = self.reservation(expired=True)
        lease = self.lease(DemoOperationLease.PREPARED, expired=True)
        attempt = self.provisioning_attempt(days=8)
        reset = self.reset_attempt(days=8)
        session_key = 'secret-framework-session-key'
        Session.objects.create(
            session_key=session_key,
            session_data='not-decoded',
            expire_date=timezone.now() - timedelta(days=1),
        )
        counts = (
            Organization.objects.count(),
            DemoCapacityReservation.objects.count(),
            DemoOperationLease.objects.count(),
            DemoProvisioningAttempt.objects.count(),
            DemoPreparedResetAttempt.objects.count(),
            Session.objects.count(),
        )
        output = StringIO()

        call_command('run_demo_maintenance', stdout=output)

        self.assertEqual(
            (
                Organization.objects.count(),
                DemoCapacityReservation.objects.count(),
                DemoOperationLease.objects.count(),
                DemoProvisioningAttempt.objects.count(),
                DemoPreparedResetAttempt.objects.count(),
                Session.objects.count(),
            ),
            counts,
        )
        text = output.getvalue()
        self.assertIn('Mode: dry run', text)
        self.assertIn('cleanup=25 attempt=500 auxiliary=100', text)
        self.assertIn('DRY RUN', text)
        self.assertNotIn(session_key, text)
        self.assertNotIn(str(reservation.token), text)
        self.assertNotIn(str(lease.token), text)
        self.assertTrue(DemoProvisioningAttempt.objects.filter(pk=attempt.pk).exists())
        self.assertTrue(DemoPreparedResetAttempt.objects.filter(pk=reset.pk).exists())

    def test_dry_run_reports_active_maintenance_lease_without_releasing_it(self):
        lease = self.lease(DemoOperationLease.MAINTENANCE, expired=False)
        output = StringIO()

        call_command('run_demo_maintenance', stdout=output)

        self.assertIn('Maintenance lease active: yes', output.getvalue())
        self.assertTrue(DemoOperationLease.objects.filter(pk=lease.pk).exists())

    def test_invalid_limits_and_cutoffs_fail_without_force_option(self):
        for arguments in (
            ('--cleanup-limit', '0'),
            ('--cleanup-limit', '101'),
            ('--attempt-limit', '5001'),
            ('--auxiliary-limit', '1001'),
            ('--before', 'invalid'),
            ('--before', '2026-01-01T00:00:00'),
            (
                '--before',
                (timezone.now() + timedelta(days=1)).isoformat(),
            ),
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(CommandError):
                    call_command('run_demo_maintenance', *arguments)
        command = load_command_class(
            'scheduler_app',
            'run_demo_maintenance',
        )
        parser = command.create_parser('manage.py', 'run_demo_maintenance')
        option_strings = {
            option
            for action in parser._actions
            for option in action.option_strings
        }
        self.assertNotIn('--force', option_strings)


class DemoMaintenanceLeaseTests(DemoMaintenanceFixtures, TestCase):
    def test_exact_token_release_and_stale_reclamation(self):
        stale = self.lease(DemoOperationLease.MAINTENANCE, expired=True)

        token = acquire_maintenance_lease()

        self.assertFalse(DemoOperationLease.objects.filter(pk=stale.pk).exists())
        self.assertFalse(release_maintenance_lease(uuid.uuid4()))
        self.assertTrue(DemoOperationLease.objects.filter(token=token).exists())
        self.assertTrue(release_maintenance_lease(token))

    def test_overlapping_confirmed_run_is_refused_without_mutation(self):
        self.lease(DemoOperationLease.MAINTENANCE, expired=False)
        reservation = self.reservation(expired=True)

        with self.assertRaises(CommandError):
            call_command('run_demo_maintenance', '--confirm', stdout=StringIO())

        self.assertTrue(
            DemoCapacityReservation.objects.filter(pk=reservation.pk).exists()
        )

    def test_prepared_lease_is_separate_and_remains_active(self):
        prepared = self.lease(DemoOperationLease.PREPARED, expired=False)
        plan = plan_demo_maintenance()

        result = run_demo_maintenance(plan=plan, confirmed=True)

        self.assertEqual(result.completion_category, 'completed')
        self.assertTrue(DemoOperationLease.objects.filter(pk=prepared.pk).exists())
        self.assertFalse(
            DemoOperationLease.objects.filter(
                operation=DemoOperationLease.MAINTENANCE
            ).exists()
        )

    def test_lease_releases_when_category_raises_unexpectedly(self):
        plan = plan_demo_maintenance()

        with patch(
            'scheduler_app.demo_maintenance.cleanup_expired_demo_sessions',
            side_effect=RuntimeError('safe injected failure'),
        ):
            result = run_demo_maintenance(plan=plan, confirmed=True)

        self.assertEqual(result.completion_category, 'partially_completed')
        self.assertIn('demo_cleanup:RuntimeError', result.errors)
        self.assertFalse(
            DemoOperationLease.objects.filter(
                operation=DemoOperationLease.MAINTENANCE
            ).exists()
        )
        self.assertIsNotNone(result.framework_sessions)

    def test_each_later_category_failure_is_isolated_and_releases_lease(self):
        operations = (
            'prune_expired_reservations',
            'prune_expired_operation_leases',
            'prune_provisioning_attempts',
            'prune_reset_attempts',
            'clear_expired_framework_sessions',
        )
        for operation in operations:
            with self.subTest(operation=operation):
                plan = plan_demo_maintenance()
                with patch(
                    f'scheduler_app.demo_maintenance.{operation}',
                    side_effect=RuntimeError('safe injected failure'),
                ):
                    result = run_demo_maintenance(
                        plan=plan,
                        confirmed=True,
                    )

                self.assertEqual(
                    result.completion_category,
                    'partially_completed',
                )
                self.assertTrue(result.errors)
                self.assertFalse(
                    DemoOperationLease.objects.filter(
                        operation=DemoOperationLease.MAINTENANCE
                    ).exists()
                )
                self.assertIsNotNone(result.framework_sessions)

    def test_command_reports_category_failure_as_nonzero(self):
        with patch(
            'scheduler_app.demo_maintenance.cleanup_expired_demo_sessions',
            side_effect=RuntimeError('safe injected failure'),
        ):
            with self.assertRaises(CommandError):
                call_command(
                    'run_demo_maintenance',
                    '--confirm',
                    stdout=StringIO(),
                )

        self.assertFalse(
            DemoOperationLease.objects.filter(
                operation=DemoOperationLease.MAINTENANCE
            ).exists()
        )


class DemoMaintenanceConfirmedTests(DemoMaintenanceFixtures, TestCase):
    def test_confirmed_cycle_cleans_each_category_and_preserves_active_rows(self):
        exited = provision_prepared_demo_session()
        active = provision_clean_demo_session()
        exit_temporary_demo_session(demo_session=exited.demo_session)
        expired_reservation = self.reservation(expired=True)
        active_reservation = self.reservation(expired=False)
        stale_lease = self.lease(DemoOperationLease.PREPARED, expired=True)
        active_lease = self.lease(DemoOperationLease.PREPARED, expired=False)
        old_attempt = self.provisioning_attempt(
            days=8,
            session=exited.demo_session,
        )
        recent_attempt = self.provisioning_attempt(days=1)
        old_reset = self.reset_attempt(days=8, session=exited.demo_session)
        recent_reset = self.reset_attempt(days=1, session=active.demo_session)
        expired_framework = Session.objects.create(
            session_key='expired-maintenance-session',
            session_data='opaque',
            expire_date=timezone.now() - timedelta(days=1),
        )
        active_framework = Session.objects.create(
            session_key='active-maintenance-session',
            session_data='opaque',
            expire_date=timezone.now() + timedelta(days=1),
        )

        call_command('run_demo_maintenance', '--confirm', stdout=StringIO())

        self.assertFalse(Organization.objects.filter(pk=exited.organization.pk).exists())
        self.assertTrue(Organization.objects.filter(pk=active.organization.pk).exists())
        self.assertFalse(
            DemoCapacityReservation.objects.filter(pk=expired_reservation.pk).exists()
        )
        self.assertTrue(
            DemoCapacityReservation.objects.filter(pk=active_reservation.pk).exists()
        )
        self.assertFalse(DemoOperationLease.objects.filter(pk=stale_lease.pk).exists())
        self.assertTrue(DemoOperationLease.objects.filter(pk=active_lease.pk).exists())
        self.assertFalse(
            DemoProvisioningAttempt.objects.filter(pk=old_attempt.pk).exists()
        )
        self.assertTrue(
            DemoProvisioningAttempt.objects.filter(pk=recent_attempt.pk).exists()
        )
        self.assertFalse(
            DemoPreparedResetAttempt.objects.filter(pk=old_reset.pk).exists()
        )
        self.assertTrue(
            DemoPreparedResetAttempt.objects.filter(pk=recent_reset.pk).exists()
        )
        self.assertFalse(Session.objects.filter(pk=expired_framework.pk).exists())
        self.assertTrue(Session.objects.filter(pk=active_framework.pk).exists())

    @override_settings(
        DEMO_MAINTENANCE_AUXILIARY_LIMIT=1,
        DEMO_MAINTENANCE_ATTEMPT_LIMIT=1,
    )
    def test_pruning_is_bounded_oldest_first_and_reports_backlog(self):
        reservations = [self.reservation(expired=True) for _ in range(2)]
        attempts = [
            self.provisioning_attempt(days=10),
            self.provisioning_attempt(days=9),
        ]
        plan = plan_demo_maintenance()

        result = run_demo_maintenance(plan=plan, confirmed=True)

        self.assertEqual(result.reservations.deleted, 1)
        self.assertEqual(result.reservations.plan.backlog, 1)
        self.assertEqual(result.provisioning_attempts.deleted, 1)
        self.assertEqual(result.provisioning_attempts.plan.backlog, 1)
        self.assertFalse(
            DemoProvisioningAttempt.objects.filter(pk=attempts[0].pk).exists()
        )
        self.assertTrue(
            DemoProvisioningAttempt.objects.filter(pk=attempts[1].pk).exists()
        )
        self.assertEqual(
            DemoCapacityReservation.objects.filter(
                pk__in=[item.pk for item in reservations]
            ).count(),
            1,
        )

    def test_recent_attempt_inside_throttle_window_remains_counted(self):
        recent = self.provisioning_attempt(days=0)
        plan = plan_demo_maintenance()

        run_demo_maintenance(plan=plan, confirmed=True)

        self.assertTrue(
            DemoProvisioningAttempt.objects.filter(pk=recent.pk).exists()
        )

    def test_pruning_linked_attempt_does_not_delete_successful_session(self):
        active = provision_clean_demo_session()
        old = self.provisioning_attempt(
            days=8,
            session=active.demo_session,
        )

        run_demo_maintenance(
            plan=plan_demo_maintenance(),
            confirmed=True,
        )

        self.assertFalse(
            DemoProvisioningAttempt.objects.filter(pk=old.pk).exists()
        )
        self.assertTrue(
            DemoSession.objects.filter(pk=active.demo_session.pk).exists()
        )
        self.assertTrue(
            Organization.objects.filter(pk=active.organization.pk).exists()
        )

    def test_repeated_confirmed_runs_are_idempotent(self):
        reservation = self.reservation(expired=True)

        first = run_demo_maintenance(
            plan=plan_demo_maintenance(),
            confirmed=True,
        )
        second = run_demo_maintenance(
            plan=plan_demo_maintenance(),
            confirmed=True,
        )

        self.assertEqual(first.reservations.deleted, 1)
        self.assertEqual(second.reservations.deleted, 0)
        self.assertFalse(
            DemoCapacityReservation.objects.filter(pk=reservation.pk).exists()
        )
