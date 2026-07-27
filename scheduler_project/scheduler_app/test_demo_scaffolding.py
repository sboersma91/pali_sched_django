from copy import deepcopy
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from members.models import DEFAULT_ORGANIZATION_NAME, Organization, OrganizationMembership

from .demo_scaffolding import (
    DEMO_SCENARIO,
    DemoSafetyError,
    apply_demo_reference_data,
    inspect_demo_environment,
    reset_demo_environment,
)
from .instructor_assignment import run_instructor_assignment
from .models import (
    ActivityCertificationRequirement,
    Certification,
    Course,
    Instructor,
    InstructorCertification,
    InstructorScheduleAvailability,
    InstructorScheduleParticipation,
    Locations,
    Schools,
    TheSched,
)


DEMO_IDENTIFIER = 'Configured Demo Organization'
ENABLED_SETTINGS = {
    'DEMO_SCAFFOLDING_ENABLED': True,
    'DEMO_ORGANIZATION_IDENTIFIER': DEMO_IDENTIFIER,
}


def create_canonical_organization():
    return Organization.objects.create(
        name=DEMO_IDENTIFIER,
        purpose=Organization.Purpose.CANONICAL_DEMO,
    )


@override_settings(**ENABLED_SETTINGS)
class DemoScaffoldingInspectionTests(TestCase):
    def setUp(self):
        self.organization = create_canonical_organization()

    def test_safe_command_resolves_exact_target_and_reports_missing_creations(self):
        output = StringIO()

        call_command(
            'inspect_demo_environment',
            organization=DEMO_IDENTIFIER,
            stdout=output,
        )

        report = output.getvalue()
        self.assertIn(f'Organization: {DEMO_IDENTIFIER}', report)
        self.assertIn('CREATE (', report)
        self.assertIn('location: Demo Commons', report)
        self.assertIn('Safe target inspected; no changes were made.', report)

    def test_existing_records_are_unchanged_or_reconciled(self):
        location = Locations.objects.create(
            organization=self.organization,
            loc_name='Demo Commons',
            loc_short='OLD',
            availible=True,
        )
        foreign = Organization.objects.create(name='Foreign Organization')
        Locations.objects.create(
            organization=foreign,
            loc_name='Demo Field',
            loc_short='DF',
            availible=True,
        )

        result = inspect_demo_environment(DEMO_IDENTIFIER)

        self.assertTrue(any(
            item.record_type == 'location'
            and item.identity == location.loc_name
            for item in result.update
        ))
        self.assertTrue(any(
            item.record_type == 'location'
            and item.identity == 'Demo Field'
            for item in result.create
        ))

    def test_structured_result_separates_all_categories(self):
        result = inspect_demo_environment(DEMO_IDENTIFIER)

        self.assertIsInstance(result.create, list)
        self.assertIsInstance(result.update, list)
        self.assertIsInstance(result.reconcile, list)
        self.assertIsInstance(result.restore, list)
        self.assertIsInstance(result.unchanged, list)
        self.assertIsInstance(result.warnings, list)
        self.assertEqual(result.blockers, [])
        self.assertTrue(result.create)
        self.assertTrue(result.reconcile)
        self.assertTrue(result.restore)
        self.assertTrue(result.unchanged)
        self.assertTrue(result.warnings)

    def test_missing_generated_output_is_reported_as_future_restore(self):
        schedule = TheSched.objects.create(
            organization=self.organization,
            sched_name=DEMO_SCENARIO['schedule']['name'],
            sched_data={
                'version': 1,
                'manual_moves': [],
                'manual_instructor_overrides': [],
                'instructor_override_revision': 0,
            },
        )

        result = inspect_demo_environment(DEMO_IDENTIFIER)

        generated = next(
            item for item in result.restore
            if item.record_type == 'schedule generated output'
        )
        self.assertEqual(generated.identity, schedule.sched_name)
        self.assertFalse(generated.current['generated_schedule_present'])
        self.assertIn('future required action', generated.reason)

    def test_clean_operational_state_is_recognized_without_normalization(self):
        stored = {
            'version': 1,
            'generated_schedule': {'ags': ['Demo Cohort North 0']},
            'generation_complete': True,
            'manual_moves': [],
            'manual_instructor_overrides': [],
            'instructor_override_revision': 0,
        }
        TheSched.objects.create(
            organization=self.organization,
            sched_name=DEMO_SCENARIO['schedule']['name'],
            sched_data=deepcopy(stored),
        )

        result = inspect_demo_environment(DEMO_IDENTIFIER)

        self.assertTrue(any(
            item.record_type == 'schedule operational state'
            for item in result.unchanged
        ))
        self.assertFalse(any(
            item.record_type == 'schedule operational state'
            for item in result.restore
        ))

    def test_identically_named_foreign_records_do_not_satisfy_scenario(self):
        foreign = Organization.objects.create(name='Foreign Demo-Like Organization')
        for expected in DEMO_SCENARIO['locations']:
            Locations.objects.create(
                organization=foreign,
                loc_name=expected['name'],
                loc_short=expected['short'],
                availible=expected['available'],
            )

        result = inspect_demo_environment(DEMO_IDENTIFIER)

        planned_location_names = {
            item.identity
            for item in result.create
            if item.record_type == 'location'
        }
        self.assertEqual(
            planned_location_names,
            {item['name'] for item in DEMO_SCENARIO['locations']},
        )
        all_items = [
            item
            for _category, items in result.categories()
            for item in items
        ]
        self.assertFalse(any(
            'Foreign Demo-Like Organization' in item.identity
            for item in all_items
        ))

    def test_repeated_inspection_is_read_only_and_stable(self):
        user = get_user_model().objects.create_user(username='existing-demo-operator')
        membership = OrganizationMembership.objects.create(
            user=user,
            organization=self.organization,
        )
        location = Locations.objects.create(
            organization=self.organization,
            loc_name='Demo Commons',
            loc_short='DC',
            availible=True,
        )
        schedule = TheSched.objects.create(
            organization=self.organization,
            sched_name=DEMO_SCENARIO['schedule']['name'],
            sched_data={'manual_moves': [{'keep': True}]},
        )
        counts_before = self._record_counts()
        organization_updated_at = self.organization.updated_at
        membership_updated_at = membership.updated_at
        schedule_data = deepcopy(schedule.sched_data)
        schedule_school_ids = list(schedule.schools.values_list('pk', flat=True))

        first = inspect_demo_environment(DEMO_IDENTIFIER)
        second = inspect_demo_environment(DEMO_IDENTIFIER)

        schedule.refresh_from_db()
        self.organization.refresh_from_db()
        membership.refresh_from_db()
        location.refresh_from_db()
        self.assertEqual(self._record_counts(), counts_before)
        self.assertEqual(self.organization.updated_at, organization_updated_at)
        self.assertEqual(membership.updated_at, membership_updated_at)
        self.assertEqual(schedule.sched_data, schedule_data)
        self.assertEqual(
            list(schedule.schools.values_list('pk', flat=True)),
            schedule_school_ids,
        )
        self.assertEqual(first, second)

    def _record_counts(self):
        models = (
            Organization,
            OrganizationMembership,
            Locations,
            Course,
            Certification,
            ActivityCertificationRequirement,
            Schools,
            Instructor,
            InstructorCertification,
            TheSched,
            InstructorScheduleParticipation,
            InstructorScheduleAvailability,
        )
        return {
            model: (
                model.schools_list.count()
                if model is Schools
                else model.objects.count()
            )
            for model in models
        }


class DemoScaffoldingSafeguardTests(TestCase):
    @override_settings(**ENABLED_SETTINGS)
    def test_explicit_canonical_purpose_is_accepted_without_mutation(self):
        organization = Organization.objects.create(
            name=DEMO_IDENTIFIER,
            purpose=Organization.Purpose.CANONICAL_DEMO,
        )

        inspect_demo_environment(DEMO_IDENTIFIER)

        organization.refresh_from_db()
        self.assertEqual(
            organization.purpose,
            Organization.Purpose.CANONICAL_DEMO,
        )

    @override_settings(**ENABLED_SETTINGS)
    def test_customer_target_is_rejected_without_silent_reclassification(self):
        organization = Organization.objects.create(name=DEMO_IDENTIFIER)

        with self.assertRaisesMessage(
            DemoSafetyError,
            'not classified as canonical_demo',
        ):
            inspect_demo_environment(DEMO_IDENTIFIER)

        organization.refresh_from_db()
        self.assertEqual(organization.purpose, Organization.Purpose.CUSTOMER)

    @override_settings(**ENABLED_SETTINGS)
    def test_apply_and_reset_reject_customer_target(self):
        organization = Organization.objects.create(name=DEMO_IDENTIFIER)

        with self.assertRaisesMessage(
            CommandError,
            'not classified as canonical_demo',
        ):
            call_command(
                'inspect_demo_environment',
                organization=DEMO_IDENTIFIER,
                apply=True,
                confirm=True,
            )
        with self.assertRaisesMessage(
            CommandError,
            'not classified as canonical_demo',
        ):
            call_command(
                'reset_demo_environment',
                organization=DEMO_IDENTIFIER,
                confirm=True,
            )

        organization.refresh_from_db()
        self.assertEqual(organization.purpose, Organization.Purpose.CUSTOMER)

    @override_settings(**ENABLED_SETTINGS)
    def test_temporary_demo_organization_is_refused(self):
        organization = Organization.objects.create(
            name=DEMO_IDENTIFIER,
            purpose=Organization.Purpose.TEMPORARY_DEMO,
        )

        with self.assertRaisesMessage(
            DemoSafetyError,
            'temporary demo organization',
        ):
            inspect_demo_environment(DEMO_IDENTIFIER)

        for command, options in (
            (
                'inspect_demo_environment',
                {'apply': True, 'confirm': True},
            ),
            ('reset_demo_environment', {'confirm': True}),
        ):
            with self.assertRaisesMessage(
                CommandError,
                'temporary demo organization',
            ):
                call_command(
                    command,
                    organization=DEMO_IDENTIFIER,
                    **options,
                )

        organization.refresh_from_db()
        self.assertEqual(
            organization.purpose,
            Organization.Purpose.TEMPORARY_DEMO,
        )

    @override_settings(
        DEBUG=True,
        DEMO_SCAFFOLDING_ENABLED=False,
        DEMO_ORGANIZATION_IDENTIFIER=DEMO_IDENTIFIER,
    )
    def test_disabled_setting_fails_even_when_debug_is_true(self):
        create_canonical_organization()

        with self.assertRaisesMessage(CommandError, 'disabled'):
            call_command(
                'inspect_demo_environment',
                organization=DEMO_IDENTIFIER,
            )

    @override_settings(**ENABLED_SETTINGS)
    def test_identifier_mismatch_fails(self):
        create_canonical_organization()

        with self.assertRaisesMessage(CommandError, 'does not exactly match'):
            call_command(
                'inspect_demo_environment',
                organization='Different Organization',
            )

    @override_settings(**ENABLED_SETTINGS)
    def test_missing_target_fails_without_creating_organization(self):
        before = Organization.objects.count()

        with self.assertRaisesMessage(CommandError, 'does not exist'):
            call_command(
                'inspect_demo_environment',
                organization=DEMO_IDENTIFIER,
            )

        self.assertEqual(Organization.objects.count(), before)

    @override_settings(
        DEMO_SCAFFOLDING_ENABLED=True,
        DEMO_ORGANIZATION_IDENTIFIER=DEFAULT_ORGANIZATION_NAME,
    )
    def test_default_organization_is_refused_without_resolution_side_effect(self):
        before = Organization.objects.count()

        with self.assertRaisesMessage(CommandError, 'can never be targeted'):
            call_command(
                'inspect_demo_environment',
                organization=DEFAULT_ORGANIZATION_NAME,
            )

        self.assertEqual(Organization.objects.count(), before)

    @override_settings(
        DEMO_SCAFFOLDING_ENABLED=True,
        DEMO_ORGANIZATION_IDENTIFIER='',
    )
    def test_blank_allowed_identifier_is_refused(self):
        with self.assertRaises(DemoSafetyError) as raised:
            inspect_demo_environment('')

        self.assertTrue(raised.exception.result.blockers)


@override_settings(**ENABLED_SETTINGS)
class DemoReferenceDataApplyTests(TestCase):
    def setUp(self):
        self.organization = create_canonical_organization()
        self.user = get_user_model().objects.create_user(
            username='approved-demo-operator',
        )
        OrganizationMembership.objects.create(
            user=self.user,
            organization=self.organization,
        )

    def apply_command(self, **options):
        values = {
            'organization': DEMO_IDENTIFIER,
            'apply': True,
            'confirm': True,
            'stdout': StringIO(),
        }
        values.update(options)
        call_command('inspect_demo_environment', **values)
        return values['stdout'].getvalue()

    def test_inspection_remains_read_only_by_default(self):
        before = self.reference_counts()

        call_command(
            'inspect_demo_environment',
            organization=DEMO_IDENTIFIER,
            stdout=StringIO(),
        )

        self.assertEqual(self.reference_counts(), before)

    def test_apply_requires_confirmation_and_confirm_requires_apply(self):
        before = self.reference_counts()

        with self.assertRaisesMessage(CommandError, 'requires --confirm'):
            call_command(
                'inspect_demo_environment',
                organization=DEMO_IDENTIFIER,
                apply=True,
                stdout=StringIO(),
            )
        with self.assertRaisesMessage(CommandError, 'only valid with --apply'):
            call_command(
                'inspect_demo_environment',
                organization=DEMO_IDENTIFIER,
                confirm=True,
                stdout=StringIO(),
            )

        self.assertEqual(self.reference_counts(), before)

    @override_settings(
        DEMO_SCAFFOLDING_ENABLED=False,
        DEMO_ORGANIZATION_IDENTIFIER=DEMO_IDENTIFIER,
    )
    def test_disabled_scaffolding_blocks_apply(self):
        before = self.reference_counts()

        with self.assertRaisesMessage(CommandError, 'disabled'):
            self.apply_command()

        self.assertEqual(self.reference_counts(), before)

    def test_apply_creates_complete_stable_reference_foundation_only(self):
        output = self.apply_command()

        self.assertEqual(
            Locations.objects.filter(organization=self.organization).count(),
            4,
        )
        self.assertEqual(
            Certification.objects.filter(organization=self.organization).count(),
            2,
        )
        self.assertEqual(
            Course.objects.filter(organization=self.organization).count(),
            5,
        )
        self.assertEqual(
            ActivityCertificationRequirement.objects.filter(
                course__organization=self.organization,
            ).count(),
            1,
        )
        self.assertEqual(
            Schools.schools_list.filter(organization=self.organization).count(),
            2,
        )
        self.assertEqual(
            Instructor.objects.filter(organization=self.organization).count(),
            5,
        )
        self.assertEqual(
            InstructorCertification.objects.filter(
                instructor__organization=self.organization,
            ).count(),
            3,
        )
        schedule = TheSched.objects.get(
            organization=self.organization,
            sched_name=DEMO_SCENARIO['schedule']['name'],
        )
        self.assertEqual(
            set(schedule.schools.values_list('school_name', flat=True)),
            set(DEMO_SCENARIO['schedule']['schools']),
        )
        self.assertTrue(schedule.sched_data['generation_complete'])
        self.assertTrue(schedule.sched_data['generated_schedule'])
        self.assertEqual(schedule.sched_data['manual_moves'], [])
        self.assertEqual(schedule.sched_data['manual_instructor_overrides'], [])
        self.assertEqual(schedule.sched_data['instructor_override_revision'], 0)
        self.assertEqual(
            InstructorScheduleParticipation.objects.filter(
                organization=self.organization,
                schedule=schedule,
                state=InstructorScheduleParticipation.NOT_PARTICIPATING,
            ).count(),
            1,
        )
        self.assertEqual(
            InstructorScheduleAvailability.objects.filter(
                organization=self.organization,
                schedule=schedule,
            ).count(),
            1,
        )
        self.assertIn('Stable demo reference data applied atomically.', output)

    def test_second_apply_is_idempotent_and_inspection_is_unchanged(self):
        first = apply_demo_reference_data(DEMO_IDENTIFIER)
        counts_after_first = self.reference_counts()
        timestamps_after_first = self.reference_timestamps()

        second = apply_demo_reference_data(DEMO_IDENTIFIER)
        inspection = inspect_demo_environment(DEMO_IDENTIFIER)

        self.assertEqual(self.reference_counts(), counts_after_first)
        self.assertEqual(self.reference_timestamps(), timestamps_after_first)
        self.assertFalse(second.created)
        self.assertFalse(second.updated)
        self.assertFalse(second.reconciled)
        stable_types = {
            'location',
            'certification',
            'activity',
            'activity locations',
            'activity certification requirement',
            'school/cohort',
            'school activity selections',
            'instructor',
            'instructor certifications',
            'schedule',
            'schedule schools',
        }
        remaining_stable_actions = [
            item
            for category in (
                inspection.create,
                inspection.update,
                inspection.reconcile,
            )
            for item in category
            if item.record_type in stable_types
        ]
        self.assertEqual(remaining_stable_actions, [])
        self.assertTrue(first.created)

    def test_apply_reconciles_drift_and_canonical_relationships(self):
        apply_demo_reference_data(DEMO_IDENTIFIER)
        location = Locations.objects.get(
            organization=self.organization,
            loc_name='Demo Commons',
        )
        location.loc_short = 'OLD'
        location.availible = False
        location.save()
        activity = Course.objects.get(
            organization=self.organization,
            course_name='Demo Navigation',
        )
        activity.course_len = 2
        activity.save(update_fields=['course_len'])
        activity.primary_locs.clear()
        cohort = Schools.schools_list.get(
            organization=self.organization,
            school_name='Demo Cohort North',
        )
        cohort.ag_num = 9
        cohort.save(update_fields=['ag_num'])
        cohort.subject.clear()

        result = apply_demo_reference_data(DEMO_IDENTIFIER)

        location.refresh_from_db()
        activity.refresh_from_db()
        cohort.refresh_from_db()
        self.assertEqual(location.loc_short, 'DC')
        self.assertTrue(location.availible)
        self.assertEqual(activity.course_len, 1)
        self.assertEqual(
            set(activity.primary_locs.values_list('loc_name', flat=True)),
            {'Demo Field'},
        )
        self.assertEqual(cohort.ag_num, 2)
        self.assertEqual(
            cohort.subject.count(),
            len(DEMO_SCENARIO['schools'][0]['activities']),
        )
        self.assertTrue(result.updated)
        self.assertTrue(result.reconciled)

    def test_extra_records_are_preserved_in_demo_and_foreign_organizations(self):
        foreign = Organization.objects.create(name='Foreign Reference Organization')
        demo_extra = Locations.objects.create(
            organization=self.organization,
            loc_name='Unrelated Demo Location',
            loc_short='UDL',
        )
        foreign_location = Locations.objects.create(
            organization=foreign,
            loc_name='Demo Commons',
            loc_short='FORE',
        )

        apply_demo_reference_data(DEMO_IDENTIFIER)

        demo_extra.refresh_from_db()
        foreign_location.refresh_from_db()
        self.assertEqual(demo_extra.loc_short, 'UDL')
        self.assertEqual(foreign_location.loc_short, 'FORE')
        self.assertEqual(
            Locations.objects.filter(
                organization=foreign,
                loc_name='Demo Commons',
            ).count(),
            1,
        )

    def test_foreign_relationships_are_never_attached(self):
        foreign = Organization.objects.create(name='Foreign Relationship Organization')
        foreign_location = Locations.objects.create(
            organization=foreign,
            loc_name='Demo Field',
            loc_short='FF',
        )

        apply_demo_reference_data(DEMO_IDENTIFIER)

        activity = Course.objects.get(
            organization=self.organization,
            course_name='Demo Navigation',
        )
        self.assertNotIn(
            foreign_location,
            activity.primary_locs.all(),
        )
        self.assertTrue(
            activity.primary_locs.filter(organization=self.organization).exists()
        )

    def test_missing_membership_blocks_apply_without_creating_account(self):
        OrganizationMembership.objects.filter(
            organization=self.organization,
        ).delete()
        user_count = get_user_model().objects.count()
        before = self.reference_counts()

        with self.assertRaisesMessage(DemoSafetyError, 'existing organization membership'):
            apply_demo_reference_data(DEMO_IDENTIFIER)

        self.assertEqual(get_user_model().objects.count(), user_count)
        self.assertEqual(self.reference_counts(), before)
        self.assertFalse(
            OrganizationMembership.objects.filter(
                organization=self.organization,
            ).exists()
        )

    def test_injected_failure_rolls_back_all_reference_changes(self):
        foreign = Organization.objects.create(name='Rollback Foreign Organization')
        foreign_location = Locations.objects.create(
            organization=foreign,
            loc_name='Foreign Stable Location',
            loc_short='FSL',
        )
        drifted_demo_location = Locations.objects.create(
            organization=self.organization,
            loc_name='Demo Commons',
            loc_short='OLD',
            availible=False,
        )
        before = self.reference_counts()
        demo_updated_at = self.organization.updated_at

        with patch(
            'scheduler_app.demo_scaffolding._apply_instructors',
            side_effect=RuntimeError('injected failure'),
        ):
            with self.assertRaisesMessage(RuntimeError, 'injected failure'):
                apply_demo_reference_data(DEMO_IDENTIFIER)

        self.organization.refresh_from_db()
        foreign_location.refresh_from_db()
        drifted_demo_location.refresh_from_db()
        self.assertEqual(self.reference_counts(), before)
        self.assertEqual(self.organization.updated_at, demo_updated_at)
        self.assertEqual(foreign_location.loc_short, 'FSL')
        self.assertEqual(drifted_demo_location.loc_short, 'OLD')
        self.assertFalse(drifted_demo_location.availible)

    def reference_counts(self):
        return {
            'organizations': Organization.objects.count(),
            'memberships': OrganizationMembership.objects.count(),
            'locations': Locations.objects.count(),
            'certifications': Certification.objects.count(),
            'activities': Course.objects.count(),
            'requirements': ActivityCertificationRequirement.objects.count(),
            'schools': Schools.schools_list.count(),
            'instructors': Instructor.objects.count(),
            'instructor_certifications': InstructorCertification.objects.count(),
            'schedules': TheSched.objects.count(),
            'participation': InstructorScheduleParticipation.objects.count(),
            'availability': InstructorScheduleAvailability.objects.count(),
        }

    def reference_timestamps(self):
        return {
            'organization': Organization.objects.get(
                pk=self.organization.pk,
            ).updated_at,
            'membership': OrganizationMembership.objects.get(
                organization=self.organization,
            ).updated_at,
        }


@override_settings(**ENABLED_SETTINGS)
class DemoOperationalStartingStateTests(TestCase):
    def setUp(self):
        self.organization = create_canonical_organization()
        user = get_user_model().objects.create_user(username='operational-demo-operator')
        OrganizationMembership.objects.create(
            user=user,
            organization=self.organization,
        )

    def apply(self):
        return apply_demo_reference_data(DEMO_IDENTIFIER)

    def schedule(self):
        return TheSched.objects.get(
            organization=self.organization,
            sched_name=DEMO_SCENARIO['schedule']['name'],
        )

    def test_participation_and_availability_are_canonical_and_isolated(self):
        foreign = Organization.objects.create(name='Foreign Operational Organization')
        foreign_schedule = TheSched.objects.create(
            organization=foreign,
            sched_name='Foreign Schedule',
            sched_data={'version': 1},
        )
        foreign_instructor = Instructor.objects.create(
            organization=foreign,
            fname='Casey',
            lname='Demo',
        )
        foreign_availability = InstructorScheduleAvailability.objects.create(
            organization=foreign,
            schedule=foreign_schedule,
            instructor=foreign_instructor,
            slot_key='tue_am1',
            state=InstructorScheduleAvailability.AVAILABLE,
        )

        self.apply()

        schedule = self.schedule()
        opt_out = DEMO_SCENARIO['participation_opt_out']
        opted_out = Instructor.objects.get(
            organization=self.organization,
            fname=opt_out['first'],
            lname=opt_out['last'],
        )
        self.assertTrue(InstructorScheduleParticipation.objects.filter(
            organization=self.organization,
            schedule=schedule,
            instructor=opted_out,
            state=InstructorScheduleParticipation.NOT_PARTICIPATING,
        ).exists())
        participating_names = {
            (item['first'], item['last'])
            for item in DEMO_SCENARIO['participating_instructors']
        }
        self.assertFalse(InstructorScheduleParticipation.objects.filter(
            organization=self.organization,
            schedule=schedule,
            instructor__fname__in={name[0] for name in participating_names},
        ).exclude(instructor=opted_out).exists())

        exception = DEMO_SCENARIO['availability_exception']
        availability = InstructorScheduleAvailability.objects.get(
            organization=self.organization,
            schedule=schedule,
        )
        self.assertEqual(
            (availability.instructor.fname, availability.instructor.lname),
            (exception['first'], exception['last']),
        )
        self.assertEqual(availability.slot_key, exception['slot_key'])
        self.assertEqual(availability.state, exception['state'])
        foreign_availability.refresh_from_db()
        self.assertEqual(
            foreign_availability.state,
            InstructorScheduleAvailability.AVAILABLE,
        )

    def test_generation_uses_normal_lifecycle_and_accepts_complete_output(self):
        original = TheSched.generate_and_store_schedule
        with patch.object(
            TheSched,
            'generate_and_store_schedule',
            autospec=True,
            side_effect=lambda schedule: original(schedule),
        ) as generation:
            self.apply()

        generation.assert_called_once()
        schedule = self.schedule()
        data = schedule.sched_data
        generated = data['generated_schedule']
        expected_groups = {
            f"{school['name']} {index}"
            for school in DEMO_SCENARIO['schools']
            for index in range(school['groups'])
        }
        self.assertEqual(set(generated['ags']), expected_groups)
        self.assertTrue(all(
            slot in generated
            for slot in DEMO_SCENARIO['required_schedule_slots']
        ))
        display = schedule.get_display_schedule_result()
        self.assertTrue(display['schedule_rows'])
        self.assertEqual(
            display['override_replay_result']['replay_conflicts'],
            [],
        )
        self.assertEqual(
            display['override_replay_result']['holding_area'],
            [],
        )
        self.assertEqual(data['manual_moves'], [])
        self.assertEqual(data['manual_instructor_overrides'], [])
        self.assertEqual(data['instructor_override_revision'], 0)

    def test_automatic_assignment_enforces_demo_constraints_and_alternate(self):
        self.apply()
        schedule = self.schedule()

        assignment = run_instructor_assignment(schedule)

        opt_out = DEMO_SCENARIO['participation_opt_out']
        opted_out = Instructor.objects.get(
            organization=self.organization,
            fname=opt_out['first'],
            lname=opt_out['last'],
        )
        assigned = [
            item for item in assignment['assignments']
            if item['assigned_instructor'] is not None
        ]
        self.assertNotIn(
            opted_out,
            [item['assigned_instructor'] for item in assigned],
        )
        exception = DEMO_SCENARIO['availability_exception']
        unavailable = Instructor.objects.get(
            organization=self.organization,
            fname=exception['first'],
            lname=exception['last'],
        )
        self.assertFalse(any(
            item['assigned_instructor'] == unavailable
            and exception['slot_key'] in {
                slot['slot_key']
                for slot in item['occurrence']['slot_footprint']
            }
            for item in assigned
        ))
        sensitive = Course.objects.get(
            organization=self.organization,
            course_name=DEMO_SCENARIO['qualification_sensitive_activity'],
        )
        requirements = set(
            sensitive.required_certifications.values_list('pk', flat=True)
        )
        sensitive_assignments = [
            item for item in assigned
            if item['occurrence']['activity_id'] == sensitive.pk
        ]
        self.assertTrue(sensitive_assignments)
        self.assertTrue(all(
            requirements <= set(
                item['assigned_instructor'].certifications.values_list(
                    'pk',
                    flat=True,
                )
            )
            for item in sensitive_assignments
        ))
        self.assertTrue(any(
            item.record_type == 'canonical starting state'
            for item in inspect_demo_environment(DEMO_IDENTIFIER).unchanged
        ))

    def test_valid_second_apply_does_not_regenerate_or_write_staffing(self):
        self.apply()
        schedule = self.schedule()
        data_before = deepcopy(schedule.sched_data)
        participation = InstructorScheduleParticipation.objects.get(
            organization=self.organization,
            schedule=schedule,
        )
        availability = InstructorScheduleAvailability.objects.get(
            organization=self.organization,
            schedule=schedule,
        )
        staffing_ids = (participation.pk, availability.pk)

        with patch.object(
            TheSched,
            'generate_and_store_schedule',
            autospec=True,
        ) as generation:
            result = self.apply()

        schedule.refresh_from_db()
        self.assertFalse(generation.called)
        self.assertEqual(schedule.sched_data, data_before)
        self.assertEqual(
            (
                InstructorScheduleParticipation.objects.get(
                    organization=self.organization,
                    schedule=schedule,
                ).pk,
                InstructorScheduleAvailability.objects.get(
                    organization=self.organization,
                    schedule=schedule,
                ).pk,
            ),
            staffing_ids,
        )
        self.assertFalse(result.created)
        self.assertFalse(result.updated)
        self.assertFalse(result.reconciled)

    def test_confirmed_apply_restores_dirty_operational_and_staffing_state(self):
        self.apply()
        schedule = self.schedule()
        dirty = deepcopy(schedule.sched_data)
        dirty['manual_moves'] = [{'status': 'active', 'test': True}]
        dirty['manual_instructor_overrides'] = [{'action': 'set', 'test': True}]
        dirty['instructor_override_revision'] = 7
        schedule.sched_data = dirty
        schedule.save(update_fields=['sched_data'])
        participation = InstructorScheduleParticipation.objects.get(
            organization=self.organization,
            schedule=schedule,
        )
        participation.state = InstructorScheduleParticipation.PARTICIPATING
        participation.save(update_fields=['state'])
        availability = InstructorScheduleAvailability.objects.get(
            organization=self.organization,
            schedule=schedule,
        )
        availability.state = InstructorScheduleAvailability.AVAILABLE
        availability.save(update_fields=['state'])

        inspection = inspect_demo_environment(DEMO_IDENTIFIER)
        self.assertTrue(any(
            item.record_type == 'schedule operational state'
            for item in inspection.restore
        ))
        self.apply()

        schedule.refresh_from_db()
        participation.refresh_from_db()
        availability.refresh_from_db()
        self.assertEqual(schedule.sched_data['manual_moves'], [])
        self.assertEqual(schedule.sched_data['manual_instructor_overrides'], [])
        self.assertEqual(schedule.sched_data['instructor_override_revision'], 0)
        self.assertEqual(
            participation.state,
            InstructorScheduleParticipation.NOT_PARTICIPATING,
        )
        self.assertEqual(
            availability.state,
            InstructorScheduleAvailability.UNAVAILABLE,
        )

    def test_generation_failure_rolls_back_staffing_and_reference_data(self):
        counts_before = {
            'locations': Locations.objects.count(),
            'participation': InstructorScheduleParticipation.objects.count(),
            'availability': InstructorScheduleAvailability.objects.count(),
            'schedules': TheSched.objects.count(),
        }

        with patch.object(
            TheSched,
            'generate_and_store_schedule',
            autospec=True,
            side_effect=RuntimeError('generation failed'),
        ):
            with self.assertRaisesMessage(RuntimeError, 'generation failed'):
                self.apply()

        self.assertEqual(Locations.objects.count(), counts_before['locations'])
        self.assertEqual(
            InstructorScheduleParticipation.objects.count(),
            counts_before['participation'],
        )
        self.assertEqual(
            InstructorScheduleAvailability.objects.count(),
            counts_before['availability'],
        )
        self.assertEqual(TheSched.objects.count(), counts_before['schedules'])

    def test_acceptance_failure_after_generation_leaves_no_partial_state(self):
        counts_before = {
            'locations': Locations.objects.count(),
            'participation': InstructorScheduleParticipation.objects.count(),
            'availability': InstructorScheduleAvailability.objects.count(),
            'schedules': TheSched.objects.count(),
        }

        with patch(
            'scheduler_app.demo_scaffolding._validate_demo_starting_state',
            side_effect=RuntimeError('acceptance failed'),
        ):
            with self.assertRaisesMessage(RuntimeError, 'acceptance failed'):
                self.apply()

        self.assertEqual(Locations.objects.count(), counts_before['locations'])
        self.assertEqual(
            InstructorScheduleParticipation.objects.count(),
            counts_before['participation'],
        )
        self.assertEqual(
            InstructorScheduleAvailability.objects.count(),
            counts_before['availability'],
        )
        self.assertEqual(TheSched.objects.count(), counts_before['schedules'])

    def test_assignment_acceptance_failure_rolls_back_drift_reconciliation(self):
        self.apply()
        schedule = self.schedule()
        participation = InstructorScheduleParticipation.objects.get(
            organization=self.organization,
            schedule=schedule,
        )
        participation.state = InstructorScheduleParticipation.PARTICIPATING
        participation.save(update_fields=['state'])
        committed_data = deepcopy(schedule.sched_data)

        with patch(
            'scheduler_app.demo_scaffolding.run_instructor_assignment',
            side_effect=RuntimeError('assignment failed'),
        ):
            with self.assertRaisesMessage(
                DemoSafetyError,
                'automatic instructor assignment failed',
            ):
                self.apply()

        participation.refresh_from_db()
        schedule.refresh_from_db()
        self.assertEqual(
            participation.state,
            InstructorScheduleParticipation.PARTICIPATING,
        )
        self.assertEqual(schedule.sched_data, committed_data)


class DemoResetSafeguardTests(TestCase):
    @override_settings(**ENABLED_SETTINGS)
    def test_reset_command_requires_confirmation_without_writes(self):
        organization = create_canonical_organization()
        before = Organization.objects.count()

        with self.assertRaisesMessage(CommandError, '--confirm is required'):
            call_command(
                'reset_demo_environment',
                organization=DEMO_IDENTIFIER,
                stdout=StringIO(),
            )

        self.assertEqual(Organization.objects.count(), before)
        self.assertFalse(
            TheSched.objects.filter(organization=organization).exists()
        )

    @override_settings(
        DEBUG=True,
        DEMO_SCAFFOLDING_ENABLED=False,
        DEMO_ORGANIZATION_IDENTIFIER=DEMO_IDENTIFIER,
    )
    def test_disabled_scaffolding_blocks_confirmed_reset(self):
        create_canonical_organization()

        with self.assertRaisesMessage(CommandError, 'disabled'):
            call_command(
                'reset_demo_environment',
                organization=DEMO_IDENTIFIER,
                confirm=True,
                stdout=StringIO(),
            )

    @override_settings(**ENABLED_SETTINGS)
    def test_identifier_mismatch_and_missing_organization_block_reset(self):
        before = Organization.objects.count()

        with self.assertRaisesMessage(CommandError, 'does not exactly match'):
            call_command(
                'reset_demo_environment',
                organization='Different Organization',
                confirm=True,
                stdout=StringIO(),
            )
        with self.assertRaisesMessage(CommandError, 'does not exist'):
            call_command(
                'reset_demo_environment',
                organization=DEMO_IDENTIFIER,
                confirm=True,
                stdout=StringIO(),
            )

        self.assertEqual(Organization.objects.count(), before)

    @override_settings(
        DEMO_SCAFFOLDING_ENABLED=True,
        DEMO_ORGANIZATION_IDENTIFIER=DEFAULT_ORGANIZATION_NAME,
    )
    def test_default_organization_is_refused_for_reset(self):
        before = Organization.objects.count()

        with self.assertRaisesMessage(CommandError, 'can never be targeted'):
            call_command(
                'reset_demo_environment',
                organization=DEFAULT_ORGANIZATION_NAME,
                confirm=True,
                stdout=StringIO(),
            )

        self.assertEqual(Organization.objects.count(), before)

    @override_settings(**ENABLED_SETTINGS)
    def test_missing_membership_and_missing_schedule_are_distinct_blockers(self):
        organization = create_canonical_organization()
        TheSched.objects.create(
            organization=organization,
            sched_name=DEMO_SCENARIO['schedule']['name'],
            sched_data={'version': 1},
        )
        with self.assertRaisesMessage(
            DemoSafetyError,
            'existing organization membership',
        ):
            reset_demo_environment(DEMO_IDENTIFIER)

        user = get_user_model().objects.create_user(username='reset-operator')
        OrganizationMembership.objects.create(
            organization=organization,
            user=user,
        )
        TheSched.objects.filter(organization=organization).delete()
        before = TheSched.objects.count()

        with self.assertRaisesMessage(
            DemoSafetyError,
            'existing canonical demo schedule',
        ):
            reset_demo_environment(DEMO_IDENTIFIER)

        self.assertEqual(TheSched.objects.count(), before)


@override_settings(**ENABLED_SETTINGS)
class DemoCanonicalResetTests(TestCase):
    def setUp(self):
        self.organization = create_canonical_organization()
        user = get_user_model().objects.create_user(username='canonical-reset-operator')
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=user,
        )
        apply_demo_reference_data(DEMO_IDENTIFIER)

    def schedule(self):
        return TheSched.objects.get(
            organization=self.organization,
            sched_name=DEMO_SCENARIO['schedule']['name'],
        )

    def test_reset_command_restores_complete_mutated_canonical_state(self):
        schedule = self.schedule()
        location = Locations.objects.get(
            organization=self.organization,
            loc_name='Demo Commons',
        )
        location.loc_short = 'OLD'
        location.save(update_fields=['loc_short'])
        activity = Course.objects.get(
            organization=self.organization,
            course_name='Demo Navigation',
        )
        activity.primary_locs.clear()
        cohort = Schools.schools_list.get(
            organization=self.organization,
            school_name='Demo Cohort North',
        )
        cohort.ag_num = 7
        cohort.save(update_fields=['ag_num'])
        cohort.subject.clear()
        alternate = Instructor.objects.get(
            organization=self.organization,
            fname='Blair',
            lname='Demo',
        )
        alternate.certifications.clear()
        participation = InstructorScheduleParticipation.objects.get(
            organization=self.organization,
            schedule=schedule,
        )
        participation.state = InstructorScheduleParticipation.PARTICIPATING
        participation.save(update_fields=['state'])
        availability = InstructorScheduleAvailability.objects.get(
            organization=self.organization,
            schedule=schedule,
        )
        availability.state = InstructorScheduleAvailability.AVAILABLE
        availability.save(update_fields=['state'])
        dirty = deepcopy(schedule.sched_data)
        dirty['manual_moves'] = [{'status': 'active'}]
        dirty['manual_instructor_overrides'] = [
            {'action': 'set'},
            {'action': 'reset_all'},
            {'action': 'set', 'status': 'stale'},
        ]
        dirty['instructor_override_revision'] = 9
        schedule.sched_data = dirty
        schedule.save(update_fields=['sched_data'])
        output = StringIO()

        call_command(
            'reset_demo_environment',
            organization=DEMO_IDENTIFIER,
            confirm=True,
            stdout=output,
        )

        schedule.refresh_from_db()
        location.refresh_from_db()
        cohort.refresh_from_db()
        participation.refresh_from_db()
        availability.refresh_from_db()
        alternate.refresh_from_db()
        self.assertEqual(location.loc_short, 'DC')
        self.assertEqual(cohort.ag_num, 2)
        self.assertEqual(
            set(cohort.subject.values_list('course_name', flat=True)),
            set(DEMO_SCENARIO['schools'][0]['activities']),
        )
        self.assertEqual(
            set(alternate.certifications.values_list('name', flat=True)),
            {'Demo Technical Skills'},
        )
        self.assertEqual(
            participation.state,
            InstructorScheduleParticipation.NOT_PARTICIPATING,
        )
        self.assertEqual(
            availability.state,
            InstructorScheduleAvailability.UNAVAILABLE,
        )
        self.assertEqual(schedule.sched_data['manual_moves'], [])
        self.assertEqual(schedule.sched_data['manual_instructor_overrides'], [])
        self.assertEqual(schedule.sched_data['instructor_override_revision'], 0)
        display = schedule.get_display_schedule_result()
        self.assertEqual(display['override_replay_result']['replay_conflicts'], [])
        self.assertEqual(display['override_replay_result']['holding_area'], [])
        self.assertTrue(run_instructor_assignment(schedule)['coverage']['complete'])
        self.assertIn(
            'Demo environment restored to the canonical starting state.',
            output.getvalue(),
        )

    def test_reset_preserves_foreign_unrelated_and_other_schedule_state(self):
        canonical = self.schedule()
        foreign = Organization.objects.create(name='Foreign Reset Organization')
        foreign_location = Locations.objects.create(
            organization=foreign,
            loc_name='Demo Commons',
            loc_short='FORE',
        )
        unrelated_location = Locations.objects.create(
            organization=self.organization,
            loc_name='Unrelated Reset Location',
            loc_short='URL',
        )
        unrelated_instructor = Instructor.objects.create(
            organization=self.organization,
            fname='Unrelated',
            lname='Instructor',
        )
        other_schedule = TheSched.objects.create(
            organization=self.organization,
            sched_name='Unrelated Schedule',
            sched_data={'version': 1, 'manual_moves': [{'keep': True}]},
        )
        unrelated_participation = InstructorScheduleParticipation.objects.create(
            organization=self.organization,
            schedule=other_schedule,
            instructor=unrelated_instructor,
            state=InstructorScheduleParticipation.NOT_PARTICIPATING,
        )
        unrelated_availability = InstructorScheduleAvailability.objects.create(
            organization=self.organization,
            schedule=other_schedule,
            instructor=unrelated_instructor,
            slot_key='wed_pm1',
            state=InstructorScheduleAvailability.UNAVAILABLE,
        )
        dirty = deepcopy(canonical.sched_data)
        dirty['manual_moves'] = [{'status': 'active'}]
        canonical.sched_data = dirty
        canonical.save(update_fields=['sched_data'])

        reset_demo_environment(DEMO_IDENTIFIER)

        foreign_location.refresh_from_db()
        unrelated_location.refresh_from_db()
        other_schedule.refresh_from_db()
        unrelated_participation.refresh_from_db()
        unrelated_availability.refresh_from_db()
        self.assertEqual(foreign_location.loc_short, 'FORE')
        self.assertEqual(unrelated_location.loc_short, 'URL')
        self.assertEqual(
            other_schedule.sched_data,
            {'version': 1, 'manual_moves': [{'keep': True}]},
        )
        self.assertEqual(
            unrelated_participation.state,
            InstructorScheduleParticipation.NOT_PARTICIPATING,
        )
        self.assertEqual(
            unrelated_availability.state,
            InstructorScheduleAvailability.UNAVAILABLE,
        )

    def test_no_op_reset_preserves_state_and_skips_generation(self):
        schedule = self.schedule()
        data_before = deepcopy(schedule.sched_data)
        location = Locations.objects.get(
            organization=self.organization,
            loc_name='Demo Commons',
        )
        participation = InstructorScheduleParticipation.objects.get(
            organization=self.organization,
            schedule=schedule,
        )
        availability = InstructorScheduleAvailability.objects.get(
            organization=self.organization,
            schedule=schedule,
        )
        identities = (participation.pk, availability.pk)
        organization_updated_at = self.organization.updated_at
        membership = OrganizationMembership.objects.get(
            organization=self.organization,
        )
        membership_updated_at = membership.updated_at
        output = StringIO()

        with patch.object(
            TheSched,
            'generate_and_store_schedule',
            autospec=True,
        ) as generation:
            result = reset_demo_environment(DEMO_IDENTIFIER)
        call_command(
            'reset_demo_environment',
            organization=DEMO_IDENTIFIER,
            confirm=True,
            stdout=output,
        )

        schedule.refresh_from_db()
        location.refresh_from_db()
        self.organization.refresh_from_db()
        membership.refresh_from_db()
        self.assertFalse(generation.called)
        self.assertTrue(result.already_canonical)
        self.assertEqual(schedule.sched_data, data_before)
        self.assertEqual(
            self.organization.updated_at,
            organization_updated_at,
        )
        self.assertEqual(membership.updated_at, membership_updated_at)
        self.assertEqual(
            (
                InstructorScheduleParticipation.objects.get(
                    organization=self.organization,
                    schedule=schedule,
                ).pk,
                InstructorScheduleAvailability.objects.get(
                    organization=self.organization,
                    schedule=schedule,
                ).pk,
            ),
            identities,
        )
        self.assertIn(
            'Demo environment already matches the canonical starting state.',
            output.getvalue(),
        )

    def test_reset_failure_after_generation_rolls_back_every_change(self):
        schedule = self.schedule()
        location = Locations.objects.get(
            organization=self.organization,
            loc_name='Demo Commons',
        )
        location.loc_short = 'DRFT'
        location.save(update_fields=['loc_short'])
        dirty = deepcopy(schedule.sched_data)
        dirty['manual_moves'] = [{'status': 'active', 'keep': True}]
        schedule.sched_data = dirty
        schedule.save(update_fields=['sched_data'])
        committed_data = deepcopy(dirty)

        with patch(
            'scheduler_app.demo_scaffolding._validate_demo_starting_state',
            side_effect=RuntimeError('post-generation failure'),
        ):
            with self.assertRaisesMessage(RuntimeError, 'post-generation failure'):
                reset_demo_environment(DEMO_IDENTIFIER)

        schedule.refresh_from_db()
        location.refresh_from_db()
        self.assertEqual(location.loc_short, 'DRFT')
        self.assertEqual(schedule.sched_data, committed_data)

    def test_reset_generation_failure_rolls_back_stable_reconciliation(self):
        schedule = self.schedule()
        location = Locations.objects.get(
            organization=self.organization,
            loc_name='Demo Commons',
        )
        location.loc_short = 'FAIL'
        location.save(update_fields=['loc_short'])
        dirty = deepcopy(schedule.sched_data)
        dirty['manual_moves'] = [{'status': 'active'}]
        schedule.sched_data = dirty
        schedule.save(update_fields=['sched_data'])

        with patch.object(
            TheSched,
            'generate_and_store_schedule',
            autospec=True,
            side_effect=RuntimeError('reset generation failed'),
        ):
            with self.assertRaisesMessage(
                RuntimeError,
                'reset generation failed',
            ):
                reset_demo_environment(DEMO_IDENTIFIER)

        schedule.refresh_from_db()
        location.refresh_from_db()
        self.assertEqual(location.loc_short, 'FAIL')
        self.assertEqual(schedule.sched_data, dirty)

    def test_reset_assignment_failure_rolls_back_staffing_reconciliation(self):
        schedule = self.schedule()
        participation = InstructorScheduleParticipation.objects.get(
            organization=self.organization,
            schedule=schedule,
        )
        participation.state = InstructorScheduleParticipation.PARTICIPATING
        participation.save(update_fields=['state'])
        availability = InstructorScheduleAvailability.objects.get(
            organization=self.organization,
            schedule=schedule,
        )
        availability.state = InstructorScheduleAvailability.AVAILABLE
        availability.save(update_fields=['state'])

        with patch(
            'scheduler_app.demo_scaffolding.run_instructor_assignment',
            side_effect=RuntimeError('reset assignment failed'),
        ):
            with self.assertRaisesMessage(
                DemoSafetyError,
                'automatic instructor assignment failed',
            ):
                reset_demo_environment(DEMO_IDENTIFIER)

        participation.refresh_from_db()
        availability.refresh_from_db()
        self.assertEqual(
            participation.state,
            InstructorScheduleParticipation.PARTICIPATING,
        )
        self.assertEqual(
            availability.state,
            InstructorScheduleAvailability.AVAILABLE,
        )
