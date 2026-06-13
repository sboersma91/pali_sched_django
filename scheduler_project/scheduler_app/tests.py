import csv
from io import StringIO
from unittest.mock import PropertyMock, patch

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from . import models, school_accounting, views
from .forms import SchoolsForm, SchedForm, suggest_activity_group_count
from .views import SchedDetail, SchedList, schedule_csv_export
from .school_accounting import calculate_school_slot_accounting, school_slot_accounting_summary
from .schedule_blocks import (
    DAY_OFFSETS,
    SCHEDULE_DAYS,
    SCHEDULE_LEGEND,
    SCHEDULE_SLOT_BLOCKS,
    SCHEDULE_SLOT_KEYS,
    WEEKDAY_CHOICES,
)
from .models import (
    Course,
    Locations,
    Schools,
    TheSched,
    class_len,
    class_locs,
    initialize_scheduling_data,
    master_locs,
)


class CanonicalScheduleBlockDefinitionTests(TestCase):
    def test_canonical_schedule_order_and_labels_remain_unchanged(self):
        self.assertEqual(
            [(day["name"], [(slot["label"], slot["key"]) for slot in day["slots"]]) for day in SCHEDULE_DAYS],
            [
                ("Monday", [("PM1", "mon_pm1"), ("PM2", "mon_pm2"), ("Night", "mon_night")]),
                ("Tuesday", [("AM1", "tue_am1"), ("AM2", "tue_am2"), ("PM1", "tue_pm1"), ("PM2", "tue_pm2"), ("Night", "tue_night")]),
                ("Wednesday", [("AM1", "wed_am1"), ("AM2", "wed_am2"), ("PM1", "wed_pm1"), ("PM2", "wed_pm2"), ("Night", "wed_night")]),
                ("Thursday", [("AM1", "thur_am1"), ("AM2", "thur_am2"), ("PM1", "thur_pm1"), ("PM2", "thur_pm2"), ("Night", "thur_night")]),
                ("Friday", [("AM1", "fri_am1"), ("AM2", "fri_am2")]),
            ],
        )
        self.assertEqual(DAY_OFFSETS, {"Mon": 0, "Tue": 5, "Wed": 10, "Thur": 15, "Fri": 19, "Tues": 5, "Thurs": 15})

    def test_active_workflows_consume_canonical_definitions(self):
        self.assertIs(views.SCHEDULE_DAYS, SCHEDULE_DAYS)
        self.assertIs(views.SCHEDULE_LEGEND, SCHEDULE_LEGEND)
        self.assertIs(school_accounting.SCHEDULE_SLOT_BLOCKS, SCHEDULE_SLOT_BLOCKS)
        self.assertIs(models.SCHEDULE_SLOT_KEYS, SCHEDULE_SLOT_KEYS)
        self.assertIs(models.WEEKDAY_CHOICES, WEEKDAY_CHOICES)

    def test_school_accounting_keeps_established_trip_window_semantics(self):
        daytime = Course(course_name="Daytime", course_len=2)
        one_block = Course(course_name="One Block", course_len=1)
        night = Course(course_name="Night", course_len=0)

        summary = calculate_school_slot_accounting("Thur", "Fri", [daytime, one_block, night])

        self.assertEqual(summary["required_daytime"], 3)
        self.assertEqual(summary["required_night"], 1)
        self.assertEqual(summary["selected_total"], summary["required_total"])
        self.assertEqual(summary["total_status"], "balanced")


@override_settings(ALLOWED_HOSTS=["localhost", "testserver"])
class PublicLandingPageTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST="localhost")

    def test_public_home_renders_product_heading_and_workflow(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create clear operational activity schedules")
        self.assertContains(
            response,
            "Configure Locations, Activities, and School visits, then generate a readable weekly Schedule for each activity group.",
        )
        self.assertContains(response, "How FlowLine Works")
        self.assertContains(response, "Review the operational output")
        self.assertContains(response, "activity group, weekday, and time block")
        self.assertNotContains(response, "Hello and Welcome to the Scheduler app!")

    def test_public_home_renders_four_workflow_cards_without_crud_links(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        for workflow in ("Locations", "Activities", "Schools", "Schedules"):
            with self.subTest(workflow=workflow):
                self.assertContains(response, f'<h3 class="h5 card-title">{workflow}</h3>', html=True)
        for operational_route in (
            "location-list",
            "add-loc",
            "course-list",
            "course-create",
            "school-list",
            "school-create",
            "sched-list",
            "sched-create",
        ):
            with self.subTest(route=operational_route):
                self.assertNotContains(response, f'href="{reverse(operational_route)}"', html=False)

    def test_public_home_shows_login_and_create_account_for_anonymous_user(self):
        response = self.client.get(reverse("home"))

        self.assertContains(response, f'<a href="{reverse("login")}" class="btn btn-primary btn-lg">Log In</a>', html=True)
        self.assertContains(response, f'<a href="{reverse("register_user")}" class="btn btn-outline-primary btn-lg">Create Account</a>', html=True)
        self.assertNotContains(response, "Open Operational Dashboard")

    def test_public_home_shows_dashboard_action_for_authenticated_user(self):
        user = get_user_model().objects.create_user(username="operator", password="password")
        self.client.force_login(user)

        response = self.client.get(reverse("home"))

        self.assertContains(response, f'<a href="{reverse("home-paid")}" class="btn btn-primary btn-lg">Open Operational Dashboard</a>', html=True)
        self.assertNotContains(response, f'<a href="{reverse("login")}" class="btn btn-primary btn-lg">Log In</a>', html=True)
        self.assertNotContains(response, "Create Account")



@override_settings(ALLOWED_HOSTS=["localhost", "testserver"])
class PublicShellPresentationTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST="localhost")

    def test_public_navbar_renders_clean_anonymous_navigation(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'<a class="navbar-brand" href="{reverse("home")}">FlowLine</a>', html=True)
        self.assertContains(response, f'<a class="nav-link" href="{reverse("login")}">Log In</a>', html=True)
        self.assertContains(response, f'<a class="nav-link" href="{reverse("register_user")}">Create Account</a>', html=True)
        for removed_content in ("Plans", "Contact", "Payed", "Search Venues"):
            with self.subTest(content=removed_content):
                self.assertNotContains(response, removed_content)
        self.assertNotContains(response, 'href="#"', html=False)
        self.assertNotContains(response, 'type="search"', html=False)

    def test_public_navbar_renders_authenticated_navigation(self):
        user = get_user_model().objects.create_user(username="operator", password="password")
        self.client.force_login(user)

        response = self.client.get(reverse("home"))

        self.assertContains(response, f'<a class="nav-link" href="{reverse("home-paid")}">Open Dashboard</a>', html=True)
        self.assertContains(response, f'<a class="nav-link" href="{reverse("logout")}">Log Out</a>', html=True)
        self.assertNotContains(response, f'<a class="nav-link" href="{reverse("login")}">Log In</a>', html=True)
        self.assertNotContains(response, f'<a class="nav-link" href="{reverse("register_user")}">Create Account</a>', html=True)

    def test_public_base_renders_default_and_page_specific_titles(self):
        home_response = self.client.get(reverse("home"))
        login_response = self.client.get(reverse("login"))
        register_response = self.client.get(reverse("register_user"))

        self.assertContains(home_response, "<title>FlowLine</title>", html=True)
        self.assertContains(login_response, "<title>Log In | FlowLine</title>", html=True)
        self.assertContains(register_response, "<title>Create Account | FlowLine</title>", html=True)
        self.assertNotContains(home_response, "Hello, world!")

    def test_login_page_renders_improved_presentation_and_existing_form_action(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h1 class="h3 mb-2">Log In</h1>', html=True)
        self.assertContains(response, "Access the operational dashboard to prepare and review schedules.")
        self.assertContains(response, f'<form action="{reverse("login")}" method="post">', html=False)
        self.assertContains(response, '<label for="username" class="form-label">Username</label>', html=True)
        self.assertContains(response, '<button type="submit" class="btn btn-primary w-100">Log In</button>', html=True)
        self.assertNotContains(response, ">Submit</button>", html=False)
        self.assertNotContains(response, "Email address")

    def test_registration_page_renders_improved_presentation_and_existing_form_action(self):
        response = self.client.get(reverse("register_user"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h1 class="h3 mb-2">Create Account</h1>', html=True)
        self.assertContains(response, "Create an account to access the FlowLine operational tools.")
        self.assertContains(response, f'<form action="{reverse("register_user")}" method="post">', html=False)
        self.assertContains(response, '<button type="submit" class="btn btn-primary">Create Account</button>', html=True)
        self.assertContains(response, "Back to Log In")
        self.assertNotContains(response, "User Registratioon")
        self.assertNotContains(response, "Something went wrong??")



@override_settings(ALLOWED_HOSTS=["localhost", "testserver"])
class PresentationTerminologyTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST="localhost")

    def test_public_and_operational_navigation_use_flowline_branding(self):
        for route in ("home", "home-paid"):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertContains(response, ">FlowLine</a>", html=False)
                self.assertNotContains(response, "Pali Scheduler")

    def test_activity_pages_use_activity_terminology(self):
        for route in ("course-list", "course-create", "add-course"):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertContains(response, "Activity")
                self.assertNotContains(response, "Course Name")
                self.assertNotContains(response, "Add Course")
                self.assertNotContains(response, ">Courses<", html=False)


@override_settings(ALLOWED_HOSTS=["localhost", "testserver"])
class OperationalNavigationTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST="localhost")

    def test_operational_navbar_renders_canonical_links(self):
        response = self.client.get(reverse("home-paid"))

        self.assertEqual(response.status_code, 200)
        expected_links = {
            "FlowLine": reverse("home-paid"),
            "Dashboard": reverse("home-paid"),
            "Schedules": reverse("sched-list"),
            "Schools": reverse("school-list"),
            "Activities": reverse("course-list"),
            "Locations": reverse("location-list"),
        }
        for label, url in expected_links.items():
            with self.subTest(label=label):
                self.assertContains(response, f'href="{url}">{label}</a>', html=False)

    def test_operational_navbar_omits_placeholders_and_legacy_add_links(self):
        response = self.client.get(reverse("home-paid"))

        self.assertEqual(response.status_code, 200)
        for removed_label in ("Forms_Add", "Crud", "og_home", "Add Instructor"):
            with self.subTest(label=removed_label):
                self.assertNotContains(response, removed_label)
        for legacy_url in (
            reverse("add-location"),
            reverse("add-course"),
            reverse("add-school"),
            reverse("add-instructor"),
            reverse("search-results"),
        ):
            with self.subTest(url=legacy_url):
                self.assertNotContains(response, f'href="{legacy_url}"', html=False)
        self.assertNotContains(response, ">Link</a>", html=False)
        self.assertNotContains(response, 'type="search"', html=False)

    def test_operational_navbar_shows_log_in_for_anonymous_user(self):
        response = self.client.get(reverse("home-paid"))

        self.assertContains(response, f'href="{reverse("login")}">Log In</a>', html=False)
        self.assertNotContains(response, "Log Out")

    def test_operational_navbar_shows_log_out_for_authenticated_user(self):
        user = get_user_model().objects.create_user(username="operator", password="password")
        self.client.force_login(user)

        response = self.client.get(reverse("home-paid"))

        self.assertContains(response, f'href="{reverse("logout")}">Log Out</a>', html=False)
        self.assertNotContains(response, "Log In")


@override_settings(ALLOWED_HOSTS=["localhost", "testserver"])
class OperationalDashboardTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST="localhost")

    def test_dashboard_replaces_placeholder_with_operational_orientation(self):
        response = self.client.get(reverse("home-paid"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Operational Dashboard")
        self.assertContains(
            response,
            "Create schedules and maintain the Schools, Activities, and Locations used to generate them.",
        )
        self.assertContains(response, "Prepare a Schedule")
        self.assertNotContains(response, "this is the home page of a person who has paid.")

    def test_dashboard_renders_workflow_cards(self):
        response = self.client.get(reverse("home-paid"))

        self.assertEqual(response.status_code, 200)
        for workflow in ("Locations", "Activities", "Schools", "Schedules"):
            with self.subTest(workflow=workflow):
                self.assertContains(response, f'<h3 class="h5 card-title">{workflow}</h3>', html=True)
        self.assertContains(response, 'class="card h-100"', count=3, html=False)
        self.assertContains(response, 'class="card h-100 border-primary"', count=1, html=False)

    def test_dashboard_renders_primary_schedule_actions_with_canonical_routes(self):
        response = self.client.get(reverse("home-paid"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'<a href="{reverse("sched-create")}" class="btn btn-primary">Create Schedule</a>', html=True)
        self.assertContains(response, f'<a href="{reverse("sched-list")}" class="btn btn-outline-primary">View Schedules</a>', html=True)

    def test_dashboard_workflow_actions_use_canonical_routes(self):
        response = self.client.get(reverse("home-paid"))

        self.assertEqual(response.status_code, 200)
        expected_actions = (
            ("location-list", "View Locations"),
            ("add-loc", "Add Location"),
            ("course-list", "View Activities"),
            ("course-create", "Add Activity"),
            ("school-list", "View Schools"),
            ("school-create", "Add School"),
            ("sched-list", "View Schedules"),
            ("sched-create", "Create Schedule"),
        )
        for route, label in expected_actions:
            with self.subTest(route=route):
                self.assertContains(response, f'href="{reverse(route)}"', html=False)
                self.assertContains(response, label)



@override_settings(ALLOWED_HOSTS=["localhost", "testserver"])
class ScheduleWorkflowTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST="localhost")
        self.school = Schools.schools_list.create(
            school_name="Existing Schedule School",
            arrive="Thur",
            depart="Fri",
            total_students=16,
            ag_num=1,
            attending_year="2026-06-04",
        )
        self.schedule = TheSched.objects.create(
            sched_name="Existing Operational Schedule",
            sched_data={"source": "test"},
        )
        self.schedule.schools.add(self.school)

    def test_schedule_list_renders_readable_operational_summary(self):
        response = self.client.get(reverse("sched-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="table table-striped table-hover align-middle"', html=False)
        self.assertContains(response, "Schedule Name")
        self.assertContains(response, "Created")
        self.assertContains(response, "Selected Schools")
        self.assertContains(response, "Existing Operational Schedule")
        self.assertContains(response, "1 School")
        self.assertContains(
            response,
            "Opening a Schedule regenerates output for its selected Schools",
        )
        self.assertContains(response, "Create Schedule")
        self.assertContains(response, "Generate / View")
        self.assertContains(response, "Edit Record")
        self.assertContains(response, "Delete")
        self.assertNotContains(response, "Add New Course")

    def test_schedule_list_shows_selected_school_count(self):
        request = RequestFactory().get(reverse("sched-list"))
        response = SchedList.as_view()(request)
        response.render()
        rendered_content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Selected Schools", rendered_content)
        self.assertIn("1 School", rendered_content)

    def test_schedule_detail_renders_metadata_actions_and_readable_schedule_table(self):
        generated_schedule = {
            "ags": ["Example School 0"],
            "mon_pm1": ["Archery"],
        }
        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.get(reverse("sched-detail", args=[self.schedule.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Generated Schedule", count=2)
        self.assertContains(response, "Schedule Name")
        self.assertContains(response, "Created")
        self.assertContains(response, "Selected Schools")
        self.assertContains(response, "Existing Schedule School")
        self.assertContains(response, "Edit Schedule Record")
        self.assertContains(response, "Delete Schedule")
        self.assertContains(response, "Back to Schedules")
        self.assertContains(response, 'class="table table-bordered table-sm align-middle text-center schedule-table"', html=False)
        self.assertContains(response, "Activity Group")
        self.assertContains(response, "Monday")
        self.assertContains(response, "Example School 0")
        self.assertContains(response, "Archery")
        self.assertContains(response, "How this Schedule record works")
        self.assertContains(response, "Viewing this page regenerates the current output")
        self.assertContains(response, "/////</code> = unavailable or not present", html=False)
        self.assertContains(response, "****</code> = unassigned available block", html=False)

    def test_schedule_create_and_update_render_dedicated_form_layout(self):
        for route, args, heading in (
            ("sched-create", [], "Create Schedule"),
            ("sched-update", [self.schedule.id], "Edit Schedule"),
        ):
            with self.subTest(route=route):
                response = self.client.get(reverse(route, args=args))

                self.assertEqual(response.status_code, 200)
                self.assertIsInstance(response.context["form"], SchedForm)
                self.assertContains(response, heading)
                self.assertContains(response, "Schedule Name")
                self.assertContains(response, "Schools to Schedule")
                self.assertContains(response, "Use a clear name")
                self.assertContains(response, "Select the Schools that should be generated together")
                self.assertContains(response, "selected Schools and their current Activities and Locations")
                self.assertContains(response, "Prepare before generating")
                self.assertContains(response, "Review Locations")
                self.assertContains(response, "Review Activities")
                self.assertContains(response, "Review Schools")
                self.assertContains(response, f'href="{reverse("location-list")}"', html=False)
                self.assertContains(response, f'href="{reverse("course-list")}"', html=False)
                self.assertContains(response, f'href="{reverse("school-list")}"', html=False)
                self.assertContains(response, f'name="schools" value="{self.school.id}"', html=False)
                self.assertNotContains(response, 'name="sched_data"', html=False)
                self.assertNotContains(response, '<form action="", method=POST>', html=False)

    def test_schedule_create_and_update_posts_preserve_existing_crud_behavior(self):
        create_response = self.client.post(
            reverse("sched-create"),
            {"sched_name": "Created Schedule", "schools": [str(self.school.id)]},
        )

        self.assertRedirects(create_response, reverse("sched-list"))
        created_schedule = TheSched.objects.get(sched_name="Created Schedule")
        self.assertEqual(list(created_schedule.schools.all()), [self.school])
        self.assertEqual(created_schedule.sched_data, {})

        second_school = Schools.schools_list.create(
            school_name="Updated Schedule School",
            arrive="Thur",
            depart="Fri",
            total_students=16,
            ag_num=1,
            attending_year="2026-06-04",
        )
        update_response = self.client.post(
            reverse("sched-update", args=[created_schedule.id]),
            {"sched_name": "Updated Schedule", "schools": [str(second_school.id)]},
        )

        self.assertRedirects(update_response, reverse("sched-list"))
        created_schedule.refresh_from_db()
        self.assertEqual(created_schedule.sched_name, "Updated Schedule")
        self.assertEqual(list(created_schedule.schools.all()), [second_school])
        self.assertEqual(created_schedule.sched_data, {})

    def test_schedule_form_saves_selected_schools_and_excludes_sched_data(self):
        second_school = Schools.schools_list.create(
            school_name="Second Form School",
            arrive="Thur",
            depart="Fri",
            total_students=16,
            ag_num=1,
            attending_year="2026-06-04",
        )
        form = SchedForm(data={
            "sched_name": "Form Selected Schedule",
            "schools": [str(self.school.id), str(second_school.id)],
            "sched_data": '{"operator": "should be ignored"}',
        })

        self.assertTrue(form.is_valid(), form.errors)
        created_schedule = form.save()
        self.assertEqual(set(created_schedule.schools.all()), {self.school, second_school})
        self.assertEqual(created_schedule.sched_data, {})
        self.assertNotIn("sched_data", form.fields)

        update_form = SchedForm(
            data={"sched_name": "Updated Form Schedule", "schools": [str(second_school.id)]},
            instance=created_schedule,
        )
        self.assertTrue(update_form.is_valid(), update_form.errors)
        updated_schedule = update_form.save()
        self.assertEqual(list(updated_schedule.schools.all()), [second_school])

    def test_schedule_delete_confirmation_renders_destructive_warning(self):
        response = self.client.get(reverse("sched-delete", args=[self.schedule.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm Schedule Deletion")
        self.assertContains(response, "Existing Operational Schedule")
        self.assertContains(response, "removes its record data")
        self.assertContains(response, "current configuration")
        self.assertContains(response, "This action cannot be undone.")
        self.assertContains(response, "Confirm Delete")
        self.assertContains(response, "Cancel")


@override_settings(ALLOWED_HOSTS=["localhost", "testserver"])
class ScheduleGenerationRegressionTests(TestCase):
    def setUp(self):
        location = Locations.objects.create(loc_name="Schedule Regression Location", loc_short="SR")
        ropes = Course.objects.create(course_name="Ropes", abriviation="ROP", course_len=2)
        ropes.primary_locs.add(location)
        self.two_block = Course.objects.create(course_name="Regression Two Block", abriviation="R2", course_len=2)
        self.one_block = Course.objects.create(course_name="Regression One Block", abriviation="R1", course_len=1)
        self.night = Course.objects.create(course_name="Regression Night", abriviation="RN", course_len=0)
        for course in (self.two_block, self.one_block, self.night):
            course.primary_locs.add(location)

        self.school = Schools.schools_list.create(
            school_name="Balanced Regression School",
            arrive="Thur",
            depart="Fri",
            total_students=16,
            ag_num=1,
            attending_year="2026-06-04",
        )
        self.school.subject.set([self.two_block, self.one_block, self.night])
        self.schedule = TheSched.objects.create(sched_name="Regression Schedule", sched_data={})
        self.schedule.schools.add(self.school)

    def render_schedule_detail(self):
        request = RequestFactory().get(reverse("sched-detail", args=[self.schedule.id]))
        response = SchedDetail.as_view()(request, pk=self.schedule.id)
        response.render()
        return response, response.content.decode()

    def export_schedule_csv(self):
        request = RequestFactory().get(reverse("sched-export", args=[self.schedule.id]))
        return schedule_csv_export(request, pk=self.schedule.id)

    def parse_schedule_csv(self, response):
        return list(csv.DictReader(StringIO(response.content.decode())))

    def create_second_valid_school(self):
        school = Schools.schools_list.create(
            school_name="Second Scoped School",
            arrive="Thur",
            depart="Fri",
            total_students=16,
            ag_num=1,
            attending_year="2026-06-04",
        )
        school.subject.set([self.two_block, self.one_block, self.night])
        return school

    def test_schedules_generate_only_their_attached_schools(self):
        second_school = self.create_second_valid_school()
        second_schedule = TheSched.objects.create(sched_name="Second Scoped Schedule")
        second_schedule.schools.add(second_school)

        first_output = self.schedule.create_sched
        second_output = second_schedule.create_sched

        self.assertEqual(first_output["ags"], ["Balanced Regression School 0"])
        self.assertEqual(second_output["ags"], ["Second Scoped School 0"])
        self.assertNotEqual(first_output["ags"], second_output["ags"])

    def test_diagnostics_ignore_unattached_schools(self):
        unattached_school = Schools.schools_list.create(
            school_name="Unattached Invalid School",
            arrive="Thur",
            depart="Fri",
            total_students=16,
            ag_num=1,
            attending_year="2026-06-04",
        )
        invalid_activity = Course.objects.create(course_name="Unattached Invalid Activity", abriviation="UIA", course_len=1)
        unattached_school.subject.set([invalid_activity])

        generated_schedule = self.schedule.create_sched

        self.assertEqual(list(generated_schedule)[:len(SCHEDULE_SLOT_KEYS)], list(SCHEDULE_SLOT_KEYS))
        self.assertEqual(self.schedule.generation_diagnostics, [])
        self.assertTrue(self.schedule.generation_complete)
        self.assertEqual(generated_schedule["ags"], ["Balanced Regression School 0"])

    def test_schedule_generation_refreshes_blank_sorted_activity_list(self):
        self.assertEqual(self.school.sorted_subject_lst, "")

        generated_schedule = self.schedule.create_sched

        self.assertNotIn("", [activity for values in generated_schedule.values() for activity in values])
        self.school.refresh_from_db()
        self.assertEqual(self.school.sorted_subject_lst, "")

    def test_schedule_recursion_does_not_look_up_empty_activity_name(self):
        class RejectEmptyActivityLookup(dict):
            def __getitem__(self, activity_name):
                if not activity_name:
                    raise AssertionError("Scheduling recursion received an empty activity name")
                return super().__getitem__(activity_name)

        with patch("scheduler_app.models.class_locs", RejectEmptyActivityLookup()):
            self.schedule.create_sched

    def test_activity_with_no_primary_locations_aborts_generation_gracefully(self):
        activity = Course.objects.create(course_name="No Location Activity", abriviation="NLA", course_len=1)
        self.school.subject.set([self.two_block, activity, self.night])

        generated_schedule = self.schedule.create_sched

        self.assertEqual(generated_schedule, {})
        self.assertEqual(self.schedule.generation_diagnostics, [{
            "school": "Balanced Regression School",
            "activity": "No Location Activity",
            "reason": "Activity is not connected to any scheduling Locations.",
        }])

    def test_activity_missing_from_current_location_lookups_is_diagnosed(self):
        initialize_scheduling_data(force=True)
        class_locs.pop(self.one_block.course_name)

        diagnostics = self.schedule.get_scheduling_diagnostics()

        self.assertIn({
            "school": "Balanced Regression School",
            "activity": "Regression One Block",
            "reason": "Activity does not appear in current scheduling Location lookups.",
        }, diagnostics)

    def test_activity_with_only_unavailable_locations_aborts_generation_gracefully(self):
        unavailable_location = Locations.objects.create(
            loc_name="Unavailable Regression Location",
            loc_short="URL",
            availible=False,
        )
        activity = Course.objects.create(course_name="Unavailable Location Activity", abriviation="ULA", course_len=1)
        activity.primary_locs.add(unavailable_location)
        self.school.subject.set([self.two_block, activity, self.night])

        generated_schedule = self.schedule.create_sched

        self.assertEqual(generated_schedule, {})
        self.assertEqual(self.schedule.generation_diagnostics, [{
            "school": "Balanced Regression School",
            "activity": "Unavailable Location Activity",
            "reason": "Activity has no available scheduling Locations.",
        }])

    def make_location_valid_schedule_unassignable(self):
        location = Locations.objects.get(loc_name="Schedule Regression Location")
        extra_two_block = Course.objects.create(course_name="Competing Two Block", abriviation="C2", course_len=2)
        extra_two_block.primary_locs.add(location)
        self.school.subject.set([self.two_block, extra_two_block, self.one_block, self.night])

    def test_location_valid_schedule_that_cannot_fit_reports_incomplete_generation(self):
        self.make_location_valid_schedule_unassignable()

        generated_schedule = self.schedule.create_sched

        self.assertFalse(self.schedule.generation_complete)
        self.assertEqual(self.schedule.generation_diagnostics, [])
        self.assertEqual(generated_schedule["ags"], ["Balanced Regression School 0"])

    def test_schedule_detail_marks_unassignable_location_valid_output_incomplete(self):
        self.make_location_valid_schedule_unassignable()

        response, rendered_content = self.render_schedule_detail()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Schedule generation is incomplete", rendered_content)
        self.assertIn("Activity and Location configuration passed initial checks", rendered_content)
        self.assertIn("Location capacity, timing constraints, or Activity combinations", rendered_content)
        self.assertIn("Incomplete Schedule Output", rendered_content)
        self.assertIn("must not be treated as a fully generated Schedule", rendered_content)
        self.assertIn("<table", rendered_content)

    def test_incomplete_schedule_csv_export_labels_output_and_unassigned_entries(self):
        self.make_location_valid_schedule_unassignable()

        response = self.export_schedule_csv()
        rows = self.parse_schedule_csv(response)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(rows)
        self.assertEqual({row["Generation Status"] for row in rows}, {"Incomplete"})
        self.assertIn("Unassigned", {row["Activity"] for row in rows})

    def test_schedule_detail_renders_activity_location_diagnostic_instead_of_table(self):
        activity = Course.objects.create(course_name="Diagnostic Activity", abriviation="DA", course_len=1)
        self.school.subject.set([self.two_block, activity, self.night])

        response, rendered_content = self.render_schedule_detail()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Schedule generation could not continue", rendered_content)
        self.assertIn("Schedule generation cannot continue until", rendered_content)
        self.assertIn("Balanced Regression School", rendered_content)
        self.assertIn("Diagnostic Activity", rendered_content)
        self.assertIn("Activity is not connected to any scheduling Locations.", rendered_content)
        self.assertNotIn("<table", rendered_content)
        self.assertNotIn(reverse("sched-export", args=[self.schedule.id]), rendered_content)

    def test_blocked_schedule_csv_export_returns_diagnostic_instead_of_empty_csv(self):
        activity = Course.objects.create(course_name="Blocked Export Activity", abriviation="BEA", course_len=1)
        self.school.subject.set([self.two_block, activity, self.night])

        response = self.export_schedule_csv()
        content = response.content.decode()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response["Content-Type"], "text/plain")
        self.assertNotIn("Content-Disposition", response)
        self.assertIn("Schedule CSV export is unavailable because generation is blocked", content)
        self.assertIn("Balanced Regression School", content)
        self.assertIn("Blocked Export Activity", content)
        self.assertNotIn("Schedule Name,Generation Status", content)

    def test_schedule_detail_generates_and_renders_balanced_school(self):
        response, rendered_content = self.render_schedule_detail()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context_data["generation_complete"])
        self.assertIs(response.context_data["schedule_days"], SCHEDULE_DAYS)
        self.assertIs(response.context_data["schedule_legend"], SCHEDULE_LEGEND)
        self.assertIn("Schedule generated successfully", rendered_content)
        self.assertIn("Selected Schools", rendered_content)
        self.assertIn("Balanced Regression School", rendered_content)
        self.assertIn("Balanced Regression School 0", rendered_content)
        self.assertIn("Regression Two Block", rendered_content)
        self.assertIn("Regression One Block", rendered_content)
        self.assertIn("Regression Night", rendered_content)
        self.assertIn("<table", rendered_content)
        self.assertIn("Export CSV", rendered_content)
        self.assertIn("Download generated schedule for spreadsheet review", rendered_content)
        self.assertIn(reverse("sched-export", args=[self.schedule.id]), rendered_content)

    def test_successful_schedule_csv_export_contains_operational_table_rows(self):
        response = self.export_schedule_csv()
        rows = self.parse_schedule_csv(response)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertEqual(response["Content-Disposition"], 'attachment; filename="regression-schedule.csv"')
        self.assertEqual(list(rows[0]), [
            "Schedule Name",
            "Generation Status",
            "Day",
            "Time Block",
            "Activity Group",
            "Activity",
            "Location",
        ])
        self.assertEqual(len(rows), 20)
        self.assertEqual({row["Schedule Name"] for row in rows}, {"Regression Schedule"})
        self.assertEqual({row["Generation Status"] for row in rows}, {"Complete"})
        self.assertEqual({row["Activity Group"] for row in rows}, {"Balanced Regression School 0"})
        self.assertEqual(
            [(row["Day"], row["Time Block"]) for row in rows],
            [(day["name"], slot["label"]) for day in SCHEDULE_DAYS for slot in day["slots"]],
        )
        self.assertIn("Regression Two Block", {row["Activity"] for row in rows})
        self.assertIn("Regression One Block", {row["Activity"] for row in rows})
        self.assertIn("Regression Night", {row["Activity"] for row in rows})
        self.assertEqual({row["Location"] for row in rows}, {""})

    def test_schedule_csv_export_uses_only_attached_schools(self):
        other_school = self.create_second_valid_school()
        other_schedule = TheSched.objects.create(sched_name="Other Export Schedule", sched_data={})
        other_schedule.schools.add(other_school)

        response = self.export_schedule_csv()
        rows = self.parse_schedule_csv(response)

        self.assertEqual(response.status_code, 200)
        self.assertEqual({row["Activity Group"] for row in rows}, {"Balanced Regression School 0"})
        self.assertNotIn("Second Valid School", response.content.decode())


@override_settings(ALLOWED_HOSTS=["localhost", "testserver"])
class SchoolFormWorkflowTests(TestCase):
    def setUp(self):
        wm_location = Locations.objects.create(loc_name="WM", loc_short="WM")
        various_location = Locations.objects.create(loc_name="Various", loc_short="VAR")
        range_location = Locations.objects.create(loc_name="Range", loc_short="RNG")
        self.wm = Course.objects.create(course_name="WM", abriviation="WM", course_len=2)
        self.wm.primary_locs.add(wm_location)
        self.night_hike = Course.objects.create(course_name="Night Hike", abriviation="NH", course_len=0)
        self.night_hike.primary_locs.add(various_location)
        self.archery = Course.objects.create(course_name="Archery", abriviation="ARCH", course_len=1)
        self.archery.primary_locs.add(range_location)
        self.client = Client(HTTP_HOST="localhost")

    def school_form_data(self, name, subjects, arrive="Mon", depart="Wed"):
        return {
            "school_name": name,
            "subject": [str(subject.id) for subject in subjects],
            "arrive": arrive,
            "depart": depart,
            "total_students": "16",
            "ag_num": "1",
            "attending_year": "2026-06-04",
            "sorted_subject_lst": "",
        }

    def test_activity_group_suggestion_uses_default_target_size(self):
        self.assertEqual(suggest_activity_group_count(1), 1)
        self.assertEqual(suggest_activity_group_count(16), 1)
        self.assertEqual(suggest_activity_group_count(17), 2)
        self.assertEqual(suggest_activity_group_count(33), 3)

    def test_blank_activity_groups_use_server_side_suggestion_when_saved(self):
        form_data = self.school_form_data(
            "Suggested Group School", [self.wm, self.archery, self.night_hike], arrive="Thur", depart="Fri"
        )
        form_data["total_students"] = "33"
        form_data["ag_num"] = ""
        form = SchoolsForm(data=form_data)

        self.assertTrue(form.is_valid(), form.errors)
        school = form.save()
        self.assertEqual(school.ag_num, 3)

    def test_manual_activity_group_override_is_preserved(self):
        form_data = self.school_form_data(
            "Manual Group School", [self.wm, self.archery, self.night_hike], arrive="Thur", depart="Fri"
        )
        form_data["total_students"] = "33"
        form_data["ag_num"] = "5"
        form = SchoolsForm(data=form_data)

        self.assertTrue(form.is_valid(), form.errors)
        school = form.save()
        self.assertEqual(school.ag_num, 5)

    def test_activity_group_suggestion_preserves_existing_activity_validation(self):
        form_data = self.school_form_data(
            "Suggested Invalid School", [self.wm, self.night_hike], arrive="Thur", depart="Fri"
        )
        form_data["total_students"] = "33"
        form_data["ag_num"] = ""
        form = SchoolsForm(data=form_data)

        self.assertFalse(form.is_valid())
        self.assertEqual(form.data["ag_num"], "3")
        self.assertIn("Daytime blocks: required 3, selected 2 (under by 1).", form.non_field_errors())

    def test_school_form_explains_activity_group_suggestion_and_override(self):
        form = SchoolsForm()

        self.assertEqual(form.fields["ag_num"].label, "Activity Groups")
        self.assertIn("one group per 16 students", form.fields["ag_num"].help_text)
        self.assertIn("Adjust this value manually", form.fields["ag_num"].help_text)
        self.assertEqual(form.fields["ag_num"].widget.attrs["data-target-group-size"], 16)

    def test_school_pages_link_to_canonical_school_list_in_navbar(self):
        response = self.client.get(reverse("school-create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'<a class="nav-link" href="{reverse("school-list")}">Schools</a>',
            html=True,
        )

    def test_school_list_add_new_school_links_to_canonical_create_page(self):
        response = self.client.get(reverse("school-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'<a href="{reverse("school-create")}" class="btn btn-primary">Add New School</a>',
            html=True,
        )

    def create_school_with_activities(self):
        school = Schools.schools_list.create(
            school_name="Readability Test School",
            arrive="Mon",
            depart="Thur",
            total_students=48,
            ag_num=3,
            attending_year="2026-06-04",
        )
        school.subject.set([self.wm, self.archery, self.night_hike])
        return school

    def test_school_list_renders_readable_table_and_actions(self):
        self.create_school_with_activities()

        response = self.client.get(reverse("school-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="table table-striped table-hover align-middle"', html=False)
        self.assertContains(response, "School Name")
        self.assertContains(response, "Arrival Day")
        self.assertContains(response, "Departure Day")
        self.assertContains(response, "Activity Groups")
        self.assertContains(response, "Students")
        self.assertContains(response, "Readability Test School")
        self.assertContains(response, "Monday")
        self.assertContains(response, "Thursday")
        self.assertContains(response, "View")
        self.assertContains(response, "Edit")
        self.assertContains(response, "Delete")

    def test_school_list_renders_selected_activity_summary(self):
        self.create_school_with_activities()

        response = self.client.get(reverse("school-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Selected Activities")
        self.assertContains(response, "WM")
        self.assertContains(response, "Archery")
        self.assertContains(response, "Night Hike")

    def test_school_detail_renders_structured_visit_information(self):
        school = self.create_school_with_activities()

        response = self.client.get(reverse("school-detail", args=[school.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Readability Test School")
        self.assertContains(response, "Arrival Day")
        self.assertContains(response, "Monday")
        self.assertContains(response, "Departure Day")
        self.assertContains(response, "Thursday")
        self.assertContains(response, "Activity Groups")
        self.assertContains(response, "Students")
        self.assertContains(response, "Edit School")
        self.assertContains(response, "Delete School")
        self.assertContains(response, "Back to Schools")

    def test_school_detail_groups_selected_activities_by_schedule_length(self):
        school = self.create_school_with_activities()

        response = self.client.get(reverse("school-detail", args=[school.id]))

        self.assertEqual(response.status_code, 200)
        content = response.content
        two_block_heading = content.index(b"Two-block daytime activities")
        one_block_heading = content.index(b"One-block daytime activities")
        night_heading = content.index(b"Night activities")
        self.assertLess(two_block_heading, content.index(b">WM</li>"))
        self.assertLess(content.index(b">WM</li>"), one_block_heading)
        self.assertLess(one_block_heading, content.index(b">Archery</li>"))
        self.assertLess(content.index(b">Archery</li>"), night_heading)
        self.assertLess(night_heading, content.index(b">Night Hike</li>"))

    def test_school_delete_confirmation_renders_destructive_warning(self):
        school = self.create_school_with_activities()

        response = self.client.get(reverse("school-delete", args=[school.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm School Deletion")
        self.assertContains(response, "Readability Test School")
        self.assertContains(response, "may affect generated schedules")
        self.assertContains(response, "This action cannot be undone.")
        self.assertContains(response, "Confirm Delete")
        self.assertContains(response, "Cancel")

    def test_canonical_school_create_page_renders_successfully(self):
        response = self.client.get(reverse("school-create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Activities")
        self.assertContains(response, "Schedule Block Summary")

    def test_school_forms_render_grouped_activity_checkboxes_with_costs(self):
        for url in ("/school_create", "/add_school"):
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Two-block daytime activities")
                self.assertContains(response, "One-block daytime activities")
                self.assertContains(response, "Night activities")
                self.assertContains(response, "WM — 2 daytime blocks")
                self.assertContains(response, "Archery — 1 daytime block")
                self.assertContains(response, "Night Hike — night activity")
                self.assertContains(response, 'type="checkbox"', count=3)
                self.assertNotContains(response, '<select name="subject"')

    def test_activity_checkboxes_render_block_metadata_for_live_summary(self):
        form = SchoolsForm()
        rendered_choices = str(form["subject"])

        self.assertIn(f'value="{self.wm.id}"', rendered_choices)
        self.assertIn(f'value="{self.archery.id}"', rendered_choices)
        self.assertIn(f'value="{self.night_hike.id}"', rendered_choices)
        self.assertIn('data-daytime-blocks="2" data-night-blocks="0"', rendered_choices)
        self.assertIn('data-daytime-blocks="1" data-night-blocks="0"', rendered_choices)
        self.assertIn('data-daytime-blocks="0" data-night-blocks="1"', rendered_choices)

    def test_server_rendered_summary_remains_available_without_javascript(self):
        form = SchoolsForm(data=self.school_form_data(
            "Balanced Summary School", [self.wm, self.archery, self.night_hike], arrive="Thur", depart="Fri"
        ))
        self.assertTrue(form.is_valid(), form.errors)
        summary = school_slot_accounting_summary(form)

        rendered_summary = render_to_string("pay_end/_school_fields.html", {"form": form, "slot_summary": summary})

        self.assertIn('data-summary-value="selected-daytime">3</span>', rendered_summary)
        self.assertIn('data-summary-value="selected-night">1</span>', rendered_summary)
        self.assertIn('data-summary-value="selected-total">4</span>', rendered_summary)
        self.assertIn("Ready to save: selected Activities exactly match required blocks.", rendered_summary)
        self.assertIn("Server-side validation remains authoritative", rendered_summary)

    def test_server_rendered_summary_marks_mismatch_not_ready(self):
        form = SchoolsForm(data=self.school_form_data(
            "Mismatch Summary School", [self.wm, self.night_hike], arrive="Thur", depart="Fri"
        ))
        self.assertFalse(form.is_valid())
        summary = school_slot_accounting_summary(form)

        rendered_summary = render_to_string("pay_end/_school_fields.html", {"form": form, "slot_summary": summary})

        self.assertIn('data-summary-status="daytime">under by 1</span>', rendered_summary)
        self.assertIn("Not ready to save: selected Activities must exactly match required blocks.", rendered_summary)
        self.assertIn("border-warning", rendered_summary)

    def test_school_update_checks_existing_activity_selections(self):
        school = Schools(
            school_name="Selected Activities School",
            arrive="Mon",
            depart="Wed",
            total_students=16,
            ag_num=1,
            attending_year="2026-06-04",
        )
        school.save()
        school.subject.set([self.wm, self.night_hike])
        school.update_sorted_subject_lst()
        school.save(update_fields=["sorted_subject_lst"])

        response = self.client.get(f"/school_update/{school.id}/")

        self.assertEqual(response.status_code, 200)
        selected_values = {str(value) for value in response.context["form"]["subject"].value()}
        self.assertEqual(selected_values, {str(self.wm.id), str(self.night_hike.id)})

    def test_school_create_saves_subjects_after_instance_has_id(self):
        response = self.client.post(
            "/school_create",
            self.school_form_data("Create Test School", [self.wm, self.archery, self.night_hike], arrive="Thur", depart="Fri"),
        )

        self.assertEqual(response.status_code, 302)
        school = Schools.schools_list.get(school_name="Create Test School")
        self.assertEqual(list(school.subject.order_by("course_name")), [self.archery, self.night_hike, self.wm])
        self.assertEqual(school.sorted_subject_lst, "WM,Archery,Night Hike")

    def test_school_update_refreshes_sorted_subjects_after_subject_changes(self):
        school = Schools(school_name="Update Test School", arrive="Mon", depart="Wed", total_students=16, ag_num=1, attending_year="2026-06-04")
        school.save()
        school.subject.set([self.wm, self.night_hike])
        school.update_sorted_subject_lst()
        school.save(update_fields=["sorted_subject_lst"])

        response = self.client.post(
            f"/school_update/{school.id}/",
            self.school_form_data("Update Test School", [self.wm, self.archery, self.night_hike], arrive="Thur", depart="Fri"),
        )

        self.assertEqual(response.status_code, 302)
        school.refresh_from_db()
        self.assertEqual(school.arrive, "Thur")
        self.assertEqual(school.depart, "Fri")
        self.assertEqual(list(school.subject.order_by("course_name")), [self.archery, self.night_hike, self.wm])
        self.assertEqual(school.sorted_subject_lst, "WM,Archery,Night Hike")

    def test_add_school_function_view_uses_valid_form_save(self):
        response = self.client.post(
            "/add_school",
            self.school_form_data("Function Test School", [self.wm, self.archery, self.night_hike], arrive="Thur", depart="Fri"),
        )

        self.assertEqual(response.status_code, 302)
        school = Schools.schools_list.get(school_name="Function Test School")
        self.assertEqual(school.sorted_subject_lst, "WM,Archery,Night Hike")

    def test_add_school_page_includes_slot_accounting_summary(self):
        response = self.client.get("/add_school")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Schedule Block Summary")
        self.assertContains(response, "data-summary-value=\"selected-daytime\">0</span>")
        self.assertContains(response, "data-summary-value=\"selected-night\">0</span>")

    def test_school_update_page_includes_slot_accounting_summary(self):
        school = Schools(school_name="Summary Test School", arrive="Mon", depart="Wed", total_students=16, ag_num=1, attending_year="2026-06-04")
        school.save()
        school.subject.set([self.wm, self.night_hike])
        school.update_sorted_subject_lst()
        school.save(update_fields=["sorted_subject_lst"])

        response = self.client.get(f"/school_update/{school.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Schedule Block Summary")
        self.assertContains(response, "Required daytime blocks:</strong> 8")
        self.assertContains(response, "Required night blocks:</strong> 2")
        self.assertContains(response, "data-summary-value=\"selected-daytime\">2</span>")
        self.assertContains(response, "data-summary-value=\"selected-night\">1</span>")
        self.assertContains(response, "data-summary-status=\"daytime\">under by 6</span>")
        self.assertContains(response, "data-summary-status=\"night\">under by 1</span>")

    def test_invalid_school_create_post_recalculates_slot_summary_from_selection(self):
        existing = Schools(school_name="Duplicate School", arrive="Mon", depart="Wed", total_students=16, ag_num=1, attending_year="2026-06-04")
        existing.save()

        response = self.client.post(
            "/school_create",
            self.school_form_data("Duplicate School", [self.archery, self.night_hike], arrive="Tue", depart="Thur"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Schedule Block Summary")
        self.assertContains(response, "Required daytime blocks:</strong> 8")
        self.assertContains(response, "Required night blocks:</strong> 2")
        self.assertContains(response, "data-summary-value=\"selected-daytime\">1</span>")
        self.assertContains(response, "data-summary-value=\"selected-night\">1</span>")
        self.assertContains(response, "data-summary-status=\"daytime\">under by 7</span>")
        self.assertContains(response, "data-summary-status=\"night\">under by 1</span>")


@override_settings(ALLOWED_HOSTS=["localhost", "testserver"])
class SchoolActivityBlockValidationTests(TestCase):
    def setUp(self):
        location = Locations.objects.create(loc_name="Validation Location", loc_short="VL")
        self.two_block = Course.objects.create(course_name="Two Block", abriviation="TB", course_len=2)
        self.one_block = Course.objects.create(course_name="One Block", abriviation="OB", course_len=1)
        self.extra_daytime = Course.objects.create(course_name="Extra Daytime", abriviation="ED", course_len=1)
        self.night = Course.objects.create(course_name="Night One", abriviation="N1", course_len=0)
        self.extra_night = Course.objects.create(course_name="Night Two", abriviation="N2", course_len=0)
        for course in (self.two_block, self.one_block, self.extra_daytime, self.night, self.extra_night):
            course.primary_locs.add(location)
        self.client = Client(HTTP_HOST="localhost")

    def form_data(self, name, subjects):
        return {
            "school_name": name,
            "subject": [str(subject.id) for subject in subjects],
            "arrive": "Thur",
            "depart": "Fri",
            "total_students": "16",
            "ag_num": "1",
            "attending_year": "2026-06-04",
            "sorted_subject_lst": "",
        }

    def assert_blocked(self, response, expected_message, selected_subjects):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "School cannot be saved")
        self.assertContains(response, "Selected activities must exactly match the required trip blocks")
        self.assertContains(response, expected_message)
        selected_values = {str(value) for value in response.context["form"]["subject"].value()}
        self.assertEqual(selected_values, {str(subject.id) for subject in selected_subjects})

    def test_balanced_selection_saves_in_canonical_and_legacy_workflows(self):
        selected = [self.two_block, self.one_block, self.night]
        canonical = self.client.post(reverse("school-create"), self.form_data("Canonical Balanced", selected))
        legacy = self.client.post(reverse("add-school"), self.form_data("Legacy Balanced", selected))

        self.assertRedirects(canonical, reverse("school-list"))
        self.assertEqual(legacy.status_code, 302)
        self.assertTrue(Schools.schools_list.filter(school_name="Canonical Balanced").exists())
        self.assertTrue(Schools.schools_list.filter(school_name="Legacy Balanced").exists())

    def test_daytime_under_selection_blocks_save(self):
        selected = [self.two_block, self.night]
        response = self.client.post(reverse("school-create"), self.form_data("Daytime Under", selected))
        self.assert_blocked(response, "Daytime blocks: required 3, selected 2 (under by 1).", selected)

    def test_legacy_workflow_blocks_invalid_selection(self):
        selected = [self.two_block, self.night]
        response = self.client.post(reverse("add-school"), self.form_data("Legacy Invalid", selected))

        self.assert_blocked(response, "Daytime blocks: required 3, selected 2 (under by 1).", selected)
        self.assertFalse(Schools.schools_list.filter(school_name="Legacy Invalid").exists())

    def test_daytime_over_selection_blocks_save(self):
        selected = [self.two_block, self.one_block, self.extra_daytime, self.night]
        response = self.client.post(reverse("school-create"), self.form_data("Daytime Over", selected))
        self.assert_blocked(response, "Daytime blocks: required 3, selected 4 (over by 1).", selected)

    def test_night_under_selection_blocks_save(self):
        selected = [self.two_block, self.one_block]
        response = self.client.post(reverse("school-create"), self.form_data("Night Under", selected))
        self.assert_blocked(response, "Night blocks: required 1, selected 0 (under by 1).", selected)

    def test_night_over_selection_blocks_save(self):
        selected = [self.two_block, self.one_block, self.night, self.extra_night]
        response = self.client.post(reverse("school-create"), self.form_data("Night Over", selected))
        self.assert_blocked(response, "Night blocks: required 1, selected 2 (over by 1).", selected)

    def test_update_validation_blocks_save_and_preserves_existing_record(self):
        school = Schools.schools_list.create(
            school_name="Update Validation",
            arrive="Thur",
            depart="Fri",
            total_students=16,
            ag_num=1,
            attending_year="2026-06-04",
        )
        school.subject.set([self.two_block, self.one_block, self.night])
        selected = [self.two_block, self.night]

        response = self.client.post(
            reverse("school-update", args=[school.id]),
            self.form_data("Update Validation", selected),
        )

        self.assert_blocked(response, "Total blocks: required 4, selected 3 (under by 1).", selected)
        school.refresh_from_db()
        self.assertEqual(set(school.subject.all()), {self.two_block, self.one_block, self.night})


@override_settings(ALLOWED_HOSTS=["localhost", "testserver"])
class SchedulingLookupRefreshTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST="localhost")
        self.first_location = Locations.objects.create(loc_name="Training Room", loc_short="TR")
        self.second_location = Locations.objects.create(loc_name="Conference Hall", loc_short="CH")
        self.support_daytime = Course.objects.create(course_name="Support Daytime", abriviation="SD", course_len=2)
        self.support_daytime.primary_locs.add(self.first_location)
        self.support_night = Course.objects.create(course_name="Support Night", abriviation="SN", course_len=0)
        self.support_night.primary_locs.add(self.first_location)
        self.school = Schools.schools_list.create(
            school_name="Lookup Refresh School",
            arrive="Mon",
            depart="Wed",
            total_students=16,
            ag_num=1,
            attending_year="2026-06-04",
        )

    def school_form_data(self, course):
        return {
            "school_name": self.school.school_name,
            "subject": [str(course.id), str(self.support_daytime.id), str(self.support_night.id)],
            "arrive": "Thur",
            "depart": "Fri",
            "total_students": str(self.school.total_students),
            "ag_num": str(self.school.ag_num),
            "attending_year": "2026-06-04",
            "sorted_subject_lst": self.school.sorted_subject_lst,
        }

    def test_school_update_refreshes_lookups_for_new_course(self):
        initialize_scheduling_data(force=True)
        course = Course.objects.create(course_name="Sales Training", abriviation="ST", course_len=1)
        course.primary_locs.add(self.first_location)

        response = self.client.post(
            reverse("school-update", args=[self.school.id]),
            self.school_form_data(course),
        )

        self.assertRedirects(response, reverse("school-list"))
        self.school.refresh_from_db()
        self.assertIn("Sales Training", self.school.sorted_subject_lst)
        self.assertEqual(class_len["Sales Training"], 1)
        self.assertEqual(class_locs["Sales Training"], ["Training Room"])

    def test_school_sorting_refreshes_updated_course_length_and_locations(self):
        course = Course.objects.create(course_name="Sales Training", abriviation="ST", course_len=1)
        course.primary_locs.add(self.first_location)
        self.school.subject.set([course])
        self.school.update_sorted_subject_lst()

        course.course_len = 0
        course.save(update_fields=["course_len"])
        course.primary_locs.set([self.second_location])
        self.school.update_sorted_subject_lst()

        self.assertEqual(self.school.sorted_subject_lst, "Sales Training")
        self.assertEqual(class_len["Sales Training"], 0)
        self.assertEqual(class_locs["Sales Training"], ["Conference Hall"])


@override_settings(ALLOWED_HOSTS=["localhost", "testserver"])
class CourseFormWorkflowTests(TestCase):
    def setUp(self):
        self.alphabetically_first_location = Locations.objects.create(
            loc_name="apple Grove",
            loc_short="AG",
            availible=True,
        )
        self.available_location = Locations.objects.create(
            loc_name="Archery Range",
            loc_short="AR",
            availible=True,
        )
        self.second_available_location = Locations.objects.create(
            loc_name="Boathouse",
            loc_short="BOAT",
            availible=True,
        )
        self.unavailable_location = Locations.objects.create(
            loc_name="Closed Field",
            loc_short="CF",
            availible=False,
        )
        self.course = Course.objects.create(
            course_name="Existing Course",
            abriviation="EX",
            course_len=2,
        )
        self.course.primary_locs.set([self.available_location, self.unavailable_location])
        self.client = Client(HTTP_HOST="localhost")

    def course_form_data(self, name, abbreviation, course_len, locations):
        return {
            "course_name": name,
            "abriviation": abbreviation,
            "course_len": str(course_len),
            "primary_locs": [str(location.id) for location in locations],
        }

    def test_course_list_renders_readable_table_and_actions(self):
        response = self.client.get(reverse("course-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="table table-striped table-hover align-middle"', html=False)
        self.assertContains(response, "Activity Name")
        self.assertContains(response, "Abbreviation")
        self.assertContains(response, "Schedule Length")
        self.assertContains(response, "Primary Locations")
        self.assertContains(response, "Existing Course")
        self.assertContains(response, "Archery Range")
        self.assertContains(response, "Closed Field")
        self.assertContains(response, "View")
        self.assertContains(response, "Edit")
        self.assertContains(response, "Delete")

    def test_course_list_renders_human_readable_schedule_length_labels(self):
        Course.objects.create(course_name="One Block Course", abriviation="ONE", course_len=1)
        Course.objects.create(course_name="Night Course", abriviation="NGT", course_len=0)

        response = self.client.get(reverse("course-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Two-block daytime activity")
        self.assertContains(response, "One-block daytime activity")
        self.assertContains(response, "Night activity")

    def test_course_detail_renders_structured_information_and_primary_locations(self):
        response = self.client.get(reverse("course-detail", args=[self.course.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Existing Course", count=3)
        self.assertContains(response, "Abbreviation")
        self.assertContains(response, "EX")
        self.assertContains(response, "Two-block daytime activity", count=2)
        self.assertContains(response, "Primary Locations")
        self.assertContains(response, "Archery Range")
        self.assertContains(response, "AR")
        self.assertContains(response, "Closed Field")
        self.assertContains(response, "CF")
        self.assertContains(response, "Edit Activity")
        self.assertContains(response, "Delete Activity")
        self.assertContains(response, "Back to Activities")

    def test_course_delete_confirmation_renders_destructive_warning(self):
        response = self.client.get(reverse("course-delete", args=[self.course.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm Activity Deletion")
        self.assertContains(response, "Existing Course")
        self.assertContains(response, "may affect School activity selections and schedules")
        self.assertContains(response, "This action cannot be undone.")
        self.assertContains(response, "Confirm Delete")
        self.assertContains(response, "Cancel")

    def test_canonical_course_create_get_renders_grouped_location_choices(self):
        response = self.client.get(reverse("course-create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Available Locations")
        self.assertContains(response, "Unavailable Locations")
        self.assertContains(response, "Archery Range — AR")
        self.assertContains(response, "Closed Field — CF — unavailable")
        self.assertLess(
            response.content.index(b"apple Grove"),
            response.content.index(b"Archery Range"),
        )
        self.assertContains(response, "Two-block daytime activity")
        self.assertContains(response, "One-block daytime activity")
        self.assertContains(response, "Night activity")
        self.assertNotContains(response, '<select name="primary_locs"')

    def test_canonical_course_create_post_saves_primary_locations(self):
        response = self.client.post(
            reverse("course-create"),
            self.course_form_data(
                "Created Course",
                "NEW",
                1,
                [self.available_location, self.unavailable_location],
            ),
        )

        self.assertRedirects(response, reverse("course-list"))
        course = Course.objects.get(course_name="Created Course")
        self.assertEqual(course.abriviation, "NEW")
        self.assertEqual(course.course_len, 1)
        self.assertEqual(
            set(course.primary_locs.all()),
            {self.available_location, self.unavailable_location},
        )

    def test_canonical_course_update_get_preserves_selected_primary_locations(self):
        response = self.client.get(reverse("course-update", args=[self.course.id]))

        self.assertEqual(response.status_code, 200)
        selected_values = {str(value) for value in response.context["form"]["primary_locs"].value()}
        self.assertEqual(
            selected_values,
            {str(self.available_location.id), str(self.unavailable_location.id)},
        )
        self.assertContains(response, "Closed Field — CF — unavailable")

    def test_canonical_course_update_post_updates_fields_and_primary_locations(self):
        response = self.client.post(
            reverse("course-update", args=[self.course.id]),
            self.course_form_data(
                "Updated Course",
                "UPD",
                0,
                [self.second_available_location],
            ),
        )

        self.assertRedirects(response, reverse("course-list"))
        self.course.refresh_from_db()
        self.assertEqual(self.course.course_name, "Updated Course")
        self.assertEqual(self.course.abriviation, "UPD")
        self.assertEqual(self.course.course_len, 0)
        self.assertEqual(list(self.course.primary_locs.all()), [self.second_available_location])

    def test_legacy_add_course_workflow_remains_functional(self):
        get_response = self.client.get(reverse("add-course"))
        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, "Available Locations")

        post_response = self.client.post(
            reverse("add-course"),
            self.course_form_data(
                "Legacy Course",
                "LEG",
                2,
                [self.available_location],
            ),
        )

        self.assertRedirects(post_response, "/add_course?submitted=True")
        self.assertEqual(
            list(Course.objects.get(course_name="Legacy Course").primary_locs.all()),
            [self.available_location],
        )

class LocationAvailabilitySchedulingLookupTests(TestCase):
    def setUp(self):
        self.available_location = Locations.objects.create(
            loc_name="Available Range",
            loc_short="AR",
            availible=True,
        )
        self.unavailable_location = Locations.objects.create(
            loc_name="Closed Range",
            loc_short="CR",
            availible=False,
        )
        self.course = Course.objects.create(
            course_name="Availability Test Activity",
            abriviation="ATA",
            course_len=1,
        )
        self.course.primary_locs.set([self.available_location, self.unavailable_location])

    def test_available_location_is_in_scheduling_lookups(self):
        initialize_scheduling_data(force=True)

        self.assertIn("Available Range", master_locs)
        self.assertEqual(class_locs["Availability Test Activity"], ["Available Range"])

    def test_unavailable_location_is_excluded_from_scheduling_lookups(self):
        initialize_scheduling_data(force=True)

        self.assertNotIn("Closed Range", master_locs)
        self.assertNotIn("Closed Range", class_locs["Availability Test Activity"])

    def test_available_to_unavailable_change_is_reflected_after_forced_refresh(self):
        initialize_scheduling_data(force=True)
        self.available_location.availible = False
        self.available_location.save(update_fields=["availible"])

        initialize_scheduling_data(force=True)

        self.assertNotIn("Available Range", master_locs)
        self.assertNotIn("Availability Test Activity", class_locs)

    def test_unavailable_to_available_change_is_reflected_after_forced_refresh(self):
        initialize_scheduling_data(force=True)
        self.unavailable_location.availible = True
        self.unavailable_location.save(update_fields=["availible"])

        initialize_scheduling_data(force=True)

        self.assertIn("Closed Range", master_locs)
        self.assertEqual(
            set(class_locs["Availability Test Activity"]),
            {"Available Range", "Closed Range"},
        )


@override_settings(ALLOWED_HOSTS=["localhost", "testserver"])
class LocationFormWorkflowTests(TestCase):
    def setUp(self):
        self.location = Locations.objects.create(
            loc_name="Existing Location",
            loc_short="EX",
            description="Existing operational notes.",
            availible=True,
        )
        self.client = Client(HTTP_HOST="localhost")

    def location_form_data(self, name, abbreviation, description, available=True):
        data = {
            "loc_name": name,
            "loc_short": abbreviation,
            "description": description,
        }
        if available:
            data["availible"] = "on"
        return data

    def test_location_list_renders_readable_table_and_actions(self):
        response = self.client.get(reverse("location-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="table table-striped table-hover align-middle"', html=False)
        self.assertContains(response, "Location Name")
        self.assertContains(response, "Abbreviation")
        self.assertContains(response, "Operator Notes")
        self.assertContains(response, "Existing operational notes.")
        self.assertContains(response, "View")
        self.assertContains(response, "Edit")
        self.assertContains(response, "Delete")

    def test_location_list_renders_availability_badges(self):
        Locations.objects.create(
            loc_name="Unavailable Location",
            loc_short="UN",
            description="Unavailable notes.",
            availible=False,
        )

        response = self.client.get(reverse("location-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<span class="badge bg-success">Available</span>', html=True)
        self.assertContains(response, '<span class="badge bg-secondary">Unavailable</span>', html=True)

    def test_location_detail_renders_structured_information_and_actions(self):
        response = self.client.get(reverse("location-detail", args=[self.location.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Existing Location", count=3)
        self.assertContains(response, "Abbreviation")
        self.assertContains(response, "EX")
        self.assertContains(response, "Available for Scheduling")
        self.assertContains(response, "Existing operational notes.")
        self.assertContains(response, "Edit Location")
        self.assertContains(response, "Delete Location")
        self.assertContains(response, "Back to Locations")

    def test_location_delete_confirmation_renders_destructive_warning(self):
        response = self.client.get(reverse("location-delete", args=[self.location.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm Location Deletion")
        self.assertContains(response, "Existing Location")
        self.assertContains(response, "may remove it from Activity configurations")
        self.assertContains(response, "This action cannot be undone.")
        self.assertContains(response, "Confirm Delete")
        self.assertContains(response, "Cancel")

    def test_canonical_location_create_get_renders_improved_form(self):
        response = self.client.get(reverse("add-loc"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Location Name")
        self.assertContains(response, "Abbreviation")
        self.assertContains(response, "Operator Notes")
        self.assertContains(response, "Available for Scheduling")
        self.assertContains(response, "Maximum 5 characters")
        self.assertContains(response, "marking it unavailable for scheduling")
        self.assertContains(response, '<textarea name="description"', html=False)

    def test_canonical_location_create_post_preserves_fields_and_availability(self):
        response = self.client.post(
            reverse("add-loc"),
            self.location_form_data(
                "Created Location",
                "NEW",
                "Created location operational notes.",
                available=True,
            ),
        )

        self.assertRedirects(response, reverse("location-list"))
        location = Locations.objects.get(loc_name="Created Location")
        self.assertEqual(location.loc_short, "NEW")
        self.assertEqual(location.description, "Created location operational notes.")
        self.assertTrue(location.availible)

    def test_canonical_location_update_get_preserves_description_and_availability(self):
        response = self.client.get(reverse("location-update", args=[self.location.id]))

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form["description"].value(), "Existing operational notes.")
        self.assertTrue(form["availible"].value())
        self.assertContains(response, "Existing operational notes.")
        self.assertContains(response, 'name="availible"', html=False)
        self.assertContains(response, "checked", count=1)

    def test_canonical_location_update_post_preserves_description_and_unavailability(self):
        response = self.client.post(
            reverse("location-update", args=[self.location.id]),
            self.location_form_data(
                "Updated Location",
                "UPD",
                "Updated operational notes.",
                available=False,
            ),
        )

        self.assertRedirects(response, reverse("location-list"))
        self.location.refresh_from_db()
        self.assertEqual(self.location.loc_name, "Updated Location")
        self.assertEqual(self.location.loc_short, "UPD")
        self.assertEqual(self.location.description, "Updated operational notes.")
        self.assertFalse(self.location.availible)

    def test_legacy_add_location_workflow_remains_functional(self):
        get_response = self.client.get(reverse("add-location"))
        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, "Operator Notes")

        post_response = self.client.post(
            reverse("add-location"),
            self.location_form_data(
                "Legacy Location",
                "LEG",
                "Legacy workflow notes.",
                available=False,
            ),
        )

        self.assertRedirects(post_response, "/add_location?submitted=True")
        location = Locations.objects.get(loc_name="Legacy Location")
        self.assertEqual(location.description, "Legacy workflow notes.")
        self.assertFalse(location.availible)
