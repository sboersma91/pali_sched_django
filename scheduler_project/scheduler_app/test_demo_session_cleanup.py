from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from members.models import DemoSession, Organization, OrganizationMembership

from .demo_session_cleanup import (
    DEFAULT_CLEANUP_LIMIT,
    MAX_CLEANUP_LIMIT,
    cleanup_demo_session,
    cleanup_expired_demo_sessions,
    plan_demo_session_cleanup,
)
from .demo_scaffolding import DEMO_SCENARIO
from .demo_session_provisioning import provision_clean_demo_session
from .models import (
    ActivityCertificationRequirement,
    Certification,
    Course,
    Instructor,
    InstructorCertification,
    InstructorScheduleAvailability,
    InstructorScheduleParticipation,
    LeadershipRole,
    Locations,
    Schools,
    TheSched,
)
from .prepared_demo_provisioning import provision_prepared_demo_session


def expire(result, *, status=DemoSession.Status.EXPIRING, cutoff=None):
    cutoff = cutoff or timezone.now()
    DemoSession.objects.filter(pk=result.demo_session.pk).update(
        expires_at=cutoff,
        status=status,
    )
    result.demo_session.refresh_from_db()
    return cutoff


class DemoCleanupDryRunTests(TestCase):
    def test_command_defaults_to_dry_run_with_accurate_safe_plan(self):
        result = provision_prepared_demo_session()
        expire(result)
        counts = {
            'locations': result.organization.locations.count(),
            'courses': result.organization.courses.count(),
            'schedules': result.organization.schedules.count(),
        }
        output = StringIO()

        call_command('cleanup_demo_sessions', stdout=output)

        text = output.getvalue()
        self.assertIn('DRY RUN', text)
        self.assertIn(str(result.demo_session.identifier), text)
        self.assertIn('ELIGIBLE', text)
        self.assertIn(f"'locations': {counts['locations']}", text)
        self.assertIn(f"'courses': {counts['courses']}", text)
        self.assertIn(f"'schedules': {counts['schedules']}", text)
        self.assertNotIn(result.user.password, text)
        self.assertTrue(DemoSession.objects.filter(pk=result.demo_session.pk).exists())
        self.assertTrue(Organization.objects.filter(pk=result.organization.pk).exists())

    def test_plan_distinguishes_unexpired_and_protected_organizations(self):
        active = provision_clean_demo_session()
        protected = provision_clean_demo_session()
        expire(protected)
        Organization.objects.filter(pk=protected.organization.pk).update(
            purpose=Organization.Purpose.CANONICAL_DEMO
        )

        plan = plan_demo_session_cleanup()
        categories = {
            item.session_id: item.category
            for item in plan.items
        }

        self.assertEqual(categories[str(active.demo_session.identifier)], 'skipped')
        self.assertEqual(categories[str(protected.demo_session.identifier)], 'blocked')

    def test_invalid_command_arguments_and_missing_target_fail(self):
        for value in ('invalid', '2026-01-01T00:00:00'):
            with self.assertRaises(CommandError):
                call_command('cleanup_demo_sessions', before=value)
        for limit in (0, -1, MAX_CLEANUP_LIMIT + 1):
            with self.assertRaises(CommandError):
                call_command('cleanup_demo_sessions', limit=limit)
        with self.assertRaises(CommandError):
            call_command(
                'cleanup_demo_sessions',
                session_id='00000000-0000-0000-0000-000000000001',
            )


class DemoCleanupEligibleDeletionTests(TestCase):
    def test_confirmed_clean_cleanup_deletes_only_ownership_unit(self):
        result = provision_clean_demo_session()
        expire(result)
        ids = (
            result.demo_session.pk,
            result.user.pk,
            result.membership.pk,
            result.organization.pk,
        )

        call_command('cleanup_demo_sessions', confirm=True, stdout=StringIO())

        self.assertFalse(DemoSession.objects.filter(pk=ids[0]).exists())
        self.assertFalse(get_user_model().objects.filter(pk=ids[1]).exists())
        self.assertFalse(OrganizationMembership.objects.filter(pk=ids[2]).exists())
        self.assertFalse(Organization.objects.filter(pk=ids[3]).exists())

    def test_confirmed_prepared_cleanup_deletes_complete_dependency_graph(self):
        result = provision_prepared_demo_session()
        expire(result)
        organization_id = result.organization.pk

        call_command(
            'cleanup_demo_sessions',
            confirm=True,
            session_id=str(result.demo_session.identifier),
            stdout=StringIO(),
        )

        self.assertFalse(Organization.objects.filter(pk=organization_id).exists())
        self.assertFalse(TheSched.objects.filter(organization_id=organization_id).exists())
        self.assertFalse(Schools.schools_list.filter(organization_id=organization_id).exists())
        self.assertFalse(Instructor.objects.filter(organization_id=organization_id).exists())
        self.assertFalse(Course.objects.filter(organization_id=organization_id).exists())
        self.assertFalse(Certification.objects.filter(organization_id=organization_id).exists())
        self.assertFalse(Locations.objects.filter(organization_id=organization_id).exists())
        self.assertFalse(
            InstructorScheduleParticipation.objects.filter(
                organization_id=organization_id
            ).exists()
        )
        self.assertFalse(
            InstructorScheduleAvailability.objects.filter(
                organization_id=organization_id
            ).exists()
        )

    def test_exact_cutoff_and_status_policy(self):
        cutoff = timezone.now() + timedelta(minutes=10)
        cases = (
            (-1, DemoSession.Status.ACTIVE, 'eligible'),
            (0, DemoSession.Status.EXPIRING, 'eligible'),
            (-1, DemoSession.Status.FAILED, 'eligible'),
            (-1, DemoSession.Status.DELETING, 'eligible'),
            (-1, DemoSession.Status.PROVISIONING, 'blocked'),
            (1, DemoSession.Status.ACTIVE, 'skipped'),
        )
        for seconds, status, expected in cases:
            with self.subTest(status=status, seconds=seconds):
                result = provision_clean_demo_session()
                DemoSession.objects.filter(pk=result.demo_session.pk).update(
                    expires_at=cutoff + timedelta(seconds=seconds),
                    status=status,
                )
                plan = plan_demo_session_cleanup(
                    cutoff=cutoff,
                    session_id=result.demo_session.identifier,
                )
                self.assertEqual(plan.items[0].category, expected)

    def test_deleting_retry_and_partial_child_absence_are_safe(self):
        result = provision_prepared_demo_session()
        expire(result, status=DemoSession.Status.DELETING)
        Locations.objects.filter(
            organization=result.organization,
            loc_name='Demo Field',
        ).delete()

        outcome = cleanup_demo_session(
            result.demo_session.identifier,
            cutoff=timezone.now(),
        )

        self.assertEqual(outcome.category, 'deleted')
        self.assertFalse(
            DemoSession.objects.filter(identifier=result.demo_session.identifier).exists()
        )


class DemoCleanupIsolationTests(TestCase):
    def test_cleanup_preserves_active_foreign_and_privileged_ownership(self):
        expired = provision_prepared_demo_session()
        expire(expired)
        active_clean = provision_clean_demo_session()
        active_prepared = provision_prepared_demo_session()
        privileged = provision_clean_demo_session()
        expire(privileged)
        get_user_model().objects.filter(pk=privileged.user.pk).update(is_staff=True)
        customer = Organization.objects.create(name='Customer')
        canonical = Organization.objects.create(
            name='Canonical',
            purpose=Organization.Purpose.CANONICAL_DEMO,
        )
        default, _created = Organization.objects.get_or_create(
            name='Default Organization'
        )
        protected_ids = {
            active_clean.organization.pk,
            active_prepared.organization.pk,
            privileged.organization.pk,
            customer.pk,
            canonical.pk,
            default.pk,
        }

        result = cleanup_expired_demo_sessions(plan=plan_demo_session_cleanup())

        self.assertEqual(len(result.deleted), 1)
        self.assertTrue(
            all(Organization.objects.filter(pk=pk).exists() for pk in protected_ids)
        )
        self.assertTrue(DemoSession.objects.filter(pk=privileged.demo_session.pk).exists())

    def test_identically_named_foreign_records_are_untouched(self):
        expired = provision_prepared_demo_session()
        expire(expired)
        foreign = Organization.objects.create(name='Foreign Customer')
        location = Locations.objects.create(
            organization=foreign,
            loc_name=DEMO_SCENARIO['locations'][0]['name'],
            loc_short='SAFE',
        )

        cleanup_expired_demo_sessions(plan=plan_demo_session_cleanup())

        self.assertTrue(Locations.objects.filter(pk=location.pk).exists())
        self.assertTrue(Organization.objects.filter(pk=foreign.pk).exists())


class DemoCleanupAtomicityTests(TestCase):
    def model_counts(self):
        return (
            get_user_model().objects.count(),
            Organization.objects.count(),
            OrganizationMembership.objects.count(),
            DemoSession.objects.count(),
            TheSched.objects.count(),
            Instructor.objects.count(),
            Course.objects.count(),
            Locations.objects.count(),
        )

    def test_each_deletion_stage_failure_rolls_back_and_records_sanitized_metadata(self):
        stages = (
            'scheduler_app.demo_session_cleanup._delete_schedule_dependents',
            'scheduler_app.demo_session_cleanup._delete_schools',
            'scheduler_app.demo_session_cleanup._delete_instructors',
            'scheduler_app.demo_session_cleanup._delete_courses',
            'scheduler_app.demo_session_cleanup._delete_reference_records',
            'scheduler_app.demo_session_cleanup._delete_ownership',
        )
        for stage in stages:
            with self.subTest(stage=stage):
                result = provision_prepared_demo_session()
                expire(result)
                baseline = self.model_counts()
                plan = plan_demo_session_cleanup(
                    session_id=result.demo_session.identifier
                )
                with patch(stage, side_effect=RuntimeError('secret value 123')):
                    batch = cleanup_expired_demo_sessions(plan=plan)

                self.assertEqual(len(batch.failed), 1)
                self.assertEqual(self.model_counts(), baseline)
                result.demo_session.refresh_from_db()
                self.assertEqual(result.demo_session.status, DemoSession.Status.EXPIRING)
                self.assertEqual(result.demo_session.cleanup_attempt_count, 1)
                self.assertNotIn('secret value 123', result.demo_session.last_cleanup_error)

    def test_one_failure_does_not_block_later_session(self):
        first = provision_clean_demo_session()
        second = provision_clean_demo_session()
        expire(first)
        expire(second)
        plan = plan_demo_session_cleanup()
        original = cleanup_demo_session

        def fail_first(session_id, *, cutoff):
            if str(session_id) == str(first.demo_session.identifier):
                raise RuntimeError('first fails')
            return original(session_id, cutoff=cutoff)

        with patch(
            'scheduler_app.demo_session_cleanup.cleanup_demo_session',
            side_effect=fail_first,
        ):
            batch = cleanup_expired_demo_sessions(plan=plan)

        self.assertEqual(len(batch.failed), 1)
        self.assertEqual(len(batch.deleted), 1)
        self.assertTrue(DemoSession.objects.filter(pk=first.demo_session.pk).exists())
        self.assertFalse(DemoSession.objects.filter(pk=second.demo_session.pk).exists())


class DemoCleanupBatchTests(TestCase):
    def test_execution_rechecks_stale_plan_and_handles_disappeared_session(self):
        became_unexpired = provision_clean_demo_session()
        disappeared = provision_clean_demo_session()
        cutoff = expire(became_unexpired)
        expire(disappeared, cutoff=cutoff)
        plan = plan_demo_session_cleanup(cutoff=cutoff)

        DemoSession.objects.filter(pk=became_unexpired.demo_session.pk).update(
            expires_at=cutoff + timedelta(minutes=1)
        )
        DemoSession.objects.filter(pk=disappeared.demo_session.pk).delete()

        batch = cleanup_expired_demo_sessions(plan=plan)

        self.assertEqual(len(batch.deleted), 0)
        self.assertEqual(len(batch.skipped), 2)
        self.assertTrue(
            DemoSession.objects.filter(pk=became_unexpired.demo_session.pk).exists()
        )
        self.assertTrue(
            Organization.objects.filter(pk=disappeared.organization.pk).exists()
        )

    def test_limit_ordering_more_remaining_and_idempotency(self):
        sessions = []
        cutoff = timezone.now() + timedelta(minutes=10)
        for index in range(3):
            result = provision_clean_demo_session()
            DemoSession.objects.filter(pk=result.demo_session.pk).update(
                expires_at=cutoff - timedelta(minutes=3 - index),
                status=DemoSession.Status.EXPIRING,
            )
            result.demo_session.refresh_from_db()
            sessions.append(result)

        plan = plan_demo_session_cleanup(cutoff=cutoff, limit=2)
        self.assertTrue(plan.more_remaining)
        self.assertEqual(
            [item.session_id for item in plan.items],
            [str(sessions[0].demo_session.identifier), str(sessions[1].demo_session.identifier)],
        )
        batch = cleanup_expired_demo_sessions(plan=plan)
        self.assertEqual(len(batch.deleted), 2)
        self.assertTrue(DemoSession.objects.filter(pk=sessions[2].demo_session.pk).exists())

        second = cleanup_expired_demo_sessions(
            plan=plan_demo_session_cleanup(cutoff=cutoff)
        )
        self.assertEqual(len(second.deleted), 1)
        self.assertEqual(
            cleanup_expired_demo_sessions(
                plan=plan_demo_session_cleanup(cutoff=cutoff)
            ).deleted,
            [],
        )

    def test_default_and_maximum_limits_are_supported(self):
        self.assertEqual(plan_demo_session_cleanup().limit, DEFAULT_CLEANUP_LIMIT)
        self.assertEqual(
            plan_demo_session_cleanup(limit=MAX_CLEANUP_LIMIT).limit,
            MAX_CLEANUP_LIMIT,
        )
