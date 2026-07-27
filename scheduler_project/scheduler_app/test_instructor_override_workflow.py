from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import signing
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from members.models import Organization, OrganizationMembership

from .instructor_assignment import run_instructor_assignment
from .instructor_overrides import build_occurrence_identity
from .views import INSTRUCTOR_OVERRIDE_SIGNING_SALT
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
    TheSched,
)


class InstructorOverrideWorkflowTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Workflow Org')
        self.other_organization = Organization.objects.create(name='Other Workflow Org')
        self.user = get_user_model().objects.create_user(
            username='override-operator',
            password='password',
        )
        OrganizationMembership.objects.create(
            user=self.user,
            organization=self.organization,
        )
        self.course = Course.objects.create(
            organization=self.organization,
            course_name='Archery',
            abriviation='ARCH',
            course_len=1,
        )
        self.schedule = self.create_schedule(
            'Workflow Week',
            {
                'ags': ['Group 0'],
                'mon_pm1': [self.course.course_name],
                'mon_pm2': ['empty'],
            },
        )
        self.first = self.create_instructor('Avery', 'Alpha')
        self.second = self.create_instructor('Blake', 'Beta')
        self.page_url = reverse(
            'instructor-assignment-schedule',
            args=[self.schedule.pk],
        )
        self.set_url = reverse(
            'instructor-override-set',
            args=[self.schedule.pk],
        )
        self.reset_url = reverse(
            'instructor-override-reset',
            args=[self.schedule.pk],
        )
        self.reset_all_url = reverse(
            'instructor-override-reset-all',
            args=[self.schedule.pk],
        )

    def create_instructor(self, first, last, organization=None):
        return Instructor.objects.create(
            organization=organization or self.organization,
            fname=first,
            lname=last,
        )

    def create_schedule(self, name, generated, organization=None):
        return TheSched.objects.create(
            organization=organization or self.organization,
            sched_name=name,
            sched_data={
                'version': 1,
                'generated_schedule': generated,
                'manual_moves': [],
                'generation_diagnostics': [],
                'generation_runtime_diagnostics': [],
                'generation_complete': True,
            },
        )

    def login(self):
        self.client.force_login(self.user)

    def identity(self, schedule=None, index=0):
        schedule = schedule or self.schedule
        result = run_instructor_assignment(schedule)
        return build_occurrence_identity(
            schedule,
            result['occurrences'][index],
        )

    def json_payload(self, **overrides):
        identity = self.identity()
        payload = {
            'action': 'set',
            'schedule_id': self.schedule.pk,
            'expected_revision': 0,
            'instructor_id': self.second.pk,
            'occurrence_id': identity['occurrence_id'],
            'group_index': identity['group_index'],
            'activity_id': identity['activity_id'],
            'slot_footprint': identity['slot_footprint'],
            'confirm_coverage_reduction': False,
        }
        payload.update(overrides)
        return payload

    def html_payload(self, instructor=None):
        response = self.client.get(self.page_url)
        return {
            'action': 'set',
            'schedule_id': self.schedule.pk,
            'expected_revision': response.context[
                'instructor_override_revision'
            ],
            'occurrence_token': response.context[
                'instructor_override_occurrences'
            ][0]['token'],
            'instructor_id': (instructor or self.second).pk,
        }

    def coverage_reduction_schedule(self):
        fixed = Course.objects.create(
            organization=self.organization,
            course_name='Fixed Multi',
            abriviation='FIXD',
            course_len=2,
        )
        common = Course.objects.create(
            organization=self.organization,
            course_name='Common',
            abriviation='COMM',
            course_len=1,
        )
        scarce = Course.objects.create(
            organization=self.organization,
            course_name='Scarce',
            abriviation='SCRC',
            course_len=1,
        )
        schedule = self.create_schedule(
            'Coverage Reduction Week',
            {
                'ags': ['Group 0', 'Group 1'],
                'mon_pm1': [fixed.course_name, common.course_name],
                'mon_pm2': [fixed.course_name, scarce.course_name],
            },
        )
        certification = Certification.objects.create(
            organization=self.organization,
            name='Scarce Skill',
        )
        InstructorCertification.objects.create(
            instructor=self.second,
            certification=certification,
        )
        ActivityCertificationRequirement.objects.create(
            course=scarce,
            certification=certification,
        )
        return schedule

    def test_endpoint_requires_login_is_post_only_and_scopes_schedule(self):
        anonymous = self.client.post(self.set_url, {})
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn(reverse('login'), anonymous['Location'])

        self.login()
        self.assertEqual(self.client.get(self.set_url).status_code, 405)
        foreign = self.create_schedule(
            'Foreign Week',
            {'ags': []},
            organization=self.other_organization,
        )
        response = self.client.post(
            reverse('instructor-override-set', args=[foreign.pk]),
            {},
        )
        self.assertEqual(response.status_code, 404)

    def test_valid_html_submission_persists_redirects_and_refreshes_fixed_plan(self):
        self.login()
        payload = self.html_payload()
        generated_before = deepcopy(
            self.schedule.sched_data['generated_schedule']
        )
        manual_moves_before = deepcopy(
            self.schedule.sched_data['manual_moves']
        )

        response = self.client.post(self.set_url, payload)

        self.assertTrue(response['Location'].startswith(
            f'{self.page_url}#instructor-occurrence-'
        ))
        self.schedule.refresh_from_db()
        self.assertEqual(
            self.schedule.sched_data['generated_schedule'],
            generated_before,
        )
        self.assertEqual(
            self.schedule.sched_data['manual_moves'],
            manual_moves_before,
        )
        self.assertEqual(
            self.schedule.sched_data['instructor_override_revision'],
            1,
        )
        refreshed = self.client.get(self.page_url)
        self.assertContains(refreshed, '>Manual<', html=False)
        self.assertContains(refreshed, str(self.second))
        self.assertContains(
            refreshed,
            'saved manual instructor assignment is active',
        )

    def test_valid_json_submission_returns_safe_success_summary(self):
        self.login()
        payload = self.json_payload()

        with CaptureQueriesContext(connection) as queries:
            response = self.client.post(
                self.set_url,
                payload,
                content_type='application/json',
                HTTP_ACCEPT='application/json',
            )

        self.assertEqual(response.status_code, 200)
        # Request-time temporary-session enforcement adds one joined ownership
        # query for authenticated users before the view executes.
        self.assertLessEqual(len(queries), 26)
        body = response.json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['code'], 'persisted')
        self.assertEqual(body['new_revision'], 1)
        self.assertEqual(body['override']['action'], 'set')
        self.assertEqual(body['override']['instructor_id'], self.second.pk)
        self.assertNotIn('planner_result', body)
        self.assertNotIn('override_id', body['override'])

    def test_malformed_and_mismatched_payloads_return_400_without_writing(self):
        self.login()
        cases = (
            {'expected_revision': None},
            {'schedule_id': self.schedule.pk + 1},
            {'instructor_id': 'invalid'},
            {'action': 'swap'},
            {'slot_footprint': 'invalid'},
            {'slot_footprint': [{'slot_key': 'mon_pm1'}]},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                response = self.client.post(
                    self.set_url,
                    self.json_payload(**overrides),
                    content_type='application/json',
                    HTTP_ACCEPT='application/json',
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()['code'], 'malformed_record')
        self.schedule.refresh_from_db()
        self.assertNotIn(
            'manual_instructor_overrides',
            self.schedule.sched_data,
        )

    def test_stale_revision_and_stale_identity_return_conflict(self):
        self.login()
        stale_revision = self.client.post(
            self.set_url,
            self.json_payload(expected_revision=4),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(stale_revision.status_code, 409)
        self.assertEqual(stale_revision.json()['code'], 'revision_conflict')

        stale_identity = self.client.post(
            self.set_url,
            self.json_payload(activity_id=self.course.pk + 100),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(stale_identity.status_code, 409)
        self.assertEqual(
            stale_identity.json()['code'],
            'stale_occurrence_identity',
        )

    def test_missing_occurrence_instructor_and_foreign_instructor_are_mapped(self):
        self.login()
        missing_occurrence = self.client.post(
            self.set_url,
            self.json_payload(
                occurrence_id='missing',
                activity_id=999999,
            ),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(missing_occurrence.status_code, 400)
        self.assertEqual(missing_occurrence.json()['code'], 'missing_occurrence')

        missing_instructor = self.client.post(
            self.set_url,
            self.json_payload(instructor_id=999999),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(missing_instructor.status_code, 400)
        self.assertEqual(missing_instructor.json()['code'], 'missing_instructor')

        foreign = self.create_instructor(
            'Foreign',
            'Instructor',
            organization=self.other_organization,
        )
        foreign_instructor = self.client.post(
            self.set_url,
            self.json_payload(instructor_id=foreign.pk),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(foreign_instructor.status_code, 400)
        self.assertEqual(
            foreign_instructor.json()['code'],
            'organization_mismatch',
        )

    def test_hard_qualification_participation_and_availability_rejections_are_422(self):
        self.login()
        certification = Certification.objects.create(
            organization=self.organization,
            name='Qualified',
        )
        ActivityCertificationRequirement.objects.create(
            course=self.course,
            certification=certification,
        )
        qualification = self.client.post(
            self.set_url,
            self.json_payload(),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(qualification.status_code, 422)
        self.assertEqual(
            qualification.json()['conflict']['rejection_code'],
            'qualification_requirements_not_met',
        )
        ActivityCertificationRequirement.objects.all().delete()

        InstructorScheduleParticipation.objects.create(
            organization=self.organization,
            schedule=self.schedule,
            instructor=self.second,
            state=InstructorScheduleParticipation.NOT_PARTICIPATING,
        )
        participation = self.client.post(
            self.set_url,
            self.json_payload(),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(participation.status_code, 422)
        self.assertEqual(
            participation.json()['conflict']['rejection_code'],
            'not_participating',
        )
        InstructorScheduleParticipation.objects.all().delete()

        InstructorScheduleAvailability.objects.create(
            organization=self.organization,
            schedule=self.schedule,
            instructor=self.second,
            slot_key='mon_pm1',
            state=InstructorScheduleAvailability.UNAVAILABLE,
        )
        unavailable = self.client.post(
            self.set_url,
            self.json_payload(),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(unavailable.status_code, 422)
        self.assertEqual(
            unavailable.json()['conflict']['rejection_code'],
            'explicitly_unavailable',
        )
        self.assertFalse(unavailable.json()['requires_confirmation'])

    def test_service_diagnostics_have_consistent_http_mapping(self):
        self.login()
        mapping = (
            ('coverage_confirmation_required', 409),
            ('unsupported_multiple_active_overrides', 409),
            ('hard_constraint_rejection', 422),
        )
        for code, expected_status in mapping:
            with self.subTest(code=code), patch(
                'scheduler_app.views.persist_manual_instructor_override',
                return_value={'ok': False, 'code': code},
            ):
                response = self.client.post(
                    self.set_url,
                    self.json_payload(),
                    content_type='application/json',
                    HTTP_ACCEPT='application/json',
                )
                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(response.json()['code'], code)

    def test_off_rejection_is_surfaced_as_hard_and_never_confirmable(self):
        self.login()
        with patch(
            'scheduler_app.views.persist_manual_instructor_override',
            return_value={
                'ok': False,
                'code': 'hard_constraint_rejection',
                'rejection_code': 'daily_off_requirement',
                'affected_slot_keys': ('tue_night',),
            },
        ):
            response = self.client.post(
                self.set_url,
                self.json_payload(),
                content_type='application/json',
                HTTP_ACCEPT='application/json',
            )

        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(
            body['conflict']['rejection_code'],
            'daily_off_requirement',
        )
        self.assertFalse(body['requires_confirmation'])

    def test_multi_slot_endpoint_requires_the_complete_identity_footprint(self):
        self.login()
        multi = Course.objects.create(
            organization=self.organization,
            course_name='Multi',
            abriviation='MULT',
            course_len=2,
        )
        schedule = self.create_schedule(
            'Multi Workflow Week',
            {
                'ags': ['Group 0'],
                'tue_am1': [multi.course_name],
                'tue_am2': [multi.course_name],
            },
        )
        result = run_instructor_assignment(schedule)
        identity = build_occurrence_identity(
            schedule,
            result['occurrences'][0],
        )
        url = reverse('instructor-override-set', args=[schedule.pk])
        payload = {
            'action': 'set',
            'schedule_id': schedule.pk,
            'expected_revision': 0,
            'instructor_id': self.second.pk,
            'occurrence_id': identity['occurrence_id'],
            'group_index': identity['group_index'],
            'activity_id': identity['activity_id'],
            'slot_footprint': identity['slot_footprint'][:1],
        }

        incomplete = self.client.post(
            url,
            payload,
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(incomplete.status_code, 409)
        self.assertEqual(
            incomplete.json()['code'],
            'stale_occurrence_identity',
        )

        complete = self.client.post(
            url,
            {**payload, 'slot_footprint': identity['slot_footprint']},
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(complete.status_code, 200)
        schedule.refresh_from_db()
        self.assertEqual(
            len(
                schedule.sched_data['manual_instructor_overrides'][0][
                    'occurrence'
                ]['slot_footprint']
            ),
            2,
        )

    def test_coverage_reduction_renders_explicit_confirmation_then_persists(self):
        self.login()
        schedule = self.coverage_reduction_schedule()
        page_url = reverse(
            'instructor-assignment-schedule',
            args=[schedule.pk],
        )
        set_url = reverse('instructor-override-set', args=[schedule.pk])
        page = self.client.get(page_url)
        fixed_choice = page.context['instructor_override_occurrences'][0]
        payload = {
            'action': 'set',
            'schedule_id': schedule.pk,
            'expected_revision': 0,
            'occurrence_token': fixed_choice['token'],
            'instructor_id': self.second.pk,
        }

        warning = self.client.post(set_url, payload)

        self.assertEqual(warning.status_code, 409)
        self.assertContains(
            warning,
            'Confirm reduced staffing coverage',
            status_code=409,
        )
        self.assertContains(
            warning,
            'name="confirm_coverage_reduction" value="1"',
            status_code=409,
            html=False,
        )
        confirmation = warning.context['instructor_override_confirmation']
        self.assertEqual(confirmation['coverage_before'], 3)
        self.assertEqual(confirmation['coverage_after'], 2)
        self.assertEqual(confirmation['coverage_delta'], -1)
        schedule.refresh_from_db()
        self.assertNotIn(
            'manual_instructor_overrides',
            schedule.sched_data,
        )

        return_anchor = warning.context['instructor_override_return_anchor']
        confirmed = self.client.post(set_url, {
            **payload,
            'expected_revision': confirmation['expected_revision'],
            'confirm_coverage_reduction': '1',
            'return_anchor': return_anchor,
        })

        self.assertRedirects(confirmed, f'{page_url}#{return_anchor}')
        schedule.refresh_from_db()
        self.assertTrue(
            schedule.sched_data['manual_instructor_overrides'][0][
                'confirmed_coverage_reduction'
            ]
        )

    def test_replacement_preserves_history_and_other_occurrence_can_be_added(self):
        self.login()
        first_payload = self.json_payload()
        first = self.client.post(
            self.set_url,
            first_payload,
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(first.status_code, 200)
        replacement = self.client.post(
            self.set_url,
            self.json_payload(
                expected_revision=1,
                instructor_id=self.first.pk,
            ),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(replacement.status_code, 200)
        self.schedule.refresh_from_db()
        records = self.schedule.sched_data['manual_instructor_overrides']
        self.assertEqual(
            [record['status'] for record in records],
            ['superseded', 'active'],
        )

        other = Course.objects.create(
            organization=self.organization,
            course_name='Climbing',
            abriviation='CLMB',
            course_len=1,
        )
        data = deepcopy(self.schedule.sched_data)
        data['generated_schedule']['mon_pm2'] = [other.course_name]
        self.schedule.sched_data = data
        self.schedule.save(update_fields=['sched_data'])
        other_identity = self.identity(index=1)
        second_active = self.client.post(
            self.set_url,
            {
                'action': 'set',
                'schedule_id': self.schedule.pk,
                'expected_revision': 2,
                'instructor_id': self.second.pk,
                'occurrence_id': other_identity['occurrence_id'],
                'group_index': other_identity['group_index'],
                'activity_id': other_identity['activity_id'],
                'slot_footprint': other_identity['slot_footprint'],
            },
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(second_active.status_code, 200)
        self.assertEqual(second_active.json()['code'], 'persisted')
        self.schedule.refresh_from_db()
        records = self.schedule.sched_data['manual_instructor_overrides']
        self.assertEqual(
            [record['status'] for record in records],
            ['superseded', 'active', 'active'],
        )

    def test_deleting_overridden_instructor_preserves_resettable_history(self):
        self.login()
        other_course = Course.objects.create(
            organization=self.organization,
            course_name='Climbing',
            abriviation='CLMB',
            course_len=1,
        )
        data = deepcopy(self.schedule.sched_data)
        data['generated_schedule']['mon_pm2'] = [other_course.course_name]
        data['manual_moves'] = [{'existing': 'activity move'}]
        self.schedule.sched_data = data
        self.schedule.save(update_fields=['sched_data'])

        stale_identity = self.identity(index=0)
        unrelated_identity = self.identity(index=1)
        for revision, identity, instructor in (
            (0, stale_identity, self.second),
            (1, unrelated_identity, self.first),
        ):
            response = self.client.post(
                self.set_url,
                {
                    'action': 'set',
                    'schedule_id': self.schedule.pk,
                    'expected_revision': revision,
                    'instructor_id': instructor.pk,
                    'occurrence_id': identity['occurrence_id'],
                    'group_index': identity['group_index'],
                    'activity_id': identity['activity_id'],
                    'slot_footprint': identity['slot_footprint'],
                },
                content_type='application/json',
                HTTP_ACCEPT='application/json',
            )
            self.assertEqual(response.status_code, 200)

        certification = Certification.objects.create(
            organization=self.organization,
            name='Delete Workflow Certification',
        )
        leadership_role = LeadershipRole.objects.create(
            organization=self.organization,
            name='Delete Workflow Leader',
        )
        InstructorCertification.objects.create(
            instructor=self.second,
            certification=certification,
        )
        InstructorLeadershipRole.objects.create(
            instructor=self.second,
            leadership_role=leadership_role,
        )
        participation = InstructorScheduleParticipation.objects.create(
            organization=self.organization,
            instructor=self.second,
            schedule=self.schedule,
            state=InstructorScheduleParticipation.PARTICIPATING,
        )
        availability = InstructorScheduleAvailability.objects.create(
            organization=self.organization,
            instructor=self.second,
            schedule=self.schedule,
            slot_key='tue_am1',
            state=InstructorScheduleAvailability.AVAILABLE,
        )
        foreign_instructor = self.create_instructor(
            'Foreign',
            'Unchanged',
            organization=self.other_organization,
        )
        deleted_instructor_id = self.second.pk
        deleted_instructor_name = str(self.second)
        self.schedule.refresh_from_db()
        generated_before = deepcopy(
            self.schedule.sched_data['generated_schedule']
        )
        manual_moves_before = deepcopy(
            self.schedule.sched_data['manual_moves']
        )

        deleted = self.client.post(
            reverse('instructor-delete', args=[deleted_instructor_id]),
            {'organization': self.other_organization.pk},
            follow=True,
        )

        self.assertRedirects(deleted, reverse('instructor-list'))
        self.assertContains(
            deleted,
            f'{deleted_instructor_name} was deleted successfully.',
        )
        self.assertFalse(
            Instructor.objects.filter(pk=deleted_instructor_id).exists()
        )
        self.assertFalse(
            InstructorScheduleParticipation.objects.filter(
                pk=participation.pk
            ).exists()
        )
        self.assertFalse(
            InstructorScheduleAvailability.objects.filter(
                pk=availability.pk
            ).exists()
        )
        self.assertFalse(
            InstructorCertification.objects.filter(
                instructor_id=deleted_instructor_id
            ).exists()
        )
        self.assertFalse(
            InstructorLeadershipRole.objects.filter(
                instructor_id=deleted_instructor_id
            ).exists()
        )
        self.assertTrue(
            Certification.objects.filter(pk=certification.pk).exists()
        )
        self.assertTrue(
            LeadershipRole.objects.filter(pk=leadership_role.pk).exists()
        )
        self.assertTrue(
            Instructor.objects.filter(pk=foreign_instructor.pk).exists()
        )

        self.schedule.refresh_from_db()
        set_records = [
            record
            for record in self.schedule.sched_data[
                'manual_instructor_overrides'
            ]
            if record['action'] == 'set'
        ]
        self.assertEqual(
            [record['instructor_id'] for record in set_records],
            [deleted_instructor_id, self.first.pk],
        )

        affected_page = self.client.get(self.page_url)

        self.assertEqual(affected_page.status_code, 200)
        self.assertContains(affected_page, 'missing_instructor')
        self.assertContains(
            affected_page,
            'The automatic assignment is currently shown',
        )
        self.assertContains(
            affected_page,
            'Return to automatic assignment',
        )
        self.assertNotContains(affected_page, deleted_instructor_name)
        assignment_schedule = affected_page.context['assignment_schedule']
        stale_automatic_cells = [
            cell
            for row in assignment_schedule.instructor_rows
            for cell in row.cells
            if (
                cell.occurrence_id == stale_identity['occurrence_id']
                and not cell.is_fixed
            )
        ]
        stale_unstaffed = [
            occurrence
            for occurrence in assignment_schedule.unstaffed_occurrences
            if occurrence.occurrence_id == stale_identity['occurrence_id']
        ]
        self.assertTrue(
            stale_automatic_cells or stale_unstaffed
        )
        applied_before_reset = affected_page.context[
            'instructor_override_reset_targets'
        ]
        stale_target = next(
            target
            for target in applied_before_reset
            if target['occurrence']['occurrence_id']
            == stale_identity['occurrence_id']
        )
        unrelated_target = next(
            target
            for target in applied_before_reset
            if target['occurrence']['occurrence_id']
            == unrelated_identity['occurrence_id']
        )
        self.assertFalse(stale_target['is_applied'])
        self.assertTrue(unrelated_target['is_applied'])

        reset = self.client.post(
            self.reset_url,
            {
                'schedule_id': self.schedule.pk,
                'expected_revision': 2,
                'occurrence_token': stale_target['token'],
            },
            follow=True,
        )

        self.assertRedirects(reset, self.page_url)
        self.assertContains(
            reset,
            'The manual instructor assignment was returned to automatic.',
        )
        self.assertNotContains(reset, 'missing_instructor')
        remaining_targets = reset.context[
            'instructor_override_reset_targets'
        ]
        self.assertEqual(len(remaining_targets), 1)
        self.assertEqual(
            remaining_targets[0]['occurrence']['occurrence_id'],
            unrelated_identity['occurrence_id'],
        )
        self.assertTrue(remaining_targets[0]['is_applied'])
        self.schedule.refresh_from_db()
        self.assertEqual(
            self.schedule.sched_data['generated_schedule'],
            generated_before,
        )
        self.assertEqual(
            self.schedule.sched_data['manual_moves'],
            manual_moves_before,
        )

    def test_stale_warning_keyboard_controls_and_no_drag_hooks(self):
        self.login()
        payload = self.html_payload()
        self.client.post(self.set_url, payload)
        self.schedule.refresh_from_db()
        data = deepcopy(self.schedule.sched_data)
        data['manual_instructor_overrides'][0]['occurrence']['activity_id'] = 999
        self.schedule.sched_data = data
        self.schedule.save(update_fields=['sched_data'])
        stored = deepcopy(data)

        with patch(
            'scheduler_app.views.run_instructor_assignment',
            wraps=run_instructor_assignment,
        ) as orchestration:
            response = self.client.get(self.page_url)

        orchestration.assert_called_once()
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'This saved manual assignment no longer applies',
        )
        self.assertContains(
            response,
            'for="instructor-override-occurrence"',
            html=False,
        )
        self.assertContains(
            response,
            'for="instructor-override-instructor"',
            html=False,
        )
        self.assertContains(
            response,
            'data-instructor-drag-source="true"',
            html=False,
        )
        self.assertNotContains(response, 'data-draggable-activity', html=False)
        self.assertNotContains(response, 'data-drop-target', html=False)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data, stored)

    def test_post_uses_service_result_without_rerunning_orchestration(self):
        self.login()
        payload = self.html_payload()
        with patch(
            'scheduler_app.views.persist_manual_instructor_override',
            return_value={
                'ok': True,
                'code': 'persisted',
                'new_revision': 1,
                'override': {},
                'planner_result': run_instructor_assignment(self.schedule),
            },
        ) as persistence, patch(
            'scheduler_app.views.run_instructor_assignment',
        ) as orchestration:
            response = self.client.post(
                self.set_url,
                payload,
            )

        self.assertEqual(response.status_code, 302)
        persistence.assert_called_once()
        orchestration.assert_not_called()

    def test_drag_sources_are_instructor_handles_not_assignment_cells(self):
        self.login()

        response = self.client.get(self.page_url)

        self.assertContains(
            response,
            'data-instructor-drag-source="true"',
            html=False,
        )
        self.assertContains(
            response,
            f'data-instructor-id="{self.first.pk}"',
            html=False,
        )
        self.assertContains(
            response,
            f'data-instructor-id="{self.second.pk}"',
            html=False,
        )
        self.assertContains(
            response,
            f'aria-label="Drag {self.first} to set a manual activity assignment"',
            html=False,
        )
        self.assertContains(
            response,
            f'>⠿</span> {self.first}</button>',
            html=False,
        )
        self.assertContains(
            response,
            f'>⠿</span> {self.second}</button>',
            html=False,
        )
        self.assertNotContains(response, '>Drag instructor</button>', html=False)
        self.assertContains(
            response,
            '<th scope="row" class="instructor-sticky-column">',
            count=2,
            html=False,
        )
        self.assertNotContains(response, 'data-draggable-activity', html=False)
        self.assertNotContains(
            response,
            '<td draggable="true"',
            html=False,
        )

    def test_only_operational_occurrences_receive_drop_target_identity(self):
        InstructorScheduleAvailability.objects.create(
            organization=self.organization,
            schedule=self.schedule,
            instructor=self.second,
            slot_key='mon_pm2',
            state=InstructorScheduleAvailability.UNAVAILABLE,
        )
        self.login()

        response = self.client.get(self.page_url)
        presentation = response.context['assignment_schedule']

        for row in presentation.instructor_rows:
            for cell in row.cells:
                if cell.state == 'assigned':
                    self.assertIsNotNone(cell.occurrence_token)
                else:
                    self.assertIsNone(cell.occurrence_token)
        self.assertContains(
            response,
            'data-instructor-occurrence-target="true"',
            html=False,
        )
        self.assertNotContains(
            response,
            'data-cell-state="off" data-instructor-occurrence-target',
            html=False,
        )
        self.assertNotContains(
            response,
            'data-cell-state="admin_time" data-instructor-occurrence-target',
            html=False,
        )
        self.assertNotContains(
            response,
            'data-cell-state="unavailable" data-instructor-occurrence-target',
            html=False,
        )

    def test_unstaffed_occurrence_is_one_server_generated_drop_target(self):
        second_course = Course.objects.create(
            organization=self.organization,
            course_name='Climbing',
            abriviation='CLMB',
            course_len=1,
        )
        data = deepcopy(self.schedule.sched_data)
        data['generated_schedule'] = {
            'ags': ['Group 0', 'Group 1'],
            'mon_pm1': [
                self.course.course_name,
                second_course.course_name,
            ],
        }
        self.schedule.sched_data = data
        self.schedule.save(update_fields=['sched_data'])
        Instructor.objects.filter(pk=self.second.pk).delete()
        self.login()

        response = self.client.get(self.page_url)

        unstaffed = response.context[
            'assignment_schedule'
        ].unstaffed_occurrences
        self.assertEqual(len(unstaffed), 1)
        self.assertIsNotNone(unstaffed[0].occurrence_token)
        self.assertContains(
            response,
            'aria-label="Set an instructor for unstaffed occurrence',
            html=False,
        )

    def test_multi_slot_cells_share_one_signed_complete_target_identity(self):
        multi = Course.objects.create(
            organization=self.organization,
            course_name='Multi Target',
            abriviation='MULT',
            course_len=2,
        )
        schedule = self.create_schedule(
            'Multi Drag Week',
            {
                'ags': ['Group 0'],
                'tue_am1': [multi.course_name],
                'tue_am2': [multi.course_name],
            },
        )
        self.login()

        response = self.client.get(reverse(
            'instructor-assignment-schedule',
            args=[schedule.pk],
        ))

        assigned_cells = [
            cell
            for row in response.context[
                'assignment_schedule'
            ].instructor_rows
            for cell in row.cells
            if cell.state == 'assigned'
        ]
        self.assertEqual(len(assigned_cells), 2)
        self.assertEqual(
            assigned_cells[0].occurrence_token,
            assigned_cells[1].occurrence_token,
        )
        identity = signing.loads(
            assigned_cells[0].occurrence_token,
            salt=INSTRUCTOR_OVERRIDE_SIGNING_SALT,
        )
        self.assertEqual(
            [slot['slot_key'] for slot in identity['slot_footprint']],
            ['tue_am1', 'tue_am2'],
        )
        self.assertEqual(
            assigned_cells[0].occurrence_id,
            assigned_cells[1].occurrence_id,
        )

    def test_drag_script_uses_existing_set_endpoint_and_server_truth_reload(self):
        self.login()

        response = self.client.get(self.page_url)

        self.assertContains(
            response,
            f'data-instructor-override-url="{self.set_url}"',
            html=False,
        )
        self.assertContains(
            response,
            'data-override-revision="0"',
            html=False,
        )
        self.assertContains(
            response,
            "action: 'set'",
            html=False,
        )
        self.assertContains(
            response,
            'expected_revision: expectedRevision',
            html=False,
        )
        self.assertContains(
            response,
            'instructor_id: dragState.instructorId',
            html=False,
        )
        self.assertContains(
            response,
            'occurrence_token: target.dataset.occurrenceToken',
            html=False,
        )
        self.assertNotContains(response, 'organization_id:', html=False)
        self.assertNotContains(response, "action: 'move'", html=False)
        self.assertNotContains(response, "action: 'swap'", html=False)
        self.assertNotContains(
            response,
            "action: 'displacement_move'",
            html=False,
        )
        self.assertContains(response, 'window.location.reload()', html=False)
        self.assertContains(
            response,
            'window.location.hash = submissionContext.returnAnchor',
            html=False,
        )
        self.assertNotContains(response, 'innerHTML', html=False)

    def test_drag_feedback_confirmation_and_submission_guards_are_accessible(self):
        self.login()

        response = self.client.get(self.page_url)

        self.assertContains(response, 'aria-live="polite"', html=False)
        self.assertContains(
            response,
            'create or change a manual assignment',
        )
        self.assertContains(
            response,
            'invalid assignments are rejected',
        )
        self.assertContains(
            response,
            'schedule reloads at that occurrence',
        )
        self.assertContains(
            response,
            '`Assign ${dragState.instructorName} to ${target.dataset.occurrenceLabel}.',
            html=False,
        )
        self.assertContains(
            response,
            'The assignment will be validated before saving.',
        )
        self.assertContains(
            response,
            'id="instructor-drag-confirm"',
            html=False,
        )
        self.assertContains(
            response,
            'id="instructor-drag-cancel"',
            html=False,
        )
        self.assertContains(response, "event.key === 'Escape'", html=False)
        self.assertContains(response, 'if (requestInFlight)', html=False)
        self.assertContains(response, 'requestInFlight = true', html=False)
        self.assertContains(
            response,
            'coverage_confirmation_required',
            html=False,
        )
        self.assertContains(response, 'revision_conflict', html=False)
        self.assertContains(
            response,
            'stale_occurrence_identity',
            html=False,
        )
        self.assertContains(
            response,
            'unsupported_multiple_active_overrides',
            html=False,
        )
        self.assertContains(
            response,
            'Reload current assignments',
            html=False,
        )

    def test_reset_endpoints_require_login_and_post(self):
        self.assertEqual(self.client.get(self.reset_url).status_code, 302)
        self.assertEqual(self.client.get(self.reset_all_url).status_code, 302)
        self.login()
        self.assertEqual(self.client.get(self.reset_url).status_code, 405)
        self.assertEqual(self.client.get(self.reset_all_url).status_code, 405)

    def test_html_reset_one_uses_prg_and_removes_page_controls(self):
        self.login()
        self.client.post(self.set_url, self.html_payload())
        page = self.client.get(self.page_url)
        target = page.context['instructor_override_reset_targets'][0]
        self.assertContains(page, 'Return to automatic assignment')
        self.assertContains(page, 'Reset all instructor assignments')

        response = self.client.post(self.reset_url, {
            'schedule_id': self.schedule.pk,
            'expected_revision': 1,
            'occurrence_token': target['token'],
        })

        self.assertEqual(response.status_code, 302)
        refreshed = self.client.get(self.page_url)
        self.assertNotContains(refreshed, 'Return to automatic assignment')
        self.assertNotContains(refreshed, 'Reset all instructor assignments')
        self.assertNotContains(refreshed, '>Manual</span>', html=False)

    def test_json_reset_one_returns_safe_summary(self):
        self.login()
        self.client.post(self.set_url, self.html_payload())
        page = self.client.get(self.page_url)
        token = page.context['instructor_override_reset_targets'][0]['token']

        response = self.client.post(
            self.reset_url,
            {
                'schedule_id': self.schedule.pk,
                'expected_revision': 1,
                'occurrence_token': token,
            },
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['code'], 'reset')
        self.assertEqual(body['old_revision'], 1)
        self.assertEqual(body['new_revision'], 2)
        self.assertEqual(body['active_override_count_after'], 0)
        self.assertIn('coverage', body)
        self.assertNotIn('manual_instructor_overrides', body)

    def test_reset_all_requires_explicit_confirmation_then_redirects(self):
        self.login()
        self.client.post(self.set_url, self.html_payload())

        missing = self.client.post(
            self.reset_all_url,
            {
                'schedule_id': self.schedule.pk,
                'expected_revision': 1,
            },
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(
            missing.json()['code'],
            'reset_confirmation_required',
        )

        confirmed = self.client.post(self.reset_all_url, {
            'schedule_id': self.schedule.pk,
            'expected_revision': 1,
            'confirm_reset_all': '1',
        })
        self.assertEqual(confirmed.status_code, 302)
        self.schedule.refresh_from_db()
        self.assertEqual(
            self.schedule.sched_data['instructor_override_revision'],
            2,
        )

    def test_reset_endpoints_keep_organization_scope(self):
        foreign = self.create_schedule(
            'Foreign',
            {'ags': ['Group'], 'mon_pm1': [self.course.course_name]},
            organization=self.other_organization,
        )
        self.login()

        self.assertEqual(
            self.client.post(
                reverse('instructor-override-reset', args=[foreign.pk]),
                {},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse('instructor-override-reset-all', args=[foreign.pk]),
                {},
            ).status_code,
            404,
        )

    def test_invalid_return_anchor_falls_back_without_open_redirect(self):
        self.login()
        payload = self.html_payload()
        payload['return_anchor'] = 'https://example.invalid/escape'

        response = self.client.post(self.set_url, payload)

        self.assertRedirects(response, self.page_url)
        self.assertNotIn('example.invalid', response['Location'])

    def test_rejected_assignment_returns_to_occurrence_with_actionable_message(self):
        InstructorScheduleAvailability.objects.create(
            organization=self.organization,
            instructor=self.second,
            schedule=self.schedule,
            slot_key='mon_pm1',
            state=InstructorScheduleAvailability.UNAVAILABLE,
        )
        self.login()
        page = self.client.get(self.page_url)
        occurrence = page.context['instructor_override_occurrences'][0]

        response = self.client.post(self.set_url, {
            'action': 'set',
            'schedule_id': self.schedule.pk,
            'expected_revision': 0,
            'occurrence_token': occurrence['token'],
            'instructor_id': self.second.pk,
            'return_anchor': occurrence['anchor'],
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            f'{self.page_url}#{occurrence["anchor"]}',
        )
        followed = self.client.get(response['Location'])
        self.assertContains(followed, 'unavailable during Monday PM1')
        self.assertContains(followed, 'Choose another instructor')

    def test_reset_redirects_use_occurrence_and_summary_targets(self):
        self.login()
        set_response = self.client.post(self.set_url, self.html_payload())
        self.assertEqual(set_response.status_code, 302)
        page = self.client.get(self.page_url)
        target = page.context['instructor_override_reset_targets'][0]

        reset = self.client.post(self.reset_url, {
            'schedule_id': self.schedule.pk,
            'expected_revision': 1,
            'occurrence_token': target['token'],
            'return_anchor': target['return_anchor'],
        })
        self.assertRedirects(
            reset,
            f'{self.page_url}#{target["return_anchor"]}',
        )

        self.client.post(self.set_url, self.html_payload())
        reset_all = self.client.post(self.reset_all_url, {
            'schedule_id': self.schedule.pk,
            'expected_revision': 3,
            'confirm_reset_all': '1',
            'return_anchor': 'staffing-summary',
        })
        self.assertRedirects(
            reset_all,
            f'{self.page_url}#staffing-summary',
        )

    def test_repeated_html_corrections_keep_each_occurrence_target(self):
        other = Course.objects.create(
            organization=self.organization,
            course_name='Climbing',
            abriviation='CLMB',
            course_len=1,
        )
        data = deepcopy(self.schedule.sched_data)
        data['generated_schedule']['mon_pm2'] = [other.course_name]
        self.schedule.sched_data = data
        self.schedule.save(update_fields=['sched_data'])
        self.login()

        for expected_revision, choice_index, instructor in (
            (0, 0, self.second),
            (1, 1, self.first),
            (2, 0, self.first),
        ):
            page = self.client.get(self.page_url)
            choice = page.context['instructor_override_occurrences'][choice_index]
            response = self.client.post(self.set_url, {
                'action': 'set',
                'schedule_id': self.schedule.pk,
                'expected_revision': expected_revision,
                'occurrence_token': choice['token'],
                'instructor_id': instructor.pk,
                'return_anchor': choice['anchor'],
            })
            self.assertRedirects(
                response,
                f'{self.page_url}#{choice["anchor"]}',
            )

        self.schedule.refresh_from_db()
        active = [
            record
            for record in self.schedule.sched_data['manual_instructor_overrides']
            if record.get('status') == 'active'
        ]
        self.assertEqual(len(active), 2)
