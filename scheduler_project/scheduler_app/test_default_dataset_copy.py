from copy import deepcopy
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from members.models import (
    DEFAULT_ORGANIZATION_NAME,
    DemoSession,
    Organization,
    OrganizationMembership,
)

from .default_dataset_copy import (
    DefaultDatasetCopyError,
    _organization_graph,
    copy_default_dataset_to_organization,
    plan_default_dataset_copy,
)
from .models import (
    ActivityCertificationRequirement,
    Certification,
    Course,
    Instructor,
    InstructorCertification,
    InstructorLeadershipRole,
    InstructorScheduleAvailability,
    InstructorScheduleParticipation,
    LeadershipRole,
    Locations,
    Schools,
    TheSched,
)


TARGET_NAME = 'Realistic Demo Source'


class DefaultDatasetCopyFixtureMixin:
    def setUp(self):
        super().setUp()
        self.source, _created = Organization.objects.get_or_create(
            name=DEFAULT_ORGANIZATION_NAME,
            defaults={'purpose': Organization.Purpose.CUSTOMER},
        )
        self.location = Locations.objects.create(
            organization=self.source,
            loc_name='Working Field',
            loc_short='WF',
            description='Fictional operational notes',
            availible=True,
        )
        self.certification = Certification.objects.create(
            organization=self.source,
            name='Working Safety',
        )
        self.role = LeadershipRole.objects.create(
            organization=self.source,
            name='Working Lead',
        )
        self.course = Course.objects.create(
            organization=self.source,
            course_name='Working Activity',
            abriviation='WACT',
            course_len=1,
            required_instructor_count=1,
        )
        self.course.primary_locs.add(self.location)
        ActivityCertificationRequirement.objects.create(
            course=self.course,
            certification=self.certification,
        )
        self.school = Schools._default_manager.create(
            organization=self.source,
            school_name='Working Cohort',
            arrive='Mon',
            depart='Fri',
            total_students=16,
            ag_num=1,
            attending_year=date(2030, 1, 1),
        )
        self.school.subject.add(self.course)
        self.school.update_sorted_subject_lst()
        self.school.save(update_fields=('sorted_subject_lst',))
        self.schedule = TheSched.objects.create(
            organization=self.source,
            sched_name='Working Infeasible Schedule',
            sched_data={
                'version': 1,
                'generated_schedule': {'ags': ['source-only']},
                'generation_complete': False,
                'generation_diagnostics': [{'reason': 'Fictional constraint'}],
                'generation_runtime_diagnostics': [],
                'manual_moves': [{'source_occurrence_id': 99}],
                'manual_instructor_overrides': [{'source_instructor_id': 88}],
            },
        )
        self.schedule.schools.add(self.school)
        self.instructor = Instructor.objects.create(
            organization=self.source,
            fname='Casey',
            lname='Fictional',
            ropes_lead=True,
            school_lead=False,
            cpr=True,
            firstaid='yes',
        )
        InstructorCertification.objects.create(
            instructor=self.instructor,
            certification=self.certification,
        )
        InstructorLeadershipRole.objects.create(
            instructor=self.instructor,
            leadership_role=self.role,
        )
        InstructorScheduleParticipation.objects.create(
            organization=self.source,
            instructor=self.instructor,
            schedule=self.schedule,
            state=InstructorScheduleParticipation.PARTICIPATING,
        )
        InstructorScheduleAvailability.objects.create(
            organization=self.source,
            instructor=self.instructor,
            schedule=self.schedule,
            slot_key='mon_pm1',
            state=InstructorScheduleAvailability.UNAVAILABLE,
        )


class DefaultDatasetCopyPlanTests(
    DefaultDatasetCopyFixtureMixin,
    TestCase,
):
    def test_command_defaults_to_read_only_plan_with_counts_and_exclusions(self):
        before = deepcopy(_organization_graph(self.source))
        output = StringIO()

        call_command(
            'copy_default_dataset_to_organization',
            target_organization=TARGET_NAME,
            stdout=output,
        )

        self.assertFalse(
            Organization.objects.filter(name=TARGET_NAME).exists()
        )
        self.assertEqual(_organization_graph(self.source), before)
        text = output.getvalue()
        for expected in (
            'locations: 1',
            'activities: 1',
            'schools: 1',
            'schedules: 1',
            'instructors: 1',
            'activity_locations: 1',
            'school_activities: 1',
            'schedule_schools: 1',
            'users',
            'memberships',
            'Dry run complete; no database changes were made.',
        ):
            self.assertIn(expected, text)

    def test_plan_identifies_infeasible_state_and_cosmetic_warning(self):
        plan = plan_default_dataset_copy(TARGET_NAME)

        self.assertEqual(
            plan.schedule_states,
            ({
                'schedule': self.schedule.sched_name,
                'source_state': 'infeasible-or-incomplete',
                'target_state': 'generated and operational state cleared',
            },),
        )
        self.assertTrue(plan.warnings)
        self.assertFalse(plan.blockers)

    def test_plan_refuses_invalid_targets_and_protected_purposes(self):
        for target in ('', '   ', DEFAULT_ORGANIZATION_NAME):
            with self.subTest(target=target):
                with self.assertRaises(DefaultDatasetCopyError):
                    plan_default_dataset_copy(target)

        for purpose in (
            Organization.Purpose.CANONICAL_DEMO,
            Organization.Purpose.TEMPORARY_DEMO,
        ):
            organization = Organization.objects.create(
                name=f'Blocked {purpose}',
                purpose=purpose,
            )
            with self.subTest(purpose=purpose):
                with self.assertRaises(DefaultDatasetCopyError):
                    plan_default_dataset_copy(organization.name)

    def test_plan_refuses_missing_or_ambiguous_source_and_target(self):
        other = Organization.objects.create(name='Other')
        with patch(
            'scheduler_app.default_dataset_copy._source_organizations',
            return_value=[],
        ):
            with self.assertRaises(DefaultDatasetCopyError):
                plan_default_dataset_copy(TARGET_NAME)
        with patch(
            'scheduler_app.default_dataset_copy._source_organizations',
            return_value=[self.source, other],
        ):
            with self.assertRaises(DefaultDatasetCopyError):
                plan_default_dataset_copy(TARGET_NAME)
        with patch(
            'scheduler_app.default_dataset_copy._target_organizations',
            return_value=[other, self.source],
        ):
            with self.assertRaises(DefaultDatasetCopyError):
                plan_default_dataset_copy(TARGET_NAME)

    def test_plan_refuses_populated_or_drifted_target(self):
        target = Organization.objects.create(name=TARGET_NAME)
        Locations.objects.create(
            organization=target,
            loc_name='Partial',
            loc_short='PART',
        )

        with self.assertRaises(DefaultDatasetCopyError) as raised:
            plan_default_dataset_copy(TARGET_NAME)

        self.assertIn(
            'operational records',
            ' '.join(raised.exception.plan.blockers),
        )

    def test_duplicate_instructor_and_foreign_relationship_block(self):
        Instructor.objects.create(
            organization=self.source,
            fname=self.instructor.fname,
            lname=self.instructor.lname,
        )
        foreign = Organization.objects.create(name='Foreign')
        foreign_location = Locations.objects.create(
            organization=foreign,
            loc_name='Foreign Location',
            loc_short='FOR',
        )
        Course.primary_locs.through.objects.create(
            course=self.course,
            locations=foreign_location,
        )

        with self.assertRaises(DefaultDatasetCopyError) as raised:
            plan_default_dataset_copy(TARGET_NAME)

        blockers = ' '.join(raised.exception.plan.blockers)
        self.assertIn('Duplicate instructor', blockers)
        self.assertIn('Foreign activity-location', blockers)

    def test_operations_runbook_documents_guarded_future_source_boundary(self):
        repository_root = Path(__file__).resolve().parents[2]
        runbook = (
            repository_root / 'docs/demo-environment-runbook.md'
        ).read_text()

        self.assertIn('## Realistic scenario source-copy boundary', runbook)
        self.assertIn(
            'copy_default_dataset_to_organization',
            runbook,
        )
        self.assertIn('Dry run is the default', runbook)
        self.assertIn('Target `sched_data` is cleared', runbook)
        self.assertIn(
            '`working-v1` artifact is the hosted provisioning',
            runbook,
        )


class DefaultDatasetCopyExecutionTests(
    DefaultDatasetCopyFixtureMixin,
    TestCase,
):
    def test_confirmed_copy_rebuilds_isolated_graph_with_new_primary_keys(self):
        source_graph = deepcopy(_organization_graph(self.source))
        user_count = get_user_model().objects.count()
        membership_count = OrganizationMembership.objects.count()
        demo_session_count = DemoSession.objects.count()

        with patch.object(
            TheSched,
            'generate_and_store_schedule',
        ) as generate:
            result = copy_default_dataset_to_organization(
                TARGET_NAME,
                confirmed=True,
            )

        target = result.target
        self.assertTrue(result.copied)
        self.assertTrue(result.created_target)
        self.assertEqual(target.purpose, Organization.Purpose.CUSTOMER)
        self.assertNotEqual(target.name, DEFAULT_ORGANIZATION_NAME)
        self.assertFalse(result.target_has_membership)
        self.assertEqual(_organization_graph(self.source), source_graph)
        self.assertEqual(
            _organization_graph(target),
            _organization_graph(
                self.source,
                normalize_schedule_state=True,
            ),
        )
        generate.assert_not_called()

        model_pairs = (
            (Locations, 'loc_name'),
            (Course, 'course_name'),
            (Schools, 'school_name'),
            (TheSched, 'sched_name'),
            (Certification, 'name'),
            (LeadershipRole, 'name'),
        )
        for model, identity in model_pairs:
            source_item = model._default_manager.filter(
                organization=self.source,
            ).get(**{identity: getattr(
                model._default_manager.filter(
                    organization=self.source,
                ).first(),
                identity,
            )})
            target_item = model._default_manager.filter(
                organization=target,
            ).get(**{identity: getattr(source_item, identity)})
            self.assertNotEqual(source_item.pk, target_item.pk)

        target_schedule = TheSched.objects.get(organization=target)
        self.assertIsNone(target_schedule.sched_data)
        self.assertEqual(
            set(target_schedule.schools.values_list('organization_id', flat=True)),
            {target.pk},
        )
        self.assertEqual(
            set(
                Course.primary_locs.through.objects.filter(
                    course__organization=target,
                ).values_list('locations__organization_id', flat=True)
            ),
            {target.pk},
        )
        self.assertEqual(get_user_model().objects.count(), user_count)
        self.assertEqual(OrganizationMembership.objects.count(), membership_count)
        self.assertEqual(DemoSession.objects.count(), demo_session_count)

    def test_existing_empty_target_is_populated_without_membership(self):
        target = Organization.objects.create(
            name=TARGET_NAME,
            purpose=Organization.Purpose.CUSTOMER,
        )

        result = copy_default_dataset_to_organization(
            TARGET_NAME,
            confirmed=True,
        )

        self.assertFalse(result.created_target)
        self.assertEqual(result.target, target)
        self.assertFalse(
            OrganizationMembership.objects.filter(organization=target).exists()
        )

    def test_exact_second_execution_is_noop_and_drift_fails_closed(self):
        first = copy_default_dataset_to_organization(
            TARGET_NAME,
            confirmed=True,
        )
        before = deepcopy(_organization_graph(first.target))

        second = copy_default_dataset_to_organization(
            TARGET_NAME,
            confirmed=True,
        )

        self.assertFalse(second.copied)
        self.assertFalse(second.created_target)
        self.assertEqual(_organization_graph(second.target), before)

        Locations.objects.filter(
            organization=first.target,
            loc_name=self.location.loc_name,
        ).update(loc_short='DRFT')
        with self.assertRaises(DefaultDatasetCopyError):
            copy_default_dataset_to_organization(
                TARGET_NAME,
                confirmed=True,
            )

    def test_injected_failure_rolls_back_target_and_preserves_source(self):
        source_graph = deepcopy(_organization_graph(self.source))
        from . import default_dataset_copy

        original = default_dataset_copy._copy_graph

        def fail_after_copy(source, target):
            original(source, target)
            raise RuntimeError('injected failure')

        with patch(
            'scheduler_app.default_dataset_copy._copy_graph',
            side_effect=fail_after_copy,
        ):
            with self.assertRaises(RuntimeError):
                copy_default_dataset_to_organization(
                    TARGET_NAME,
                    confirmed=True,
                )

        self.assertFalse(
            Organization.objects.filter(name=TARGET_NAME).exists()
        )
        self.assertEqual(_organization_graph(self.source), source_graph)

    def test_deleting_target_does_not_change_source(self):
        source_graph = deepcopy(_organization_graph(self.source))
        result = copy_default_dataset_to_organization(
            TARGET_NAME,
            confirmed=True,
        )

        # PROTECT relationships require deleting owned records in dependency
        # order; target deletion is exercised only inside this test transaction.
        target = result.target
        InstructorScheduleAvailability.objects.filter(
            organization=target,
        ).delete()
        InstructorScheduleParticipation.objects.filter(
            organization=target,
        ).delete()
        TheSched.objects.filter(organization=target).delete()
        Schools._default_manager.filter(organization=target).delete()
        Instructor.objects.filter(organization=target).delete()
        Course.objects.filter(organization=target).delete()
        Certification.objects.filter(organization=target).delete()
        LeadershipRole.objects.filter(organization=target).delete()
        Locations.objects.filter(organization=target).delete()
        target.delete()

        self.assertEqual(_organization_graph(self.source), source_graph)

    def test_command_reports_target_diagnostics_without_credentials(self):
        output = StringIO()

        call_command(
            'copy_default_dataset_to_organization',
            target_organization=TARGET_NAME,
            confirm=True,
            stdout=output,
        )

        text = output.getvalue()
        self.assertIn('Default dataset copied atomically.', text)
        self.assertIn('Target membership: none', text)
        self.assertNotIn('password', text.lower())
        self.assertNotIn('@', text)
        self.assertNotIn('session key', text.lower())
