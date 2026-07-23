from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from members.models import Organization, OrganizationMembership

from .instructor_availability import apply_instructor_participation_changes
from .models import (
    Instructor,
    InstructorScheduleAvailability,
    InstructorScheduleParticipation,
    TheSched,
)


class ParticipationUiTestMixin:
    def create_instructor(self, first, last='Instructor', organization=None):
        return Instructor.objects.create(
            organization=organization or self.organization,
            fname=first,
            lname=last,
            ropes_lead=False,
            school_lead=False,
            cpr=True,
            firstaid='yes',
        )

    def change(self, instructor, state):
        return {'instructor_id': instructor.pk, 'state': state}


class InstructorParticipationChangeServiceTests(ParticipationUiTestMixin, TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Participation Change Org')
        self.other_organization = Organization.objects.create(name='Foreign Change Org')
        self.schedule = TheSched.objects.create(
            organization=self.organization,
            sched_name='Participation Change Week',
            sched_data={},
        )
        self.foreign_schedule = TheSched.objects.create(
            organization=self.other_organization,
            sched_name='Foreign Participation Week',
            sched_data={},
        )
        self.first = self.create_instructor('Avery')
        self.second = self.create_instructor('Blake')
        self.foreign = self.create_instructor('Foreign', organization=self.other_organization)

    def apply(self, first_state='participating', second_state='participating'):
        return apply_instructor_participation_changes(
            self.organization,
            self.schedule,
            [self.change(self.first, first_state), self.change(self.second, second_state)],
        )

    def test_participating_uses_default_and_not_participating_creates_record(self):
        result = self.apply('participating', 'not_participating')

        self.assertEqual(result.created, 1)
        self.assertFalse(
            InstructorScheduleParticipation.objects.filter(instructor=self.first).exists()
        )
        self.assertEqual(
            InstructorScheduleParticipation.objects.get(instructor=self.second).state,
            'not_participating',
        )

    def test_state_change_updates_and_noop_remains_unchanged(self):
        self.apply('participating', 'participating')

        updated = self.apply('not_participating', 'participating')
        unchanged = self.apply('not_participating', 'participating')

        self.assertEqual(updated.created, 1)
        self.assertEqual(unchanged.unchanged, 2)
        self.assertEqual(
            InstructorScheduleParticipation.objects.get(instructor=self.first).state,
            'not_participating',
        )

    def test_participating_deletes_existing_opt_out(self):
        self.apply('not_participating', 'participating')

        result = self.apply('participating', 'participating')

        self.assertEqual(result.deleted, 1)
        self.assertFalse(InstructorScheduleParticipation.objects.exists())

    def test_multiple_changes_are_atomic_when_one_state_is_invalid(self):
        with self.assertRaises(ValidationError):
            self.apply('participating', 'partial')

        self.assertFalse(InstructorScheduleParticipation.objects.exists())

    def test_missing_expected_instructor_rejects_complete_change(self):
        with self.assertRaises(ValidationError):
            apply_instructor_participation_changes(
                self.organization,
                self.schedule,
                [self.change(self.first, 'participating')],
            )

        self.assertFalse(InstructorScheduleParticipation.objects.exists())

    def test_unexpected_or_foreign_instructor_rejects_complete_change(self):
        with self.assertRaises(ValidationError):
            apply_instructor_participation_changes(
                self.organization,
                self.schedule,
                [
                    self.change(self.first, 'participating'),
                    self.change(self.second, 'participating'),
                    self.change(self.foreign, 'participating'),
                ],
            )

        self.assertFalse(InstructorScheduleParticipation.objects.exists())

    def test_foreign_schedule_is_rejected(self):
        with self.assertRaises(ValidationError):
            apply_instructor_participation_changes(
                self.organization,
                self.foreign_schedule,
                [
                    self.change(self.first, 'participating'),
                    self.change(self.second, 'participating'),
                ],
            )

    def test_slot_availability_is_untouched(self):
        availability = InstructorScheduleAvailability.objects.create(
            organization=self.organization,
            instructor=self.first,
            schedule=self.schedule,
            slot_key='mon_pm1',
            state='unavailable',
        )

        self.apply('participating', 'not_participating')

        availability.refresh_from_db()
        self.assertEqual(availability.state, 'unavailable')
        self.assertEqual(InstructorScheduleAvailability.objects.count(), 1)


@override_settings(ALLOWED_HOSTS=['testserver'])
class InstructorParticipationViewTests(ParticipationUiTestMixin, TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Participation View Org')
        self.other_organization = Organization.objects.create(name='Foreign View Org')
        self.user = get_user_model().objects.create_user(
            username='participation-operator', password='password'
        )
        OrganizationMembership.objects.create(
            user=self.user,
            organization=self.organization,
        )
        self.schedule = TheSched.objects.create(
            organization=self.organization,
            sched_name='Operator Participation Week',
            sched_data={},
        )
        self.foreign_schedule = TheSched.objects.create(
            organization=self.other_organization,
            sched_name='Foreign Operator Week',
            sched_data={},
        )
        self.first = self.create_instructor('Zoe', 'Alpha')
        self.second = self.create_instructor('Avery', 'Beta')
        self.foreign = self.create_instructor('Foreign', organization=self.other_organization)
        self.url = reverse('instructor-participation', args=[self.schedule.pk])

    def login(self):
        self.client.force_login(self.user)

    def full_post(self, first='participating', second='participating', extra=None):
        data = {
            f'participation_{self.first.pk}': first,
            f'participation_{self.second.pk}': second,
        }
        data.update(extra or {})
        return data

    def test_authentication_and_foreign_schedule_isolation(self):
        anonymous = self.client.get(self.url)
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn(reverse('login'), anonymous['Location'])

        self.login()
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(
            self.client.get(reverse(
                'instructor-participation', args=[self.foreign_schedule.pk]
            )).status_code,
            404,
        )

    def test_get_lists_owned_instructors_and_current_opt_out_states(self):
        InstructorScheduleParticipation.objects.create(
            organization=self.organization,
            instructor=self.first,
            schedule=self.schedule,
            state='participating',
        )
        InstructorScheduleParticipation.objects.create(
            organization=self.organization,
            instructor=self.second,
            schedule=self.schedule,
            state='not_participating',
        )
        self.login()

        response = self.client.get(self.url)
        content = response.content.decode()

        self.assertContains(response, 'Instructor Participation')
        self.assertContains(response, self.schedule.sched_name)
        self.assertContains(response, str(self.first))
        self.assertContains(response, str(self.second))
        self.assertNotContains(response, str(self.foreign))
        self.assertIn('<option value="participating" selected>', content)
        self.assertIn('<option value="not_participating" selected>', content)
        self.assertNotContains(response, 'Undecided')

    def test_page_has_product_explanation_links_and_no_slot_controls(self):
        self.login()
        response = self.client.get(self.url)

        self.assertContains(response, 'participate in this schedule’s staffing pool by default')
        self.assertContains(response, 'Detailed availability exceptions')
        self.assertContains(
            response, reverse('instructor-availability', args=[self.schedule.pk])
        )
        self.assertContains(response, reverse('sched-detail', args=[self.schedule.pk]))
        self.assertNotContains(response, 'availability_')
        self.assertNotContains(response, 'mon_pm1')

    def test_valid_post_saves_redirects_and_displays_success(self):
        self.login()

        response = self.client.post(
            self.url,
            self.full_post('participating', 'not_participating'),
            follow=True,
        )

        self.assertRedirects(response, self.url)
        self.assertContains(response, 'Instructor participation saved successfully')
        self.assertEqual(
            list(InstructorScheduleParticipation.objects.values_list('state', flat=True)),
            ['not_participating'],
        )

    def test_participating_post_deletes_existing_record(self):
        record = InstructorScheduleParticipation.objects.create(
            organization=self.organization,
            instructor=self.first,
            schedule=self.schedule,
            state='participating',
        )
        self.login()

        response = self.client.post(self.url, self.full_post('participating'))

        self.assertRedirects(response, self.url)
        self.assertFalse(
            InstructorScheduleParticipation.objects.filter(pk=record.pk).exists()
        )

    def test_invalid_missing_unexpected_and_foreign_fields_are_atomic(self):
        existing = InstructorScheduleParticipation.objects.create(
            organization=self.organization,
            instructor=self.first,
            schedule=self.schedule,
            state='not_participating',
        )
        self.login()
        invalid_posts = [
            self.full_post('participating', 'partial'),
            {f'participation_{self.first.pk}': 'participating'},
            self.full_post(extra={'participation_999999': 'participating'}),
            self.full_post(extra={f'participation_{self.foreign.pk}': 'participating'}),
        ]

        for data in invalid_posts:
            with self.subTest(data=data):
                response = self.client.post(self.url, data)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Instructor participation was not saved')
                existing.refresh_from_db()
                self.assertEqual(existing.state, 'not_participating')
                self.assertEqual(InstructorScheduleParticipation.objects.count(), 1)

    def test_navigation_and_detailed_availability_wording(self):
        self.login()

        schedule_detail = self.client.get(
            reverse('sched-detail', args=[self.schedule.pk])
        )
        detailed = self.client.get(
            reverse('instructor-availability', args=[self.schedule.pk])
        )

        self.assertContains(schedule_detail, 'Manage Instructor Participation')
        self.assertContains(schedule_detail, 'Manage Detailed Availability Exceptions')
        self.assertContains(schedule_detail, self.url)
        self.assertContains(detailed, 'available across this schedule by default')
        self.assertContains(detailed, 'unavailable exceptions')
        self.assertContains(detailed, 'Staffing-pool participation is managed separately')
        self.assertContains(detailed, self.url)

    def test_participation_post_does_not_change_slot_availability(self):
        availability = InstructorScheduleAvailability.objects.create(
            organization=self.organization,
            instructor=self.first,
            schedule=self.schedule,
            slot_key='mon_pm1',
            state='unavailable',
        )
        self.login()

        self.client.post(self.url, self.full_post('participating', 'participating'))

        availability.refresh_from_db()
        self.assertEqual(availability.state, 'unavailable')
        self.assertEqual(InstructorScheduleAvailability.objects.count(), 1)
