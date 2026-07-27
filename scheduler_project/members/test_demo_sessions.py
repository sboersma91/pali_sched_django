from datetime import timedelta
import uuid

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from scheduler_app.models import Course, Instructor, Locations, Schools, TheSched

from .admin import DemoSessionAdmin, OrganizationAdmin
from .models import (
    DEFAULT_ORGANIZATION_NAME,
    DemoSession,
    Organization,
    OrganizationMembership,
    get_default_organization,
)


class OrganizationPurposeTests(TestCase):
    def test_ordinary_and_default_organizations_are_customer_purpose(self):
        ordinary = Organization.objects.create(name='Ordinary Organization')
        default = get_default_organization()

        self.assertEqual(ordinary.purpose, Organization.Purpose.CUSTOMER)
        self.assertEqual(default.name, DEFAULT_ORGANIZATION_NAME)
        self.assertEqual(default.purpose, Organization.Purpose.CUSTOMER)

    def test_demo_purposes_require_explicit_assignment(self):
        canonical = Organization.objects.create(
            name='Explicit Canonical',
            purpose=Organization.Purpose.CANONICAL_DEMO,
        )
        temporary = Organization.objects.create(
            name='Explicit Temporary',
            purpose=Organization.Purpose.TEMPORARY_DEMO,
        )
        misleading = Organization.objects.create(name='temporary_demo')

        self.assertEqual(canonical.purpose, Organization.Purpose.CANONICAL_DEMO)
        self.assertEqual(temporary.purpose, Organization.Purpose.TEMPORARY_DEMO)
        self.assertEqual(misleading.purpose, Organization.Purpose.CUSTOMER)

    def test_invalid_purpose_fails_model_validation(self):
        organization = Organization(name='Invalid Purpose', purpose='invalid')

        with self.assertRaises(ValidationError):
            organization.full_clean()

    def test_invalid_purpose_is_rejected_by_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Organization.objects.create(name='Invalid Purpose', purpose='invalid')


class DemoSessionTestBase:
    def make_owner(self, name='visitor', *, purpose=Organization.Purpose.TEMPORARY_DEMO):
        organization = Organization.objects.create(
            name=f'{name} Organization',
            purpose=purpose,
        )
        user = get_user_model().objects.create_user(username=name)
        OrganizationMembership.objects.create(user=user, organization=organization)
        return user, organization

    def make_session(self, user, organization, **overrides):
        values = {
            'user': user,
            'organization': organization,
            'mode': DemoSession.Mode.CLEAN,
            'status': DemoSession.Status.ACTIVE,
            'expires_at': timezone.now() + timedelta(hours=1),
        }
        values.update(overrides)
        return DemoSession(**values)


class ValidDemoSessionTests(DemoSessionTestBase, TestCase):
    def test_valid_prepared_session_has_identifier_timestamps_and_defaults(self):
        user, organization = self.make_owner()
        session = self.make_session(
            user,
            organization,
            mode=DemoSession.Mode.PREPARED,
            scenario_version='canonical-v1',
        )
        session.save()

        self.assertIsInstance(session.identifier, uuid.UUID)
        self.assertTrue(session.created_at)
        self.assertTrue(session.updated_at)
        self.assertTrue(session.last_activity_at)
        self.assertEqual(session.cleanup_attempt_count, 0)
        self.assertEqual(session.last_cleanup_error, '')

    def test_clean_session_is_valid_without_scenario_version(self):
        user, organization = self.make_owner()

        session = self.make_session(user, organization)
        session.full_clean()
        session.save()

        self.assertEqual(session.scenario_version, '')

    def test_generated_identifiers_are_unique(self):
        first_user, first_org = self.make_owner('first')
        second_user, second_org = self.make_owner('second')

        first = self.make_session(first_user, first_org)
        second = self.make_session(second_user, second_org)

        self.assertNotEqual(first.identifier, second.identifier)


class DemoSessionOwnershipValidationTests(DemoSessionTestBase, TestCase):
    def assert_invalid(self, session):
        with self.assertRaises(ValidationError):
            session.full_clean()

    def test_customer_canonical_and_default_organizations_are_rejected(self):
        for index, purpose in enumerate(
            (Organization.Purpose.CUSTOMER, Organization.Purpose.CANONICAL_DEMO)
        ):
            user, organization = self.make_owner(f'owner-{index}', purpose=purpose)
            self.assert_invalid(self.make_session(user, organization))

        default = get_default_organization()
        user = get_user_model().objects.create_user(username='default-owner')
        OrganizationMembership.objects.create(user=user, organization=default)
        self.assert_invalid(self.make_session(user, default))

    def test_missing_or_mismatched_membership_is_rejected(self):
        organization = Organization.objects.create(
            name='Temporary',
            purpose=Organization.Purpose.TEMPORARY_DEMO,
        )
        no_membership = get_user_model().objects.create_user(username='unowned')
        self.assert_invalid(self.make_session(no_membership, organization))

        other = Organization.objects.create(name='Other')
        OrganizationMembership.objects.create(
            user=no_membership,
            organization=other,
        )
        self.assert_invalid(self.make_session(no_membership, organization))

    def test_staff_and_superusers_are_rejected(self):
        staff, staff_org = self.make_owner('staff')
        staff.is_staff = True
        staff.save()
        self.assert_invalid(self.make_session(staff, staff_org))

        superuser, super_org = self.make_owner('superuser')
        superuser.is_superuser = True
        superuser.save()
        self.assert_invalid(self.make_session(superuser, super_org))

    def test_user_and_organization_uniqueness_validate(self):
        user, organization = self.make_owner()
        self.make_session(user, organization).save()

        self.assert_invalid(self.make_session(user, organization))

    def test_user_and_organization_uniqueness_are_database_enforced(self):
        user, organization = self.make_owner()
        self.make_session(user, organization).save()
        other_user, other_organization = self.make_owner('other')

        with self.assertRaises(IntegrityError), transaction.atomic():
            DemoSession.objects.bulk_create(
                [self.make_session(user, other_organization)]
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            DemoSession.objects.bulk_create(
                [self.make_session(other_user, organization)]
            )


class DemoSessionLifecycleValidationTests(DemoSessionTestBase, TestCase):
    def assert_invalid(self, **overrides):
        user, organization = self.make_owner(
            f'visitor-{get_user_model().objects.count()}'
        )
        with self.assertRaises(ValidationError):
            self.make_session(user, organization, **overrides).full_clean()

    def test_missing_and_past_expiration_are_rejected(self):
        self.assert_invalid(expires_at=None)
        self.assert_invalid(expires_at=timezone.now() - timedelta(seconds=1))

    def test_future_expiration_is_valid(self):
        user, organization = self.make_owner()
        self.make_session(user, organization).full_clean()

    def test_invalid_mode_and_status_are_rejected(self):
        self.assert_invalid(mode='invalid')
        self.assert_invalid(status='invalid')

    def test_negative_cleanup_attempts_are_rejected(self):
        self.assert_invalid(cleanup_attempt_count=-1)

    def test_prepared_requires_scenario_version_but_clean_does_not(self):
        self.assert_invalid(mode=DemoSession.Mode.PREPARED, scenario_version='')
        user, organization = self.make_owner('clean')
        self.make_session(
            user,
            organization,
            mode=DemoSession.Mode.CLEAN,
            scenario_version='',
        ).full_clean()


class DemoSessionMutationConsistencyTests(DemoSessionTestBase, TestCase):
    def setUp(self):
        self.user, self.organization = self.make_owner()
        self.session = self.make_session(self.user, self.organization)
        self.session.save()

    def assert_session_is_now_invalid(self):
        self.session.refresh_from_db()
        with self.assertRaises(ValidationError):
            self.session.full_clean()

    def test_membership_change_is_detected_without_silent_repair(self):
        other = Organization.objects.create(name='Other Organization')
        membership = self.user.organization_membership
        membership.organization = other
        membership.save()

        self.assert_session_is_now_invalid()
        self.assertEqual(membership.organization, other)

    def test_organization_purpose_change_is_detected_without_signal_rewrite(self):
        self.organization.purpose = Organization.Purpose.CUSTOMER
        self.organization.save()

        self.assert_session_is_now_invalid()
        self.assertTrue(DemoSession.objects.filter(pk=self.session.pk).exists())

    def test_staff_and_superuser_promotions_are_detected(self):
        self.user.is_staff = True
        self.user.save()
        self.assert_session_is_now_invalid()

        self.user.is_staff = False
        self.user.is_superuser = True
        self.user.save()
        self.assert_session_is_now_invalid()


class DemoSessionSideEffectTests(DemoSessionTestBase, TestCase):
    def test_validation_and_creation_have_no_provisioning_side_effects(self):
        user, organization = self.make_owner()
        user.set_password('unchanged-secret')
        user.save()
        counts_before = {
            'users': get_user_model().objects.count(),
            'organizations': Organization.objects.count(),
            'memberships': OrganizationMembership.objects.count(),
            'locations': Locations.objects.count(),
            'courses': Course.objects.count(),
            'schools': Schools.schools_list.count(),
            'instructors': Instructor.objects.count(),
            'schedules': TheSched.objects.count(),
            'django_sessions': Session.objects.count(),
        }
        password_before = user.password

        session = self.make_session(user, organization)
        session.full_clean()
        session.save()

        self.assertEqual(
            counts_before,
            {
                'users': get_user_model().objects.count(),
                'organizations': Organization.objects.count(),
                'memberships': OrganizationMembership.objects.count(),
                'locations': Locations.objects.count(),
                'courses': Course.objects.count(),
                'schools': Schools.schools_list.count(),
                'instructors': Instructor.objects.count(),
                'schedules': TheSched.objects.count(),
                'django_sessions': Session.objects.count(),
            },
        )
        user.refresh_from_db()
        self.assertEqual(user.password, password_before)


class DemoSessionAdminTests(TestCase):
    def test_admin_exposes_safe_ownership_and_lifecycle_fields(self):
        session_admin = admin.site._registry[DemoSession]
        organization_admin = admin.site._registry[Organization]

        self.assertIsInstance(session_admin, DemoSessionAdmin)
        self.assertIn('identifier', session_admin.list_display)
        self.assertIn('user', session_admin.list_display)
        self.assertIn('organization', session_admin.list_display)
        self.assertEqual(
            session_admin.search_fields,
            ('identifier', 'user__username', 'organization__name'),
        )
        self.assertNotIn('password', session_admin.list_display)
        self.assertNotIn('session_key', session_admin.list_display)
        self.assertIsInstance(organization_admin, OrganizationAdmin)
        self.assertIn('purpose', organization_admin.list_display)
        self.assertIn('purpose', organization_admin.list_filter)
        self.assertEqual(
            organization_admin.get_readonly_fields(None, Organization()),
            ('purpose',),
        )


class OrganizationPurposeMigrationTests(TransactionTestCase):
    reset_sequences = True

    migrate_from = [('members', '0001_initial')]
    migrate_to = [('members', '0002_organization_purpose_demosession')]

    def test_existing_organizations_receive_customer_default_without_new_rows(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        OldOrganization = old_apps.get_model('members', 'Organization')
        OldOrganization.objects.create(name='Existing Organization')
        before = OldOrganization.objects.count()

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        NewOrganization = new_apps.get_model('members', 'Organization')
        NewDemoSession = new_apps.get_model('members', 'DemoSession')

        self.assertEqual(NewOrganization.objects.count(), before)
        self.assertEqual(
            NewOrganization.objects.get(name='Existing Organization').purpose,
            'customer',
        )
        self.assertEqual(NewDemoSession.objects.count(), 0)

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()
