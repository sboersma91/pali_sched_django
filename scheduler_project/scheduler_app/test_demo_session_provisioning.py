from datetime import timedelta
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from members.models import (
    DEFAULT_ORGANIZATION_NAME,
    DemoSession,
    Organization,
    OrganizationMembership,
)

from .demo_session_provisioning import (
    DEFAULT_CLEAN_DEMO_SESSION_LIFETIME,
    IDENTITY_COLLISION_RETRY_LIMIT,
    MAX_CLEAN_DEMO_SESSION_LIFETIME,
    CleanDemoIdentityCollisionError,
    CleanDemoLifetimeError,
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


def uuid_sequence(*values):
    iterator = iter(values)
    return lambda: next(iterator)


class CleanDemoProvisioningSuccessTests(TestCase):
    def test_creates_one_aligned_active_clean_ownership_unit(self):
        counts = (
            get_user_model().objects.count(),
            Organization.objects.count(),
            OrganizationMembership.objects.count(),
            DemoSession.objects.count(),
        )

        result = provision_clean_demo_session()

        self.assertTrue(result.completed)
        self.assertEqual(get_user_model().objects.count(), counts[0] + 1)
        self.assertEqual(Organization.objects.count(), counts[1] + 1)
        self.assertEqual(OrganizationMembership.objects.count(), counts[2] + 1)
        self.assertEqual(DemoSession.objects.count(), counts[3] + 1)
        self.assertEqual(result.membership.user, result.user)
        self.assertEqual(result.membership.organization, result.organization)
        self.assertEqual(result.demo_session.user, result.user)
        self.assertEqual(result.demo_session.organization, result.organization)
        self.assertEqual(result.demo_session.mode, DemoSession.Mode.CLEAN)
        self.assertEqual(result.demo_session.status, DemoSession.Status.ACTIVE)

    def test_temporary_identity_and_cleanup_defaults_are_safe(self):
        result = provision_clean_demo_session()

        self.assertTrue(result.user.is_active)
        self.assertFalse(result.user.is_staff)
        self.assertFalse(result.user.is_superuser)
        self.assertFalse(result.user.has_usable_password())
        self.assertEqual(result.user.email, '')
        self.assertFalse(result.user.groups.exists())
        self.assertFalse(result.user.user_permissions.exists())
        self.assertEqual(
            result.organization.purpose,
            Organization.Purpose.TEMPORARY_DEMO,
        )
        self.assertNotEqual(result.organization.name, DEFAULT_ORGANIZATION_NAME)
        self.assertEqual(result.demo_session.scenario_version, '')
        self.assertEqual(result.demo_session.cleanup_attempt_count, 0)
        self.assertEqual(result.demo_session.last_cleanup_error, '')

    def test_trusted_clock_sets_activity_and_expiration(self):
        now = timezone.now()
        lifetime = timedelta(minutes=45)

        result = provision_clean_demo_session(
            lifetime=lifetime,
            clock=lambda: now,
        )

        self.assertEqual(result.demo_session.last_activity_at, now)
        self.assertEqual(result.demo_session.expires_at, now + lifetime)
        self.assertEqual(result.expires_at, now + lifetime)

    def test_new_organization_has_no_operational_records(self):
        organization = provision_clean_demo_session().organization

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


class CleanDemoProvisioningLifetimeTests(TestCase):
    def test_default_and_positive_custom_lifetimes_are_accepted(self):
        now = timezone.now()
        default = provision_clean_demo_session(clock=lambda: now)
        custom = provision_clean_demo_session(
            lifetime=timedelta(minutes=1),
            clock=lambda: now,
        )

        self.assertEqual(
            default.expires_at,
            now + DEFAULT_CLEAN_DEMO_SESSION_LIFETIME,
        )
        self.assertEqual(custom.expires_at, now + timedelta(minutes=1))

    def test_zero_negative_excessive_and_non_timedelta_lifetimes_are_rejected(self):
        invalid = (
            timedelta(0),
            timedelta(seconds=-1),
            MAX_CLEAN_DEMO_SESSION_LIFETIME + timedelta(seconds=1),
            None,
        )

        for lifetime in invalid:
            with self.assertRaises(CleanDemoLifetimeError):
                provision_clean_demo_session(lifetime=lifetime)

        self.assertEqual(DemoSession.objects.count(), 0)

    def test_naive_clock_is_rejected_without_creating_records(self):
        naive = timezone.now().replace(tzinfo=None)

        with self.assertRaisesMessage(CleanDemoLifetimeError, 'timezone-aware'):
            provision_clean_demo_session(clock=lambda: naive)

        self.assertEqual(DemoSession.objects.count(), 0)


class CleanDemoProvisioningCollisionTests(TestCase):
    def test_username_collision_retries_without_reusing_user(self):
        collision = uuid.uuid4()
        replacement = uuid.uuid4()
        organization_token = uuid.uuid4()
        existing = get_user_model().objects.create_user(
            username=f'demo_{collision.hex}'
        )

        result = provision_clean_demo_session(
            identity_factory=uuid_sequence(
                collision,
                replacement,
                organization_token,
            )
        )

        self.assertNotEqual(result.user.pk, existing.pk)
        self.assertEqual(result.user.username, f'demo_{replacement.hex}')

    def test_organization_collision_retries_without_reusing_organization(self):
        user_token = uuid.uuid4()
        collision = uuid.uuid4()
        replacement = uuid.uuid4()
        existing = Organization.objects.create(
            name=f'Demo Session {collision.hex}',
            purpose=Organization.Purpose.TEMPORARY_DEMO,
        )

        result = provision_clean_demo_session(
            identity_factory=uuid_sequence(
                user_token,
                collision,
                replacement,
            )
        )

        self.assertNotEqual(result.organization.pk, existing.pk)
        self.assertEqual(
            result.organization.name,
            f'Demo Session {replacement.hex}',
        )

    def test_configured_canonical_name_is_skipped_without_query_or_reuse(self):
        user_token = uuid.uuid4()
        reserved = uuid.uuid4()
        replacement = uuid.uuid4()

        with override_settings(
            DEMO_ORGANIZATION_IDENTIFIER=f'Demo Session {reserved.hex}'
        ):
            result = provision_clean_demo_session(
                identity_factory=uuid_sequence(
                    user_token,
                    reserved,
                    replacement,
                )
            )

        self.assertEqual(
            result.organization.name,
            f'Demo Session {replacement.hex}',
        )

    def test_username_retry_exhaustion_is_clear_and_atomic(self):
        collision = uuid.uuid4()
        existing = get_user_model().objects.create_user(
            username=f'demo_{collision.hex}'
        )
        factory = lambda: collision
        organization_count = Organization.objects.count()

        with self.assertRaisesMessage(
            CleanDemoIdentityCollisionError,
            'username within the retry limit',
        ):
            provision_clean_demo_session(identity_factory=factory)

        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertTrue(get_user_model().objects.filter(pk=existing.pk).exists())
        self.assertEqual(Organization.objects.count(), organization_count)

    def test_organization_retry_exhaustion_rolls_back_new_user(self):
        collision = uuid.uuid4()
        user_token = uuid.uuid4()
        existing = Organization.objects.create(
            name=f'Demo Session {collision.hex}',
            purpose=Organization.Purpose.TEMPORARY_DEMO,
        )
        tokens = [user_token] + [collision] * IDENTITY_COLLISION_RETRY_LIMIT
        organization_count = Organization.objects.count()

        with self.assertRaisesMessage(
            CleanDemoIdentityCollisionError,
            'organization name within the retry limit',
        ):
            provision_clean_demo_session(
                identity_factory=uuid_sequence(*tokens)
            )

        self.assertEqual(get_user_model().objects.count(), 0)
        self.assertEqual(Organization.objects.count(), organization_count)
        self.assertTrue(Organization.objects.filter(pk=existing.pk).exists())


class CleanDemoProvisioningAtomicityTests(TestCase):
    def ownership_counts(self):
        return (
            get_user_model().objects.count(),
            Organization.objects.count(),
            OrganizationMembership.objects.count(),
            DemoSession.objects.count(),
        )

    def assert_failure_rolls_back(self, target, message):
        before = self.ownership_counts()
        with patch(target, side_effect=RuntimeError(message)):
            with self.assertRaisesMessage(RuntimeError, message):
                provision_clean_demo_session()
        self.assertEqual(self.ownership_counts(), before)

    def test_failure_after_user_creation_rolls_back(self):
        self.assert_failure_rolls_back(
            'scheduler_app.demo_session_provisioning._create_temporary_organization',
            'after user',
        )

    def test_failure_after_organization_creation_rolls_back(self):
        self.assert_failure_rolls_back(
            'scheduler_app.demo_session_provisioning._create_membership',
            'after organization',
        )

    def test_failure_after_membership_creation_rolls_back(self):
        self.assert_failure_rolls_back(
            'scheduler_app.demo_session_provisioning._build_demo_session',
            'after membership',
        )

    def test_failure_after_session_construction_rolls_back(self):
        with patch.object(
            DemoSession,
            'full_clean',
            side_effect=ValidationError('after construction'),
        ):
            before = self.ownership_counts()
            with self.assertRaisesMessage(ValidationError, 'after construction'):
                provision_clean_demo_session()
            self.assertEqual(self.ownership_counts(), before)

    def test_failure_during_final_verification_rolls_back(self):
        self.assert_failure_rolls_back(
            'scheduler_app.demo_session_provisioning._verify_clean_ownership_unit',
            'final verification',
        )


class CleanDemoProvisioningIsolationTests(TestCase):
    def test_existing_ownership_and_default_records_remain_unchanged(self):
        UserModel = get_user_model()
        default, _created = Organization.objects.get_or_create(
            name=DEFAULT_ORGANIZATION_NAME
        )
        canonical = Organization.objects.create(
            name='Canonical',
            purpose=Organization.Purpose.CANONICAL_DEMO,
        )
        customer = Organization.objects.create(name='Customer')
        temporary = Organization.objects.create(
            name='Existing Temporary',
            purpose=Organization.Purpose.TEMPORARY_DEMO,
        )
        existing_user = UserModel.objects.create_user(username='existing')
        membership = OrganizationMembership.objects.create(
            user=existing_user,
            organization=customer,
        )
        state = {
            item.pk: (item.name, item.purpose, item.updated_at)
            for item in (default, canonical, customer, temporary)
        }

        result = provision_clean_demo_session()

        for organization in (default, canonical, customer, temporary):
            organization.refresh_from_db()
            self.assertEqual(
                (organization.name, organization.purpose, organization.updated_at),
                state[organization.pk],
            )
        membership.refresh_from_db()
        self.assertEqual(membership.organization, customer)
        self.assertNotEqual(result.user, existing_user)
        self.assertNotIn(result.organization.pk, state)

    def test_canonical_services_and_authentication_are_not_invoked(self):
        with (
            patch(
                'scheduler_app.demo_scaffolding.inspect_demo_environment'
            ) as inspect,
            patch('scheduler_app.demo_scaffolding.apply_demo_reference_data') as apply,
            patch('scheduler_app.demo_scaffolding.reset_demo_environment') as reset,
            patch('django.contrib.auth.login') as login,
            patch('django.contrib.auth.authenticate') as authenticate,
        ):
            provision_clean_demo_session()

        inspect.assert_not_called()
        apply.assert_not_called()
        reset.assert_not_called()
        login.assert_not_called()
        authenticate.assert_not_called()
        self.assertEqual(Session.objects.count(), 0)

    def test_two_calls_create_distinct_unshared_ownership_units(self):
        first = provision_clean_demo_session()
        second = provision_clean_demo_session()

        self.assertNotEqual(first.user.pk, second.user.pk)
        self.assertNotEqual(first.organization.pk, second.organization.pk)
        self.assertNotEqual(
            first.demo_session.identifier,
            second.demo_session.identifier,
        )
        self.assertNotEqual(first.membership.pk, second.membership.pk)
        self.assertEqual(DemoSession.objects.count(), 2)
        self.assertEqual(OrganizationMembership.objects.count(), 2)
