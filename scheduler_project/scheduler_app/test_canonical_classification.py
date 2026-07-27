from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from members.models import (
    DEFAULT_ORGANIZATION_NAME,
    DemoSession,
    Organization,
    OrganizationMembership,
)

from .canonical_classification import (
    CanonicalClassificationError,
    classify_canonical_demo_organization,
)
from .models import Course, Instructor, Locations, Schools, TheSched


DEMO_IDENTIFIER = 'Configured Demo Organization'
ENABLED_SETTINGS = {
    'DEMO_SCAFFOLDING_ENABLED': True,
    'DEMO_ORGANIZATION_IDENTIFIER': DEMO_IDENTIFIER,
}


class CanonicalClassificationTestBase:
    def create_target(self, *, purpose=Organization.Purpose.CUSTOMER):
        organization = Organization.objects.create(
            name=DEMO_IDENTIFIER,
            purpose=purpose,
        )
        user = get_user_model().objects.create_user(username='demo-operator')
        membership = OrganizationMembership.objects.create(
            user=user,
            organization=organization,
        )
        return organization, user, membership


class CanonicalClassificationCommandSafetyTests(
    CanonicalClassificationTestBase,
    TestCase,
):
    @override_settings(**ENABLED_SETTINGS)
    def test_confirmation_is_mandatory_and_performs_no_write(self):
        organization, _user, _membership = self.create_target()

        with self.assertRaisesMessage(CommandError, '--confirm is required'):
            call_command(
                'classify_canonical_demo',
                organization=DEMO_IDENTIFIER,
            )

        organization.refresh_from_db()
        self.assertEqual(organization.purpose, Organization.Purpose.CUSTOMER)

    @override_settings(
        DEBUG=True,
        DEMO_SCAFFOLDING_ENABLED=False,
        DEMO_ORGANIZATION_IDENTIFIER=DEMO_IDENTIFIER,
    )
    def test_debug_alone_does_not_authorize_when_scaffolding_is_disabled(self):
        organization, _user, _membership = self.create_target()

        with self.assertRaisesMessage(CommandError, 'disabled'):
            call_command(
                'classify_canonical_demo',
                organization=DEMO_IDENTIFIER,
                confirm=True,
            )

        organization.refresh_from_db()
        self.assertEqual(organization.purpose, Organization.Purpose.CUSTOMER)

    @override_settings(
        DEMO_SCAFFOLDING_ENABLED=True,
        DEMO_ORGANIZATION_IDENTIFIER='',
    )
    def test_blank_configured_identifier_is_rejected(self):
        with self.assertRaisesMessage(CommandError, 'No allowed'):
            call_command(
                'classify_canonical_demo',
                organization=DEMO_IDENTIFIER,
                confirm=True,
            )

    @override_settings(**ENABLED_SETTINGS)
    def test_exact_identifier_mismatch_and_partial_match_are_rejected(self):
        organization, _user, _membership = self.create_target()

        for identifier in ('Foreign Organization', 'Configured Demo'):
            with self.assertRaisesMessage(CommandError, 'does not exactly match'):
                call_command(
                    'classify_canonical_demo',
                    organization=identifier,
                    confirm=True,
                )

        organization.refresh_from_db()
        self.assertEqual(organization.purpose, Organization.Purpose.CUSTOMER)


@override_settings(**ENABLED_SETTINGS)
class CanonicalClassificationSuccessTests(
    CanonicalClassificationTestBase,
    TestCase,
):
    def test_command_classifies_exact_customer_and_reports_transition(self):
        organization, _user, _membership = self.create_target()
        output = StringIO()

        call_command(
            'classify_canonical_demo',
            organization=DEMO_IDENTIFIER,
            confirm=True,
            stdout=output,
        )

        organization.refresh_from_db()
        self.assertEqual(
            organization.purpose,
            Organization.Purpose.CANONICAL_DEMO,
        )
        self.assertIn('Current purpose: customer', output.getvalue())
        self.assertIn('Proposed purpose: canonical_demo', output.getvalue())
        self.assertIn('Stored purpose: canonical_demo', output.getvalue())

    def test_service_changes_only_purpose_and_reports_structured_result(self):
        organization, user, membership = self.create_target()
        location = Locations.objects.create(
            organization=organization,
            loc_name='Existing Location',
            loc_short='EX',
        )
        original = {
            'name': organization.name,
            'created_at': organization.created_at,
            'updated_at': organization.updated_at,
            'membership_id': membership.pk,
            'location_id': location.pk,
        }

        result = classify_canonical_demo_organization(DEMO_IDENTIFIER)

        organization.refresh_from_db()
        self.assertEqual(result.original_purpose, Organization.Purpose.CUSTOMER)
        self.assertEqual(
            result.final_purpose,
            Organization.Purpose.CANONICAL_DEMO,
        )
        self.assertFalse(result.already_canonical)
        self.assertEqual(organization.name, original['name'])
        self.assertEqual(organization.created_at, original['created_at'])
        self.assertEqual(organization.updated_at, original['updated_at'])
        self.assertEqual(user.organization_membership.pk, original['membership_id'])
        self.assertEqual(organization.locations.get().pk, original['location_id'])

    def test_service_locks_target_in_the_transactional_path(self):
        self.create_target()

        with patch.object(
            Organization.objects,
            'select_for_update',
            wraps=Organization.objects.select_for_update,
        ) as select_for_update:
            classify_canonical_demo_organization(DEMO_IDENTIFIER)

        select_for_update.assert_called_once_with()

    def test_already_canonical_is_noop_without_timestamp_churn(self):
        organization, _user, membership = self.create_target(
            purpose=Organization.Purpose.CANONICAL_DEMO,
        )
        updated_at = organization.updated_at

        result = classify_canonical_demo_organization(DEMO_IDENTIFIER)

        organization.refresh_from_db()
        self.assertTrue(result.already_canonical)
        self.assertEqual(result.original_purpose, 'canonical_demo')
        self.assertEqual(result.final_purpose, 'canonical_demo')
        self.assertEqual(organization.updated_at, updated_at)
        self.assertTrue(
            OrganizationMembership.objects.filter(pk=membership.pk).exists()
        )


@override_settings(**ENABLED_SETTINGS)
class CanonicalClassificationRefusalTests(
    CanonicalClassificationTestBase,
    TestCase,
):
    def test_default_organization_is_rejected(self):
        with self.settings(DEMO_ORGANIZATION_IDENTIFIER=DEFAULT_ORGANIZATION_NAME):
            Organization.objects.get_or_create(name=DEFAULT_ORGANIZATION_NAME)
            with self.assertRaisesMessage(
                CanonicalClassificationError,
                'can never be classified',
            ):
                classify_canonical_demo_organization(DEFAULT_ORGANIZATION_NAME)

    def test_missing_target_is_rejected_without_creation(self):
        before = Organization.objects.count()

        with self.assertRaisesMessage(
            CanonicalClassificationError,
            'does not exist',
        ):
            classify_canonical_demo_organization(DEMO_IDENTIFIER)

        self.assertEqual(Organization.objects.count(), before)

    def test_ambiguous_match_is_rejected_where_structurally_simulated(self):
        first, _user, _membership = self.create_target()
        second = Organization(name=DEMO_IDENTIFIER)

        with patch(
            'scheduler_app.canonical_classification._locked_matches',
            return_value=[first, second],
        ):
            with self.assertRaisesMessage(
                CanonicalClassificationError,
                'ambiguous',
            ):
                classify_canonical_demo_organization(DEMO_IDENTIFIER)

    def test_temporary_organization_is_rejected(self):
        organization, _user, _membership = self.create_target(
            purpose=Organization.Purpose.TEMPORARY_DEMO,
        )

        with self.assertRaisesMessage(
            CanonicalClassificationError,
            'temporary demo organization',
        ):
            classify_canonical_demo_organization(DEMO_IDENTIFIER)

        organization.refresh_from_db()
        self.assertEqual(
            organization.purpose,
            Organization.Purpose.TEMPORARY_DEMO,
        )

    def test_organization_with_demo_session_is_rejected(self):
        organization, user, _membership = self.create_target(
            purpose=Organization.Purpose.TEMPORARY_DEMO,
        )
        DemoSession.objects.create(
            user=user,
            organization=organization,
            mode=DemoSession.Mode.CLEAN,
            status=DemoSession.Status.ACTIVE,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        Organization.objects.filter(pk=organization.pk).update(
            purpose=Organization.Purpose.CUSTOMER
        )

        with self.assertRaisesMessage(
            CanonicalClassificationError,
            'owned by a DemoSession',
        ):
            classify_canonical_demo_organization(DEMO_IDENTIFIER)

    def test_missing_membership_is_rejected(self):
        organization = Organization.objects.create(name=DEMO_IDENTIFIER)

        with self.assertRaisesMessage(
            CanonicalClassificationError,
            'existing organization membership',
        ):
            classify_canonical_demo_organization(DEMO_IDENTIFIER)

        organization.refresh_from_db()
        self.assertEqual(organization.purpose, Organization.Purpose.CUSTOMER)


@override_settings(**ENABLED_SETTINGS)
class CanonicalClassificationAtomicityTests(
    CanonicalClassificationTestBase,
    TestCase,
):
    def test_validation_failure_after_assignment_rolls_back(self):
        organization, _user, _membership = self.create_target()

        with patch.object(
            Organization,
            'full_clean',
            side_effect=ValidationError('injected validation failure'),
        ):
            with self.assertRaisesMessage(
                CanonicalClassificationError,
                'validation failed',
            ):
                classify_canonical_demo_organization(DEMO_IDENTIFIER)

        organization.refresh_from_db()
        self.assertEqual(organization.purpose, Organization.Purpose.CUSTOMER)

    def test_final_verification_failure_rolls_back(self):
        organization, _user, _membership = self.create_target()

        with patch(
            'scheduler_app.canonical_classification._verify_stored_purpose',
            side_effect=CanonicalClassificationError('injected verification failure'),
        ):
            with self.assertRaisesMessage(
                CanonicalClassificationError,
                'verification failure',
            ):
                classify_canonical_demo_organization(DEMO_IDENTIFIER)

        organization.refresh_from_db()
        self.assertEqual(organization.purpose, Organization.Purpose.CUSTOMER)

    def test_classification_has_no_provisioning_or_operational_side_effects(self):
        organization, user, _membership = self.create_target()
        user.set_password('unchanged-secret')
        user.save()
        password = user.password
        counts = {
            'users': get_user_model().objects.count(),
            'organizations': Organization.objects.count(),
            'memberships': OrganizationMembership.objects.count(),
            'demo_sessions': DemoSession.objects.count(),
            'locations': Locations.objects.count(),
            'courses': Course.objects.count(),
            'schools': Schools.schools_list.count(),
            'instructors': Instructor.objects.count(),
            'schedules': TheSched.objects.count(),
            'browser_sessions': Session.objects.count(),
        }

        classify_canonical_demo_organization(DEMO_IDENTIFIER)

        self.assertEqual(
            counts,
            {
                'users': get_user_model().objects.count(),
                'organizations': Organization.objects.count(),
                'memberships': OrganizationMembership.objects.count(),
                'demo_sessions': DemoSession.objects.count(),
                'locations': Locations.objects.count(),
                'courses': Course.objects.count(),
                'schools': Schools.schools_list.count(),
                'instructors': Instructor.objects.count(),
                'schedules': TheSched.objects.count(),
                'browser_sessions': Session.objects.count(),
            },
        )
        user.refresh_from_db()
        self.assertEqual(user.password, password)
        self.assertFalse(TheSched.objects.filter(organization=organization).exists())
