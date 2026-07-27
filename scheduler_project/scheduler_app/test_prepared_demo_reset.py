from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import authenticate, get_user_model, login, logout
from django.test import TestCase
from django.utils import timezone

from members.models import DemoSession, Organization, OrganizationMembership

from .demo_scaffolding import DEMO_SCENARIO, PREPARED_DEMO_SCENARIO_VERSION
from .demo_session_provisioning import provision_clean_demo_session
from .instructor_assignment import run_instructor_assignment
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
from .prepared_demo_provisioning import provision_prepared_demo_session
from .prepared_demo_reset import (
    PreparedDemoResetError,
    reset_prepared_demo_session,
)


class PreparedDemoResetTests(TestCase):
    def test_mutated_prepared_session_is_fully_restored_and_ownership_preserved(self):
        result = provision_prepared_demo_session()
        ownership = (
            result.user.pk,
            result.organization.pk,
            result.membership.pk,
            result.demo_session.pk,
            result.demo_session.identifier,
            result.demo_session.mode,
            result.demo_session.status,
            result.demo_session.scenario_version,
            result.demo_session.expires_at,
        )
        course = Course.objects.get(
            organization=result.organization,
            course_name='Demo Navigation',
        )
        course.abriviation = 'BAD'
        course.save(update_fields=('abriviation',))
        school = Schools.schools_list.get(
            organization=result.organization,
            school_name='Demo Cohort North',
        )
        school.subject.clear()
        instructor = Instructor.objects.get(
            organization=result.organization,
            fname='Alex',
        )
        instructor.certifications.clear()
        InstructorScheduleParticipation.objects.all().delete()
        InstructorScheduleAvailability.objects.all().delete()
        dirty = deepcopy(result.schedule.sched_data)
        dirty['manual_moves'] = [{'activity': 'Demo Navigation'}]
        dirty['manual_instructor_overrides'] = [{'event': 'assign'}]
        dirty['instructor_override_revision'] = 9
        result.schedule.sched_data = dirty
        result.schedule.save(update_fields=('sched_data',))

        reset = reset_prepared_demo_session(demo_session=result.demo_session)

        self.assertTrue(reset.completed)
        self.assertFalse(reset.already_canonical)
        self.assertEqual(
            (
                reset.user.pk,
                reset.organization.pk,
                reset.membership.pk,
                reset.demo_session.pk,
                reset.demo_session.identifier,
                reset.demo_session.mode,
                reset.demo_session.status,
                reset.demo_session.scenario_version,
                reset.demo_session.expires_at,
            ),
            ownership,
        )
        course.refresh_from_db()
        self.assertEqual(course.abriviation, 'DNAV')
        school.refresh_from_db()
        self.assertSetEqual(
            set(school.subject.values_list('course_name', flat=True)),
            {
                'Demo Navigation',
                'Demo Technical Course',
                'Demo Evening Program',
            },
        )
        self.assertEqual(instructor.certifications.count(), 1)
        self.assertEqual(
            InstructorScheduleParticipation.objects.filter(
                organization=result.organization
            ).count(),
            1,
        )
        self.assertEqual(
            InstructorScheduleAvailability.objects.filter(
                organization=result.organization
            ).count(),
            1,
        )
        self.assertEqual(reset.schedule.sched_data['manual_moves'], [])
        self.assertEqual(
            reset.schedule.sched_data['manual_instructor_overrides'],
            [],
        )
        self.assertEqual(reset.schedule.sched_data['instructor_override_revision'], 0)
        self.assertTrue(run_instructor_assignment(reset.schedule)['coverage']['complete'])

    def test_extra_visitor_operational_graph_is_removed(self):
        result = provision_prepared_demo_session()
        location = Locations.objects.create(
            organization=result.organization,
            loc_name='Visitor Place',
            loc_short='VP',
        )
        certification = Certification.objects.create(
            organization=result.organization,
            name='Visitor Certification',
        )
        course = Course.objects.create(
            organization=result.organization,
            course_name='Visitor Activity',
            abriviation='VACT',
        )
        course.primary_locs.add(location)
        school = Schools.schools_list.create(
            organization=result.organization,
            school_name='Visitor Cohort',
            ag_num=1,
            arrive='Mon',
            depart='Fri',
            total_students=1,
            attending_year=timezone.now().date(),
        )
        school.subject.add(course)
        instructor = Instructor.objects.create(
            organization=result.organization,
            fname='Visitor',
            lname='Instructor',
        )
        instructor.certifications.add(certification)
        schedule = TheSched.objects.create(
            organization=result.organization,
            sched_name='Visitor Schedule',
            sched_data={'version': 1},
        )
        schedule.schools.add(school)
        InstructorScheduleParticipation.objects.create(
            organization=result.organization,
            schedule=schedule,
            instructor=instructor,
            state=InstructorScheduleParticipation.NOT_PARTICIPATING,
        )
        InstructorScheduleAvailability.objects.create(
            organization=result.organization,
            schedule=schedule,
            instructor=instructor,
            slot_key='mon_pm1',
            state=InstructorScheduleAvailability.UNAVAILABLE,
        )

        reset = reset_prepared_demo_session(demo_session=result.demo_session)

        self.assertFalse(Locations.objects.filter(pk=location.pk).exists())
        self.assertFalse(Certification.objects.filter(pk=certification.pk).exists())
        self.assertFalse(Course.objects.filter(pk=course.pk).exists())
        self.assertFalse(Schools.schools_list.filter(pk=school.pk).exists())
        self.assertFalse(Instructor.objects.filter(pk=instructor.pk).exists())
        self.assertFalse(TheSched.objects.filter(pk=schedule.pk).exists())
        self.assertTrue(Organization.objects.filter(pk=result.organization.pk).exists())
        self.assertEqual(len(reset.deleted), 6)

    def test_already_canonical_is_a_no_op(self):
        result = provision_prepared_demo_session()
        data = deepcopy(result.schedule.sched_data)
        timestamps = (
            result.demo_session.created_at,
            result.demo_session.last_activity_at,
            result.demo_session.expires_at,
        )

        with patch.object(
            TheSched,
            'generate_and_store_schedule',
            autospec=True,
        ) as generate:
            reset = reset_prepared_demo_session(demo_session=result.demo_session)

        self.assertTrue(reset.already_canonical)
        generate.assert_not_called()
        reset.demo_session.refresh_from_db()
        self.assertEqual(reset.schedule.sched_data, data)
        self.assertEqual(
            (
                reset.demo_session.created_at,
                reset.demo_session.last_activity_at,
                reset.demo_session.expires_at,
            ),
            timestamps,
        )
        self.assertFalse(reset.applied.created)
        self.assertFalse(reset.applied.updated)
        self.assertFalse(reset.applied.reconciled)
        self.assertFalse(reset.deleted)

    def test_invalid_targets_fail_without_writes(self):
        clean = provision_clean_demo_session()
        prepared = provision_prepared_demo_session()
        cases = (
            (clean.demo_session, {}),
            (prepared.demo_session, {'status': DemoSession.Status.EXPIRING}),
            (prepared.demo_session, {'scenario_version': 'other'}),
        )
        for target, changes in cases:
            with self.subTest(changes=changes):
                DemoSession.objects.filter(pk=target.pk).update(**changes)
                before = (
                    get_user_model().objects.count(),
                    Organization.objects.count(),
                    OrganizationMembership.objects.count(),
                    DemoSession.objects.count(),
                    TheSched.objects.count(),
                )
                with self.assertRaises(PreparedDemoResetError):
                    reset_prepared_demo_session(demo_session=target)
                self.assertEqual(
                    before,
                    (
                        get_user_model().objects.count(),
                        Organization.objects.count(),
                        OrganizationMembership.objects.count(),
                        DemoSession.objects.count(),
                        TheSched.objects.count(),
                    ),
                )
                DemoSession.objects.filter(pk=prepared.demo_session.pk).update(
                    status=DemoSession.Status.ACTIVE,
                    scenario_version=PREPARED_DEMO_SCENARIO_VERSION,
                )

    def test_expired_privileged_permanent_and_missing_membership_are_refused(self):
        mutators = (
            lambda result: DemoSession.objects.filter(pk=result.demo_session.pk).update(
                expires_at=result.demo_session.created_at + timedelta(seconds=1)
            ),
            lambda result: get_user_model().objects.filter(pk=result.user.pk).update(
                is_staff=True
            ),
            lambda result: get_user_model().objects.filter(pk=result.user.pk).update(
                is_superuser=True
            ),
            lambda result: Organization.objects.filter(pk=result.organization.pk).update(
                purpose=Organization.Purpose.CUSTOMER
            ),
            lambda result: OrganizationMembership.objects.filter(
                pk=result.membership.pk
            ).delete(),
        )
        for mutate in mutators:
            with self.subTest(mutate=mutate):
                result = provision_prepared_demo_session()
                mutate(result)
                with self.assertRaises(PreparedDemoResetError):
                    reset_prepared_demo_session(
                        demo_session=result.demo_session,
                        clock=lambda: result.demo_session.created_at + timedelta(minutes=1),
                    )

    def test_missing_schedule_and_foreign_relationship_are_refused(self):
        missing = provision_prepared_demo_session()
        missing.schedule.delete()
        with self.assertRaises(PreparedDemoResetError):
            reset_prepared_demo_session(demo_session=missing.demo_session)

        target = provision_prepared_demo_session()
        foreign = provision_prepared_demo_session()
        course = Course.objects.get(
            organization=target.organization,
            course_name='Demo Navigation',
        )
        foreign_location = Locations.objects.get(
            organization=foreign.organization,
            loc_name='Demo Field',
        )
        Course.primary_locs.through.objects.create(
            course=course,
            locations=foreign_location,
        )
        target_data = deepcopy(target.schedule.sched_data)
        foreign_data = deepcopy(foreign.schedule.sched_data)

        with self.assertRaises(PreparedDemoResetError):
            reset_prepared_demo_session(demo_session=target.demo_session)

        target.schedule.refresh_from_db()
        foreign.schedule.refresh_from_db()
        self.assertEqual(target.schedule.sched_data, target_data)
        self.assertEqual(foreign.schedule.sched_data, foreign_data)
        self.assertTrue(
            Course.primary_locs.through.objects.filter(
                course=course,
                locations=foreign_location,
            ).exists()
        )

    def test_failure_after_deletion_or_reconciliation_rolls_back(self):
        stages = (
            'scheduler_app.prepared_demo_reset._apply_canonical_scenario',
            'scheduler_app.prepared_demo_reset._validate_demo_starting_state',
        )
        for stage in stages:
            with self.subTest(stage=stage):
                result = provision_prepared_demo_session()
                extra = Locations.objects.create(
                    organization=result.organization,
                    loc_name='Rollback Place',
                    loc_short='RP',
                )
                canonical = Locations.objects.get(
                    organization=result.organization,
                    loc_name='Demo Commons',
                )
                canonical.loc_short = 'BAD'
                canonical.save(update_fields=('loc_short',))
                before_data = deepcopy(result.schedule.sched_data)
                with patch(stage, side_effect=RuntimeError('injected failure')):
                    with self.assertRaisesMessage(
                        PreparedDemoResetError,
                        'all reset writes were rolled back',
                    ):
                        reset_prepared_demo_session(demo_session=result.demo_session)
                canonical.refresh_from_db()
                result.schedule.refresh_from_db()
                self.assertTrue(Locations.objects.filter(pk=extra.pk).exists())
                self.assertEqual(canonical.loc_short, 'BAD')
                self.assertEqual(result.schedule.sched_data, before_data)

    def test_target_reset_is_isolated_and_sequential_retry_is_no_op(self):
        target = provision_prepared_demo_session()
        other = provision_prepared_demo_session()
        other_data = deepcopy(other.schedule.sched_data)
        Locations.objects.create(
            organization=target.organization,
            loc_name='Target Extra',
            loc_short='TE',
        )

        first = reset_prepared_demo_session(demo_session=target.demo_session)
        second = reset_prepared_demo_session(demo_session=target.demo_session)

        self.assertFalse(first.already_canonical)
        self.assertTrue(second.already_canonical)
        other.schedule.refresh_from_db()
        self.assertEqual(other.schedule.sched_data, other_data)
        self.assertEqual(
            Locations.objects.filter(organization=other.organization).count(),
            len(DEMO_SCENARIO['locations']),
        )

    def test_service_has_no_authentication_or_cleanup_behavior(self):
        result = provision_prepared_demo_session()
        with (
            patch('django.contrib.auth.authenticate', wraps=authenticate) as auth,
            patch('django.contrib.auth.login', wraps=login) as sign_in,
            patch('django.contrib.auth.logout', wraps=logout) as sign_out,
            patch(
                'scheduler_app.demo_session_cleanup.cleanup_demo_session'
            ) as cleanup,
        ):
            reset_prepared_demo_session(demo_session=result.demo_session)

        auth.assert_not_called()
        sign_in.assert_not_called()
        sign_out.assert_not_called()
        cleanup.assert_not_called()
