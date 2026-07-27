from copy import deepcopy
from datetime import timedelta
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.test import TestCase, override_settings
from django.utils import timezone

from members.models import DemoSession, Organization, OrganizationMembership

from .demo_scaffolding import (
    DEMO_SCENARIO,
    PREPARED_DEMO_SCENARIO_VERSION,
    apply_demo_reference_data,
)
from .demo_session_provisioning import (
    IDENTITY_COLLISION_RETRY_LIMIT,
    MAX_CLEAN_DEMO_SESSION_LIFETIME,
    CleanDemoIdentityCollisionError,
    CleanDemoLifetimeError,
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
from .prepared_demo_provisioning import (
    DEFAULT_PREPARED_DEMO_SESSION_LIFETIME,
    provision_prepared_demo_session,
)
from .schedule_blocks import UNASSIGNED_SLOT_VALUE, UNAVAILABLE_SLOT_VALUE


def uuid_sequence(*values):
    iterator = iter(values)
    return lambda: next(iterator)


class PreparedDemoProvisioningTests(TestCase):
    def test_creates_aligned_active_prepared_ownership(self):
        before = (
            get_user_model().objects.count(),
            Organization.objects.count(),
            OrganizationMembership.objects.count(),
            DemoSession.objects.count(),
        )

        result = provision_prepared_demo_session()

        self.assertTrue(result.completed)
        self.assertEqual(get_user_model().objects.count(), before[0] + 1)
        self.assertEqual(Organization.objects.count(), before[1] + 1)
        self.assertEqual(OrganizationMembership.objects.count(), before[2] + 1)
        self.assertEqual(DemoSession.objects.count(), before[3] + 1)
        self.assertEqual(result.membership.user, result.user)
        self.assertEqual(result.membership.organization, result.organization)
        self.assertEqual(result.demo_session.user, result.user)
        self.assertEqual(result.demo_session.organization, result.organization)
        self.assertEqual(
            result.organization.purpose,
            Organization.Purpose.TEMPORARY_DEMO,
        )
        self.assertEqual(result.demo_session.mode, DemoSession.Mode.PREPARED)
        self.assertEqual(result.demo_session.status, DemoSession.Status.ACTIVE)
        self.assertEqual(
            result.demo_session.scenario_version,
            PREPARED_DEMO_SCENARIO_VERSION,
        )
        self.assertTrue(result.user.is_active)
        self.assertFalse(result.user.is_staff)
        self.assertFalse(result.user.is_superuser)
        self.assertFalse(result.user.has_usable_password())

    def test_session_is_provisioning_during_build_and_active_when_returned(self):
        observed = []

        def inspect_build(organization, demo_session):
            observed.append(demo_session.status)
            from .prepared_demo_provisioning import _construct_prepared_scenario
            return original(organization, demo_session)

        from .prepared_demo_provisioning import _construct_prepared_scenario
        original = _construct_prepared_scenario
        with patch(
            'scheduler_app.prepared_demo_provisioning._construct_prepared_scenario',
            side_effect=inspect_build,
        ):
            result = provision_prepared_demo_session()

        self.assertEqual(observed, [DemoSession.Status.PROVISIONING])
        self.assertEqual(result.demo_session.status, DemoSession.Status.ACTIVE)

    def test_reference_data_exactly_matches_scenario(self):
        organization = provision_prepared_demo_session().organization

        self.assertSetEqual(
            set(Locations.objects.filter(organization=organization).values_list(
                'loc_name',
                flat=True,
            )),
            {item['name'] for item in DEMO_SCENARIO['locations']},
        )
        self.assertSetEqual(
            set(Certification.objects.filter(organization=organization).values_list(
                'name',
                flat=True,
            )),
            set(DEMO_SCENARIO['certifications']),
        )
        courses = Course.objects.filter(organization=organization)
        self.assertSetEqual(
            set(courses.values_list('course_name', flat=True)),
            {item['name'] for item in DEMO_SCENARIO['activities']},
        )
        for expected in DEMO_SCENARIO['activities']:
            course = courses.get(course_name=expected['name'])
            self.assertSetEqual(
                set(course.primary_locs.values_list('loc_name', flat=True)),
                set(expected['locations']),
            )
        self.assertEqual(
            ActivityCertificationRequirement.objects.filter(
                course__organization=organization
            ).count(),
            1,
        )
        schools = Schools.schools_list.filter(organization=organization)
        self.assertEqual(schools.count(), len(DEMO_SCENARIO['schools']))
        for expected in DEMO_SCENARIO['schools']:
            school = schools.get(school_name=expected['name'])
            self.assertEqual(school.ag_num, expected['groups'])
            self.assertSetEqual(
                set(school.subject.values_list('course_name', flat=True)),
                set(expected['activities']),
            )
        instructors = Instructor.objects.filter(organization=organization)
        self.assertEqual(instructors.count(), len(DEMO_SCENARIO['instructors']))
        self.assertEqual(
            InstructorCertification.objects.filter(
                instructor__organization=organization
            ).count(),
            sum(
                len(item['certifications'])
                for item in DEMO_SCENARIO['instructors']
            ),
        )

    def test_schedule_relationships_participation_and_availability_match(self):
        result = provision_prepared_demo_session()
        schedule = result.schedule

        self.assertEqual(schedule.organization, result.organization)
        self.assertEqual(schedule.sched_name, DEMO_SCENARIO['schedule']['name'])
        self.assertSetEqual(
            set(schedule.schools.values_list('school_name', flat=True)),
            set(DEMO_SCENARIO['schedule']['schools']),
        )
        participation = InstructorScheduleParticipation.objects.get(
            organization=result.organization,
            schedule=schedule,
        )
        self.assertEqual(
            (participation.instructor.fname, participation.instructor.lname),
            (
                DEMO_SCENARIO['participation_opt_out']['first'],
                DEMO_SCENARIO['participation_opt_out']['last'],
            ),
        )
        self.assertEqual(
            participation.state,
            InstructorScheduleParticipation.NOT_PARTICIPATING,
        )
        availability = InstructorScheduleAvailability.objects.get(
            organization=result.organization,
            schedule=schedule,
        )
        self.assertEqual(
            availability.slot_key,
            DEMO_SCENARIO['availability_exception']['slot_key'],
        )
        self.assertEqual(
            availability.state,
            InstructorScheduleAvailability.UNAVAILABLE,
        )

    def test_generation_and_operational_state_use_normal_lifecycle(self):
        with patch.object(
            TheSched,
            'generate_and_store_schedule',
            autospec=True,
            wraps=TheSched.generate_and_store_schedule,
        ) as generate:
            result = provision_prepared_demo_session()

        generate.assert_called_once()
        result.schedule.refresh_from_db()
        data = result.schedule.sched_data
        generated = data['generated_schedule']
        self.assertTrue(data['generation_complete'])
        self.assertEqual(data['manual_moves'], [])
        self.assertEqual(data['manual_instructor_overrides'], [])
        self.assertEqual(data['instructor_override_revision'], 0)
        self.assertEqual(
            len(generated['ags']),
            DEMO_SCENARIO['expected_group_count'],
        )
        self.assertTrue(
            set(DEMO_SCENARIO['required_schedule_slots']) <= set(generated)
        )
        real_values = {
            value
            for slot in DEMO_SCENARIO['required_schedule_slots']
            for value in generated[slot]
            if value not in {UNASSIGNED_SLOT_VALUE, UNAVAILABLE_SLOT_VALUE}
        }
        self.assertSetEqual(
            real_values,
            {item['name'] for item in DEMO_SCENARIO['activities']},
        )
        replay = result.schedule.get_display_schedule_result()[
            'override_replay_result'
        ]
        self.assertFalse(replay['replay_conflicts'])
        self.assertFalse(replay['ignored_overrides'])
        self.assertFalse(replay['holding_area'])

    def test_assignment_is_complete_qualified_available_and_isolated(self):
        result = provision_prepared_demo_session()
        assignment = run_instructor_assignment(result.schedule)

        self.assertTrue(assignment['coverage']['complete'])
        self.assertEqual(assignment['organization_id'], result.organization.pk)
        organization_instructors = set(
            Instructor.objects.filter(
                organization=result.organization
            ).values_list('pk', flat=True)
        )
        assigned = {
            item['assigned_instructor'].pk
            for item in assignment['assignments']
            if item['assigned_instructor']
        }
        self.assertTrue(assigned <= organization_instructors)
        opt_out = DEMO_SCENARIO['participation_opt_out']
        opted_out = Instructor.objects.get(
            organization=result.organization,
            fname=opt_out['first'],
            lname=opt_out['last'],
        )
        self.assertNotIn(opted_out.pk, assigned)
        self.assertTrue(result.validation_summary['assignment_complete'])


class PreparedDemoLifetimeCollisionTests(TestCase):
    def test_default_custom_lifetime_and_trusted_clock(self):
        now = timezone.now()
        default = provision_prepared_demo_session(clock=lambda: now)
        custom = provision_prepared_demo_session(
            lifetime=timedelta(minutes=15),
            clock=lambda: now,
        )

        self.assertEqual(
            default.expires_at,
            now + DEFAULT_PREPARED_DEMO_SESSION_LIFETIME,
        )
        self.assertEqual(custom.demo_session.last_activity_at, now)
        self.assertEqual(custom.expires_at, now + timedelta(minutes=15))

    def test_invalid_lifetimes_and_capacity_denial_precede_writes(self):
        baseline = (
            get_user_model().objects.count(),
            Organization.objects.count(),
            DemoSession.objects.count(),
        )
        for lifetime in (
            timedelta(0),
            timedelta(seconds=-1),
            MAX_CLEAN_DEMO_SESSION_LIFETIME + timedelta(seconds=1),
        ):
            with self.assertRaises(CleanDemoLifetimeError):
                provision_prepared_demo_session(lifetime=lifetime)
        with patch(
            'scheduler_app.prepared_demo_provisioning._enforce_capacity_policy',
            side_effect=RuntimeError('capacity denied'),
        ):
            with self.assertRaisesMessage(RuntimeError, 'capacity denied'):
                provision_prepared_demo_session()
        self.assertEqual(
            baseline,
            (
                get_user_model().objects.count(),
                Organization.objects.count(),
                DemoSession.objects.count(),
            ),
        )

    def test_collisions_retry_without_reuse_and_exhaustion_rolls_back(self):
        collision = uuid.uuid4()
        replacement = uuid.uuid4()
        organization_token = uuid.uuid4()
        existing = get_user_model().objects.create_user(
            username=f'demo_{collision.hex}'
        )

        result = provision_prepared_demo_session(
            identity_factory=uuid_sequence(
                collision,
                replacement,
                organization_token,
            )
        )
        self.assertNotEqual(result.user.pk, existing.pk)

        before = (
            get_user_model().objects.count(),
            Organization.objects.count(),
            DemoSession.objects.count(),
        )
        with self.assertRaises(CleanDemoIdentityCollisionError):
            provision_prepared_demo_session(identity_factory=lambda: collision)
        self.assertEqual(
            before,
            (
                get_user_model().objects.count(),
                Organization.objects.count(),
                DemoSession.objects.count(),
            ),
        )


class PreparedDemoAtomicityTests(TestCase):
    def all_counts(self):
        return (
            get_user_model().objects.count(),
            Organization.objects.count(),
            OrganizationMembership.objects.count(),
            DemoSession.objects.count(),
            Locations.objects.count(),
            Certification.objects.count(),
            Course.objects.count(),
            Schools.schools_list.count(),
            Instructor.objects.count(),
            TheSched.objects.count(),
            InstructorScheduleParticipation.objects.count(),
            InstructorScheduleAvailability.objects.count(),
        )

    def test_failures_at_every_prepared_stage_roll_back_everything(self):
        stages = (
            'scheduler_app.demo_session_provisioning._build_demo_session',
            'scheduler_app.prepared_demo_provisioning._construct_prepared_scenario',
            'scheduler_app.demo_scaffolding._apply_certifications',
            'scheduler_app.demo_scaffolding._apply_activity_requirement',
            'scheduler_app.demo_scaffolding._apply_instructors',
            'scheduler_app.demo_scaffolding._apply_instructor_certifications',
            'scheduler_app.demo_scaffolding._apply_schedule',
            'scheduler_app.demo_scaffolding._apply_participation',
            'scheduler_app.demo_scaffolding._apply_availability',
            'scheduler_app.demo_scaffolding._validate_applied_reference_data',
            'scheduler_app.demo_scaffolding._validate_demo_starting_state',
            'scheduler_app.prepared_demo_provisioning._activate_prepared_session',
            'scheduler_app.prepared_demo_provisioning._final_prepared_verification',
        )
        for stage in stages:
            with self.subTest(stage=stage):
                baseline = self.all_counts()
                with patch(stage, side_effect=RuntimeError('injected failure')):
                    with self.assertRaisesMessage(
                        RuntimeError,
                        'injected failure',
                    ):
                        provision_prepared_demo_session()
                self.assertEqual(self.all_counts(), baseline)

    def test_assignment_validation_failure_rolls_back_everything(self):
        baseline = self.all_counts()
        with patch(
            'scheduler_app.demo_scaffolding.run_instructor_assignment',
            side_effect=RuntimeError('assignment validation failed'),
        ):
            with self.assertRaisesMessage(
                Exception,
                'assignment validation failed',
            ):
                provision_prepared_demo_session()
        self.assertEqual(self.all_counts(), baseline)

    def test_generation_failure_rolls_back_everything(self):
        baseline = self.all_counts()
        with patch.object(
            TheSched,
            'generate_and_store_schedule',
            side_effect=RuntimeError('generation failed'),
        ):
            with self.assertRaisesMessage(RuntimeError, 'generation failed'):
                provision_prepared_demo_session()
        self.assertEqual(self.all_counts(), baseline)


class PreparedDemoIsolationTests(TestCase):
    @override_settings(
        DEMO_SCAFFOLDING_ENABLED=True,
        DEMO_ORGANIZATION_IDENTIFIER='Canonical Source',
    )
    def test_canonical_database_rows_and_json_are_not_read_or_copied(self):
        canonical = Organization.objects.create(
            name='Canonical Source',
            purpose=Organization.Purpose.CANONICAL_DEMO,
        )
        canonical_user = get_user_model().objects.create_user(username='canonical')
        OrganizationMembership.objects.create(
            user=canonical_user,
            organization=canonical,
        )
        apply_demo_reference_data('Canonical Source')
        canonical_schedule = TheSched.objects.get(organization=canonical)
        canonical_json = deepcopy(canonical_schedule.sched_data)
        canonical_state = {
            'organization_updated': canonical.updated_at,
            'location_ids': set(canonical.locations.values_list('pk', flat=True)),
            'course_ids': set(canonical.courses.values_list('pk', flat=True)),
            'schedule_id': canonical_schedule.pk,
            'schedule_updated': canonical_schedule.timestamp_og,
        }

        with (
            patch(
                'scheduler_app.canonical_classification.classify_canonical_demo_organization'
            ) as classify,
            patch(
                'scheduler_app.demo_scaffolding.apply_demo_reference_data',
                wraps=apply_demo_reference_data,
            ) as canonical_apply,
            patch('scheduler_app.demo_scaffolding.reset_demo_environment') as reset,
        ):
            result = provision_prepared_demo_session()

        classify.assert_not_called()
        canonical_apply.assert_not_called()
        reset.assert_not_called()
        canonical.refresh_from_db()
        canonical_schedule.refresh_from_db()
        self.assertEqual(canonical.updated_at, canonical_state['organization_updated'])
        self.assertEqual(canonical_schedule.sched_data, canonical_json)
        self.assertEqual(
            canonical_schedule.timestamp_og,
            canonical_state['schedule_updated'],
        )
        self.assertTrue(
            set(result.organization.locations.values_list('pk', flat=True)).isdisjoint(
                canonical_state['location_ids']
            )
        )
        self.assertTrue(
            set(result.organization.courses.values_list('pk', flat=True)).isdisjoint(
                canonical_state['course_ids']
            )
        )
        self.assertNotEqual(result.schedule.pk, canonical_state['schedule_id'])

    def test_two_sessions_are_independent_and_manual_mutation_does_not_cross(self):
        first = provision_prepared_demo_session()
        second = provision_prepared_demo_session()

        self.assertNotEqual(first.user.pk, second.user.pk)
        self.assertNotEqual(first.organization.pk, second.organization.pk)
        self.assertNotEqual(first.membership.pk, second.membership.pk)
        self.assertNotEqual(
            first.demo_session.identifier,
            second.demo_session.identifier,
        )
        self.assertNotEqual(first.schedule.pk, second.schedule.pk)
        self.assertFalse(
            first.schedule.schools.filter(
                organization=second.organization
            ).exists()
        )
        first.schedule.sched_data['manual_moves'] = [{'test': 'first only'}]
        first.schedule.save(update_fields=('sched_data',))
        second.schedule.refresh_from_db()
        self.assertEqual(second.schedule.sched_data['manual_moves'], [])

    def test_no_authentication_or_browser_session_is_created(self):
        with (
            patch('django.contrib.auth.login') as login,
            patch('django.contrib.auth.authenticate') as authenticate,
        ):
            result = provision_prepared_demo_session()

        login.assert_not_called()
        authenticate.assert_not_called()
        self.assertFalse(result.user.has_usable_password())
        self.assertEqual(Session.objects.count(), 0)
