from copy import deepcopy

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import RequestFactory, TestCase, TransactionTestCase

from members.models import Organization

from .forms import CourseForm
from .instructor_assignment import (
    extract_operational_occurrences,
    run_instructor_assignment,
)
from .models import Course, Instructor, Locations, TheSched


def stored_schedule(activity_name, *, two_slots=False):
    generated_schedule = {
        'ags': ['School 0'],
        'tue_am1': [activity_name],
    }
    if two_slots:
        generated_schedule['tue_am2'] = [activity_name]
    return {
        'version': 1,
        'generated_schedule': generated_schedule,
        'manual_moves': [],
        'generation_diagnostics': [],
        'generation_runtime_diagnostics': [],
        'generation_complete': True,
    }


class RequiredInstructorCountMigrationTests(TransactionTestCase):
    migrate_from = ('scheduler_app', '0033_instructorscheduleparticipation')
    migrate_to = ('scheduler_app', '0034_course_required_instructor_count')

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        OrganizationModel = old_apps.get_model('members', 'Organization')
        CourseModel = old_apps.get_model('scheduler_app', 'Course')
        organization = OrganizationModel.objects.create(name='Migration Organization')
        self.course_id = CourseModel.objects.create(
            organization=organization,
            course_name='Existing Activity',
            abriviation='EXST',
            course_len=1,
        ).pk

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        self.apps = executor.loader.project_state([self.migrate_to]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_course_receives_default_of_one(self):
        CourseModel = self.apps.get_model('scheduler_app', 'Course')

        course = CourseModel.objects.get(pk=self.course_id)

        self.assertEqual(course.required_instructor_count, 1)


class RequiredInstructorCountFoundationTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Staffing Organization')
        self.other_organization = Organization.objects.create(name='Other Organization')
        self.location = Locations.objects.create(
            organization=self.organization,
            loc_name='Staffing Field',
            loc_short='SF',
        )

    def create_course(self, **overrides):
        values = {
            'organization': self.organization,
            'course_name': 'Archery',
            'abriviation': 'ARCH',
            'course_len': 1,
        }
        values.update(overrides)
        return Course.objects.create(**values)

    def create_schedule(self, course, *, two_slots=False):
        return TheSched.objects.create(
            organization=self.organization,
            sched_name=f'Staffing Schedule {TheSched.objects.count()}',
            sched_data=stored_schedule(course.course_name, two_slots=two_slots),
        )

    def test_new_course_defaults_to_one_and_programmatic_larger_values_remain_stored(self):
        default_course = self.create_course()
        larger_course = self.create_course(
            course_name='Climbing',
            abriviation='CLMB',
            required_instructor_count=3,
        )

        self.assertEqual(default_course.required_instructor_count, 1)
        self.assertEqual(larger_course.required_instructor_count, 3)

    def test_model_rejects_values_below_one_and_form_does_not_expose_field(self):
        course = self.create_course(required_instructor_count=0)

        with self.assertRaises(ValidationError) as context:
            course.full_clean()

        self.assertIn('required_instructor_count', context.exception.message_dict)

        form = CourseForm(organization=self.organization)
        self.assertNotIn('required_instructor_count', form.fields)

    def test_form_preserves_server_owned_organization(self):
        form = CourseForm(
            data={
                'course_name': 'Scoped Staffing',
                'abriviation': 'SCOP',
                'course_len': '1',
                'required_instructor_count': '2',
                'primary_locs': [str(self.location.pk)],
                'organization': str(self.other_organization.pk),
            },
            organization=self.organization,
        )

        self.assertTrue(form.is_valid(), form.errors)
        course = form.save(commit=False)
        course.organization = self.organization
        course.save()
        self.assertEqual(course.organization, self.organization)
        self.assertEqual(course.required_instructor_count, 1)

    def test_admin_excludes_dormant_field_from_normal_editing(self):
        course_admin = admin.site._registry[Course]
        request = RequestFactory().get('/admin/scheduler_app/course/add/')
        request.user = get_user_model().objects.create_superuser(
            username='staffing-admin',
            password='password',
        )

        self.assertNotIn(
            'required_instructor_count',
            course_admin.get_form(request=request).base_fields,
        )

    def test_occurrence_uses_current_course_value_without_regeneration(self):
        course = self.create_course(required_instructor_count=1)
        schedule = self.create_schedule(course)

        first_occurrences = extract_operational_occurrences(schedule)
        course.required_instructor_count = 2
        course.save(update_fields=['required_instructor_count'])
        second_occurrences = extract_operational_occurrences(schedule)

        self.assertEqual(first_occurrences[0]['required_instructor_count'], 1)
        self.assertEqual(second_occurrences[0]['required_instructor_count'], 2)
        schedule.refresh_from_db()
        self.assertNotIn(
            'required_instructor_count',
            str(schedule.sched_data['generated_schedule']),
        )

    def test_multi_slot_occurrence_has_one_count_and_complete_footprint(self):
        course = self.create_course(
            course_len=2,
        )
        schedule = self.create_schedule(course, two_slots=True)

        occurrences = extract_operational_occurrences(schedule)

        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0]['required_instructor_count'], 1)
        self.assertEqual(
            [slot['slot_key'] for slot in occurrences[0]['slot_footprint']],
            ['tue_am1', 'tue_am2'],
        )

    def test_requirement_one_uses_existing_single_instructor_behavior(self):
        course = self.create_course()
        schedule = self.create_schedule(course)
        instructor = Instructor.objects.create(
            organization=self.organization,
            fname='Single',
            lname='Instructor',
        )

        result = run_instructor_assignment(schedule)

        self.assertEqual(result['occurrences'][0]['required_instructor_count'], 1)
        self.assertEqual(len(result['assignments']), 1)
        self.assertEqual(result['assignments'][0]['assigned_instructor'], instructor)
        self.assertEqual(result['assignments'][0]['status'], 'assigned')

    def test_production_orchestration_accepts_one_equivalent_fixed_occurrence(self):
        course = self.create_course()
        schedule = self.create_schedule(course)
        automatic = Instructor.objects.create(
            organization=self.organization,
            fname='Automatic',
            lname='Instructor',
        )
        selected = Instructor.objects.create(
            organization=self.organization,
            fname='Selected',
            lname='Instructor',
        )
        submitted_occurrence = extract_operational_occurrences(schedule)[0]

        result = run_instructor_assignment(
            schedule,
            fixed_assignments=({
                'occurrence': submitted_occurrence,
                'instructor': selected,
            },),
        )

        self.assertEqual(result['assignments'][0]['assigned_instructor'], selected)
        self.assertEqual(
            result['assignments'][0]['assignment_source'],
            'fixed',
        )
        self.assertTrue(
            result['fixed_assignment_diagnostics'][0]['accepted'],
        )
        self.assertIn(
            automatic,
            result['candidate_instructors'],
        )

    def test_unsupported_count_fails_without_altering_schedule_or_writing_assignments(self):
        course = self.create_course(required_instructor_count=2)
        schedule = self.create_schedule(course)
        stored_before = deepcopy(schedule.sched_data)

        with self.assertRaisesMessage(
            ValidationError,
            'Multi-instructor occurrence staffing is not supported',
        ):
            run_instructor_assignment(schedule)

        schedule.refresh_from_db()
        self.assertEqual(schedule.sched_data, stored_before)
        self.assertEqual(Course.objects.count(), 1)
        self.assertEqual(TheSched.objects.count(), 1)

    def test_unresolved_course_identity_is_not_given_a_default(self):
        schedule = TheSched.objects.create(
            organization=self.organization,
            sched_name='Orphaned Activity Schedule',
            sched_data=stored_schedule('Missing Course'),
        )

        occurrences = extract_operational_occurrences(schedule)

        self.assertIsNone(occurrences[0]['activity_id'])
        self.assertIsNone(occurrences[0]['required_instructor_count'])
        with self.assertRaisesMessage(
            ValidationError,
            'does not resolve to a valid Course',
        ):
            run_instructor_assignment(schedule)
