import csv
import json
from copy import deepcopy
from io import StringIO
from unittest.mock import PropertyMock, patch

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from .forms import SchoolsForm, SchedForm, suggest_activity_group_count
from .views import (
    SchedDetail,
    SchedList,
    build_generation_collapse_explanation,
    build_localized_failure_explanations,
    schedule_csv_export,
)
from .school_accounting import (
    calculate_school_slot_accounting,
    school_slot_accounting_summary,
    school_validation_slot_blocks,
)
from .schedule_operations import (
    apply_holding_reassignment_proposal,
    apply_move_proposal,
    apply_persisted_overrides,
    build_schedule_blocks,
    diagnose_sched_data_structure,
    evaluate_move_proposal_for_save,
    iter_schedule_blocks,
    normalize_sched_data_structure,
    persist_manual_move,
    repair_malformed_sched_data,
    repair_sched_data_structure,
    validate_schedule_blocks,
    verify_move_proposal_source,
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
    summarize_generation_completion,
)


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



class ScheduleOperationsTests(TestCase):
    def test_build_schedule_blocks_creates_metadata_for_resolvable_activity(self):
        activity = Course.objects.create(course_name="Adapter Activity", abriviation="AA", course_len=1)

        rows = build_schedule_blocks({
            "ags": ["Adapter School 0"],
            "mon_pm1": [activity.course_name],
        })

        block = rows[0]["cells"][0]
        self.assertEqual(block, {
            "block_id": "0:mon_pm1",
            "group_index": 0,
            "group_label": "Adapter School 0",
            "slot_key": "mon_pm1",
            "slot_label": "PM1",
            "raw_value": "Adapter Activity",
            "display_value": "Adapter Activity",
            "activity_id": activity.id,
            "activity_length": 1,
            "activity_abbreviation": "AA",
            "is_activity": True,
            "is_empty": False,
            "is_unavailable": False,
            "occurrence_id": "occurrence:0:mon_pm1",
            "occurrence_length": 1,
            "occurrence_position": 1,
            "is_multi_block": False,
            "is_proposed_source": False,
            "is_proposed_target": False,
            "proposed_from_block_id": None,
            "proposed_to_block_id": None,
            "is_persisted_override": False,
            "override_status": None,
            "override_source": None,
            "replay_conflicts": [],
            "has_overlap": False,
            "overlapping_blocks": [],
            "conflicts": [],
        })
        self.assertEqual(rows[0]["ag"], "Adapter School 0")
        self.assertEqual(len(rows[0]["cells"]), 20)

    def test_build_schedule_blocks_preserves_empty_and_unavailable_display_values(self):
        rows = build_schedule_blocks({
            "ags": ["Adapter School 0"],
            "mon_pm1": ["empty"],
            "mon_pm2": ["g_box"],
        })

        empty_block, unavailable_block = rows[0]["cells"][:2]
        self.assertEqual(empty_block["raw_value"], "empty")
        self.assertEqual(empty_block["display_value"], "****")
        self.assertTrue(empty_block["is_empty"])
        self.assertFalse(empty_block["is_activity"])
        self.assertIsNone(empty_block["activity_id"])
        self.assertIsNone(empty_block["activity_length"])
        self.assertEqual(unavailable_block["raw_value"], "g_box")
        self.assertEqual(unavailable_block["display_value"], "/////")
        self.assertTrue(unavailable_block["is_unavailable"])
        self.assertFalse(unavailable_block["is_activity"])
        self.assertIsNone(unavailable_block["activity_id"])
        self.assertIsNone(empty_block["occurrence_id"])
        self.assertIsNone(empty_block["occurrence_length"])
        self.assertIsNone(empty_block["occurrence_position"])
        self.assertFalse(empty_block["is_multi_block"])

    def test_one_block_activities_remain_independent_occurrences(self):
        activity = Course.objects.create(course_name="Repeated One Block", abriviation="R1B", course_len=1)

        rows = build_schedule_blocks({
            "ags": ["Adapter School 0"],
            "mon_pm1": [activity.course_name],
            "mon_pm2": [activity.course_name],
        })

        first_block, second_block = rows[0]["cells"][:2]
        self.assertEqual(first_block["occurrence_id"], "occurrence:0:mon_pm1")
        self.assertEqual(second_block["occurrence_id"], "occurrence:0:mon_pm2")
        self.assertNotEqual(first_block["occurrence_id"], second_block["occurrence_id"])
        self.assertFalse(first_block["is_multi_block"])
        self.assertFalse(second_block["is_multi_block"])

    def test_two_block_activity_pair_shares_occurrence_identity(self):
        activity = Course.objects.create(course_name="Grouped Two Block", abriviation="G2B", course_len=2)

        rows = build_schedule_blocks({
            "ags": ["Adapter School 0"],
            "tue_am1": [activity.course_name],
            "tue_am2": [activity.course_name],
        })

        first_block, second_block = rows[0]["cells"][3:5]
        self.assertEqual(first_block["occurrence_id"], "occurrence:0:tue_am1")
        self.assertEqual(second_block["occurrence_id"], first_block["occurrence_id"])
        self.assertEqual(first_block["occurrence_length"], 2)
        self.assertEqual(second_block["occurrence_length"], 2)
        self.assertEqual(first_block["occurrence_position"], 1)
        self.assertEqual(second_block["occurrence_position"], 2)
        self.assertTrue(first_block["is_multi_block"])
        self.assertTrue(second_block["is_multi_block"])

    def test_unrelated_adjacent_activities_are_not_grouped(self):
        first_activity = Course.objects.create(course_name="First Two Block", abriviation="F2B", course_len=2)
        second_activity = Course.objects.create(course_name="Second Two Block", abriviation="S2B", course_len=2)

        rows = build_schedule_blocks({
            "ags": ["Adapter School 0"],
            "tue_am1": [first_activity.course_name],
            "tue_am2": [second_activity.course_name],
        })

        first_block, second_block = rows[0]["cells"][3:5]
        self.assertNotEqual(first_block["occurrence_id"], second_block["occurrence_id"])
        self.assertFalse(first_block["is_multi_block"])
        self.assertFalse(second_block["is_multi_block"])


class ScheduleValidationTests(TestCase):
    def conflict_types(self, block):
        return {conflict["type"] for conflict in block["conflicts"]}

    def test_valid_schedule_blocks_remain_conflict_free(self):
        daytime = Course.objects.create(course_name="Valid Daytime", abriviation="VD", course_len=1)
        night = Course.objects.create(course_name="Valid Night", abriviation="VN", course_len=0)
        two_block = Course.objects.create(course_name="Valid Two Block", abriviation="V2", course_len=2)
        blocks = build_schedule_blocks({
            "ags": ["Validation School 0"],
            "mon_pm1": [daytime.course_name],
            "mon_night": [night.course_name],
            "tue_am1": [two_block.course_name],
            "tue_am2": [two_block.course_name],
        })

        conflict_summaries = validate_schedule_blocks(blocks)

        self.assertEqual(conflict_summaries, [])
        self.assertTrue(all(not block["conflicts"] for block in blocks[0]["cells"]))

    def test_invalid_daytime_and_night_placements_attach_structured_conflicts(self):
        daytime = Course.objects.create(course_name="Misplaced Daytime", abriviation="MD", course_len=1)
        night = Course.objects.create(course_name="Misplaced Night", abriviation="MN", course_len=0)
        blocks = build_schedule_blocks({
            "ags": ["Validation School 0"],
            "mon_pm1": [night.course_name],
            "mon_night": [daytime.course_name],
        })

        conflict_summaries = validate_schedule_blocks(blocks)
        daytime_slot, night_slot = blocks[0]["cells"][0], blocks[0]["cells"][2]

        self.assertEqual([conflict["type"] for conflict in conflict_summaries], [
            "invalid_time_slot",
            "invalid_time_slot",
        ])
        self.assertEqual(self.conflict_types(daytime_slot), {"invalid_time_slot"})
        self.assertEqual(self.conflict_types(night_slot), {"invalid_time_slot"})
        for conflict in conflict_summaries:
            self.assertEqual(conflict["severity"], "error")
            self.assertTrue(conflict["message"])
            self.assertEqual(len(conflict["related_block_ids"]), 1)

    def test_missing_two_block_pair_attaches_broken_multi_block_conflict(self):
        activity = Course.objects.create(course_name="Broken Two Block", abriviation="B2", course_len=2)
        blocks = build_schedule_blocks({
            "ags": ["Validation School 0"],
            "tue_am1": [activity.course_name],
        })

        conflict_summaries = validate_schedule_blocks(blocks)
        block = blocks[0]["cells"][3]

        self.assertEqual(self.conflict_types(block), {"broken_multi_block"})
        self.assertEqual(conflict_summaries[0]["type"], "broken_multi_block")
        self.assertEqual(conflict_summaries[0]["related_block_ids"], [block["block_id"]])

    def test_mismatched_multi_block_metadata_attaches_conflict_to_both_blocks(self):
        activity = Course.objects.create(course_name="Mismatched Two Block", abriviation="M2", course_len=2)
        blocks = build_schedule_blocks({
            "ags": ["Validation School 0"],
            "tue_am1": [activity.course_name],
            "tue_am2": [activity.course_name],
        })
        first_block, second_block = blocks[0]["cells"][3:5]
        second_block["occurrence_position"] = 1

        conflict_summaries = validate_schedule_blocks(blocks)

        self.assertEqual(len(conflict_summaries), 1)
        self.assertEqual(conflict_summaries[0]["type"], "broken_multi_block")
        self.assertEqual(self.conflict_types(first_block), {"broken_multi_block"})
        self.assertEqual(self.conflict_types(second_block), {"broken_multi_block"})

    def test_non_adjacent_multi_block_occurrence_attaches_conflict_to_both_blocks(self):
        activity = Course.objects.create(course_name="Nonadjacent Two Block", abriviation="N2", course_len=2)
        blocks = build_schedule_blocks({
            "ags": ["Validation School 0"],
            "tue_am1": [activity.course_name],
            "tue_pm2": [activity.course_name],
        })
        first_block, second_block = blocks[0]["cells"][3], blocks[0]["cells"][6]
        second_block.update({
            "occurrence_id": first_block["occurrence_id"],
            "occurrence_length": 2,
            "occurrence_position": 2,
            "is_multi_block": True,
        })
        first_block.update({
            "occurrence_length": 2,
            "occurrence_position": 1,
            "is_multi_block": True,
        })

        conflict_summaries = validate_schedule_blocks(blocks)

        self.assertEqual(len(conflict_summaries), 1)
        self.assertEqual(conflict_summaries[0]["type"], "broken_multi_block")
        self.assertEqual(self.conflict_types(first_block), {"broken_multi_block"})
        self.assertEqual(self.conflict_types(second_block), {"broken_multi_block"})

    def test_duplicate_group_slot_detected_when_normalized_blocks_contain_duplicate(self):
        activity = Course.objects.create(course_name="Duplicate Activity", abriviation="DA", course_len=1)
        blocks = build_schedule_blocks({
            "ags": ["Validation School 0"],
            "mon_pm1": [activity.course_name],
        })
        original_block = blocks[0]["cells"][0]
        duplicate_block = original_block.copy()
        duplicate_block["block_id"] = "duplicate:0:mon_pm1"
        duplicate_block["conflicts"] = []
        blocks[0]["cells"].append(duplicate_block)

        conflict_summaries = validate_schedule_blocks(blocks)

        duplicate_conflict = next(
            conflict for conflict in conflict_summaries if conflict["type"] == "duplicate_group_slot"
        )
        self.assertEqual(
            duplicate_conflict["related_block_ids"],
            [original_block["block_id"], duplicate_block["block_id"]],
        )
        self.assertIn("duplicate_group_slot", self.conflict_types(original_block))
        self.assertIn("duplicate_group_slot", self.conflict_types(duplicate_block))

    def test_same_slot_in_different_groups_is_not_duplicate_occupancy(self):
        first = Course.objects.create(course_name="Validation Group One", abriviation="VG1", course_len=1)
        second = Course.objects.create(course_name="Validation Group Two", abriviation="VG2", course_len=1)
        blocks = build_schedule_blocks({
            "ags": ["Validation School 0", "Validation School 1"],
            "mon_pm1": [first.course_name, second.course_name],
        })

        conflicts = validate_schedule_blocks(blocks)

        self.assertNotIn("duplicate_group_slot", {conflict["type"] for conflict in conflicts})
        self.assertEqual(blocks[0]["cells"][0]["group_index"], 0)
        self.assertEqual(blocks[1]["cells"][0]["group_index"], 1)


class ScheduleMoveProposalTests(TestCase):
    def test_valid_one_block_proposal_moves_activity_in_memory(self):
        activity = Course.objects.create(course_name="Proposal Activity", abriviation="PA", course_len=1)
        blocks = build_schedule_blocks({
            "ags": ["Proposal School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["empty"],
        })

        result = apply_move_proposal(blocks, {
            "source_block_id": "0:mon_pm1",
            "source_activity_id": activity.id,
            "source_activity_name": activity.course_name,
            "source_occurrence_id": "occurrence:0:mon_pm1",
            "target_slot_key": "tue_am1",
            "target_group_index": 0,
        })
        source, target = blocks[0]["cells"][0], blocks[0]["cells"][3]

        self.assertTrue(result["applied"])
        self.assertEqual(source["raw_value"], "empty")
        self.assertTrue(source["is_empty"])
        self.assertTrue(source["is_proposed_source"])
        self.assertEqual(source["proposed_to_block_id"], target["block_id"])
        self.assertEqual(target["raw_value"], activity.course_name)
        self.assertTrue(target["is_activity"])
        self.assertTrue(target["is_proposed_target"])
        self.assertEqual(target["proposed_from_block_id"], source["block_id"])

    def test_invalid_target_slot_rejects_proposal_without_mutating_blocks(self):
        activity = Course.objects.create(course_name="Invalid Target Activity", abriviation="ITA", course_len=1)
        blocks = build_schedule_blocks({
            "ags": ["Proposal School 0"],
            "mon_pm1": [activity.course_name],
        })
        source_before = blocks[0]["cells"][0].copy()

        result = apply_move_proposal(blocks, {
            "source_block_id": "0:mon_pm1",
            "source_activity_id": activity.id,
            "source_activity_name": activity.course_name,
            "source_occurrence_id": "occurrence:0:mon_pm1",
            "target_slot_key": "invalid_slot",
            "target_group_index": 0,
        })

        self.assertFalse(result["applied"])
        self.assertEqual(result["error"], "invalid_target_slot")
        self.assertEqual(blocks[0]["cells"][0], source_before)

    def test_multi_block_proposal_moves_whole_occurrence(self):
        activity = Course.objects.create(course_name="Proposal Two Block", abriviation="P2", course_len=2)
        blocks = build_schedule_blocks({
            "ags": ["Proposal School 0"],
            "tue_am1": [activity.course_name],
            "tue_am2": [activity.course_name],
            "wed_am1": ["empty"],
            "wed_am2": ["empty"],
        })

        result = apply_move_proposal(blocks, {
            "source_block_id": "0:tue_am1",
            "source_activity_id": activity.id,
            "source_activity_name": activity.course_name,
            "source_occurrence_id": "occurrence:0:tue_am1",
            "target_slot_key": "wed_am1",
            "target_group_index": 0,
        })

        self.assertTrue(result["applied"])
        self.assertEqual(result["move_type"], "occurrence")
        self.assertEqual(result["occurrence_length"], 2)
        self.assertEqual(blocks[0]["cells"][3]["raw_value"], "empty")
        self.assertEqual(blocks[0]["cells"][4]["raw_value"], "empty")
        self.assertEqual(blocks[0]["cells"][8]["raw_value"], activity.course_name)
        self.assertEqual(blocks[0]["cells"][9]["raw_value"], activity.course_name)
        self.assertEqual(blocks[0]["cells"][8]["occurrence_id"], blocks[0]["cells"][9]["occurrence_id"])
        self.assertTrue(blocks[0]["cells"][8]["is_multi_block"])

    def test_occupied_target_proposal_defaults_to_displacement_preview(self):
        source_activity = Course.objects.create(course_name="Overlap Source", abriviation="OS", course_len=1)
        target_activity = Course.objects.create(course_name="Overlap Target", abriviation="OT", course_len=1)
        blocks = build_schedule_blocks({
            "ags": ["Proposal School 0"],
            "mon_pm1": [source_activity.course_name],
            "mon_pm2": [target_activity.course_name],
        })

        result = apply_move_proposal(blocks, {
            "source_block_id": "0:mon_pm1",
            "source_activity_id": source_activity.id,
            "source_activity_name": source_activity.course_name,
            "source_occurrence_id": "occurrence:0:mon_pm1",
            "target_slot_key": "mon_pm2",
            "target_group_index": 0,
        })
        conflicts = validate_schedule_blocks(blocks)
        target = blocks[0]["cells"][1]
        holding_item = result["proposal_holding_area"][0]

        self.assertTrue(result["applied"])
        self.assertTrue(result["target_was_occupied"])
        self.assertEqual(target["raw_value"], source_activity.course_name)
        self.assertEqual(target["overlapping_blocks"], [])
        self.assertEqual(holding_item["activity_name"], target_activity.course_name)
        self.assertEqual(holding_item["origin_slot_key"], "mon_pm2")
        self.assertNotIn("duplicate_group_slot", {conflict["type"] for conflict in conflicts})

    def test_explicit_overlap_target_proposal_keeps_both_activities_and_creates_duplicate_conflict(self):
        source_activity = Course.objects.create(course_name="Overlap Source Explicit", abriviation="OSE", course_len=1)
        target_activity = Course.objects.create(course_name="Overlap Target Explicit", abriviation="OTE", course_len=1)
        blocks = build_schedule_blocks({
            "ags": ["Proposal School 0"],
            "mon_pm1": [source_activity.course_name],
            "mon_pm2": [target_activity.course_name],
        })

        result = apply_move_proposal(blocks, {
            "source_block_id": "0:mon_pm1",
            "source_activity_id": source_activity.id,
            "source_activity_name": source_activity.course_name,
            "source_occurrence_id": "occurrence:0:mon_pm1",
            "target_slot_key": "mon_pm2",
            "target_group_index": 0,
            "action_type": "overlap_move",
        })
        conflicts = validate_schedule_blocks(blocks)
        target = blocks[0]["cells"][1]
        overlap = target["overlapping_blocks"][0]

        self.assertTrue(result["applied"])
        self.assertEqual(target["raw_value"], target_activity.course_name)
        self.assertEqual(overlap["raw_value"], source_activity.course_name)
        duplicate = next(conflict for conflict in conflicts if conflict["type"] == "duplicate_group_slot")
        self.assertEqual(duplicate["severity"], "warning")
        self.assertEqual(duplicate["related_block_ids"], [target["block_id"], overlap["block_id"]])

    def test_non_first_row_proposal_uses_composite_group_slot_target(self):
        first_row = Course.objects.create(course_name="First Row Control", abriviation="FRC", course_len=1)
        second_row = Course.objects.create(course_name="Second Row Move", abriviation="SRM", course_len=1)
        blocks = build_schedule_blocks({
            "ags": ["Proposal School 0", "Proposal School 1"],
            "mon_pm1": [first_row.course_name, second_row.course_name],
            "tue_am1": ["empty", "empty"],
        })

        result = apply_move_proposal(blocks, {
            "source_block_id": "1:mon_pm1",
            "source_activity_id": second_row.id,
            "source_activity_name": second_row.course_name,
            "source_occurrence_id": "occurrence:1:mon_pm1",
            "source_group_index": 1,
            "source_slot_key": "mon_pm1",
            "target_slot_key": "tue_am1",
            "target_group_index": 1,
        })

        self.assertTrue(result["applied"])
        self.assertEqual(result["source_group_index"], 1)
        self.assertEqual(result["target_group_index"], 1)
        self.assertEqual(result["target_block_id"], "1:tue_am1")
        self.assertEqual(blocks[0]["cells"][0]["raw_value"], first_row.course_name)
        self.assertEqual(blocks[0]["cells"][3]["raw_value"], "empty")
        self.assertEqual(blocks[1]["cells"][0]["raw_value"], "empty")
        self.assertEqual(blocks[1]["cells"][3]["raw_value"], second_row.course_name)

    def test_source_group_identity_mismatch_rejects_proposal(self):
        activity = Course.objects.create(course_name="Group Identity Check", abriviation="GIC", course_len=1)
        blocks = build_schedule_blocks({
            "ags": ["Proposal School 0", "Proposal School 1"],
            "mon_pm1": ["empty", activity.course_name],
            "tue_am1": ["empty", "empty"],
        })

        result = apply_move_proposal(blocks, {
            "source_block_id": "1:mon_pm1",
            "source_activity_id": activity.id,
            "source_activity_name": activity.course_name,
            "source_occurrence_id": "occurrence:1:mon_pm1",
            "source_group_index": 0,
            "source_slot_key": "mon_pm1",
            "target_slot_key": "tue_am1",
            "target_group_index": 1,
        })

        self.assertFalse(result["applied"])
        self.assertEqual(result["error"], "source_identity_mismatch")
        self.assertEqual(blocks[1]["cells"][0]["raw_value"], activity.course_name)


class PersistedOverrideReplayTests(TestCase):
    def setUp(self):
        self.activity = Course.objects.create(
            course_name="Replay Activity",
            abriviation="RA",
            course_len=1,
        )
        self.schedule = TheSched.objects.create(
            sched_name="Replay Schedule",
            sched_data={"version": 1, "manual_moves": []},
        )

    def move_record(
        self,
        *,
        source_block_id="0:mon_pm1",
        source_group_index=0,
        source_slot_key="mon_pm1",
        target_group_index=0,
        target_slot_key="tue_am1",
        activity=None,
        occurrence_id=None,
        status="active",
        move_type="single_block",
        action_type=None,
    ):
        activity = activity or self.activity
        record = {
            "source_block_id": source_block_id,
            "source_activity_id": activity.id,
            "source_activity_name": activity.course_name,
            "source_occurrence_id": occurrence_id or f"occurrence:{source_block_id}",
            "source_group_index": source_group_index,
            "source_slot_key": source_slot_key,
            "target_group_index": target_group_index,
            "target_slot_key": target_slot_key,
            "move_type": move_type,
            "created_at": "2026-06-14T12:00:00Z",
            "status": status,
        }
        if action_type is not None:
            record["action_type"] = action_type
        return record

    def holding_record(
        self,
        *,
        holding_id="holding:override:0:0:mon_pm2:1",
        target_group_index=0,
        target_slot_key="tue_am1",
        activity=None,
        occurrence_id="occurrence:0:mon_pm2",
        action_type="overlap_move",
        status="active",
    ):
        activity = activity or self.activity
        return {
            "source_kind": "holding",
            "source_holding_id": holding_id,
            "source_activity_id": activity.id,
            "source_activity_name": activity.course_name,
            "source_occurrence_id": occurrence_id,
            "target_group_index": target_group_index,
            "target_slot_key": target_slot_key,
            "move_type": "single_block",
            "action_type": action_type,
            "created_at": "2026-06-14T12:01:00Z",
            "status": status,
        }

    def test_replays_single_block_override_without_mutating_generated_schedule(self):
        generated = {
            "ags": ["Replay School 0"],
            "mon_pm1": [self.activity.course_name],
            "tue_am1": ["empty"],
        }
        generated_before = deepcopy(generated)
        blocks = build_schedule_blocks(generated)
        self.schedule.sched_data["manual_moves"] = [self.move_record()]

        result = apply_persisted_overrides(self.schedule, blocks)
        source, target = blocks[0]["cells"][0], blocks[0]["cells"][3]

        self.assertEqual(generated, generated_before)
        self.assertEqual(len(result["applied_overrides"]), 1)
        self.assertTrue(source["is_empty"])
        self.assertTrue(source["is_persisted_override"])
        self.assertEqual(source["override_status"], "applied_source")
        self.assertEqual(target["raw_value"], self.activity.course_name)
        self.assertTrue(target["is_persisted_override"])
        self.assertEqual(target["override_status"], "applied")

    def test_replays_overlap_and_validation_detects_duplicate_occupancy(self):
        target_activity = Course.objects.create(
            course_name="Replay Occupied Target",
            abriviation="ROT",
            course_len=1,
        )
        blocks = build_schedule_blocks({
            "ags": ["Replay School 0"],
            "mon_pm1": [self.activity.course_name],
            "mon_pm2": [target_activity.course_name],
        })
        self.schedule.sched_data["manual_moves"] = [
            self.move_record(target_slot_key="mon_pm2", action_type="overlap_move")
        ]

        replay_result = apply_persisted_overrides(self.schedule, blocks)
        conflicts = validate_schedule_blocks(blocks)
        target = blocks[0]["cells"][1]
        overlap = target["overlapping_blocks"][0]

        self.assertTrue(replay_result["applied_overrides"][0]["target_was_occupied"])
        self.assertEqual(target["raw_value"], target_activity.course_name)
        self.assertEqual(overlap["raw_value"], self.activity.course_name)
        self.assertTrue(overlap["is_persisted_override"])
        self.assertEqual(overlap["override_status"], "applied")
        self.assertIn("duplicate_group_slot", {conflict["type"] for conflict in conflicts})
        self.assertEqual(replay_result["applied_overrides"][0]["action_type"], "overlap_move")

    def test_legacy_record_without_action_type_defaults_to_overlap(self):
        target_activity = Course.objects.create(
            course_name="Legacy Overlap Target",
            abriviation="LOT",
            course_len=1,
        )
        blocks = build_schedule_blocks({
            "ags": ["Replay School 0"],
            "mon_pm1": [self.activity.course_name],
            "mon_pm2": [target_activity.course_name],
        })
        legacy_move = self.move_record(target_slot_key="mon_pm2")
        self.assertNotIn("action_type", legacy_move)
        self.schedule.sched_data["manual_moves"] = [legacy_move]

        result = apply_persisted_overrides(self.schedule, blocks, replay_mode="displacement")
        target = blocks[0]["cells"][1]

        self.assertEqual(result["holding_area"], [])
        self.assertEqual(target["activity_id"], target_activity.id)
        self.assertEqual(target["overlapping_blocks"][0]["activity_id"], self.activity.id)
        self.assertEqual(result["applied_overrides"][0]["action_type"], "overlap_move")

    def test_replays_multiple_ordered_overrides(self):
        second_activity = Course.objects.create(
            course_name="Second Replay Activity",
            abriviation="SRA",
            course_len=1,
        )
        blocks = build_schedule_blocks({
            "ags": ["Replay School 0", "Replay School 1"],
            "mon_pm1": [self.activity.course_name, second_activity.course_name],
            "tue_am1": ["empty", "empty"],
        })
        self.schedule.sched_data["manual_moves"] = [
            self.move_record(),
            self.move_record(
                source_block_id="1:mon_pm1",
                source_group_index=1,
                target_group_index=1,
                activity=second_activity,
            ),
        ]

        result = apply_persisted_overrides(self.schedule, blocks)

        self.assertEqual(len(result["applied_overrides"]), 2)
        self.assertEqual(blocks[0]["cells"][3]["raw_value"], self.activity.course_name)
        self.assertEqual(blocks[1]["cells"][3]["raw_value"], second_activity.course_name)

    def test_stale_and_invalid_active_overrides_fail_without_corrupting_blocks(self):
        blocks = build_schedule_blocks({
            "ags": ["Replay School 0"],
            "mon_pm1": [self.activity.course_name],
            "tue_am1": ["empty"],
        })
        blocks_before = deepcopy(blocks)
        stale = self.move_record()
        stale["source_activity_name"] = "Changed Activity"
        self.schedule.sched_data["manual_moves"] = [
            stale,
            {"status": "active", "move_type": "single_block"},
            "invalid",
        ]

        result = apply_persisted_overrides(self.schedule, blocks)

        self.assertEqual(len(result["applied_overrides"]), 0)
        self.assertEqual(len(result["replay_conflicts"]), 3)
        self.assertEqual(blocks[0]["cells"][0]["raw_value"], blocks_before[0]["cells"][0]["raw_value"])
        self.assertEqual(blocks[0]["cells"][3]["raw_value"], blocks_before[0]["cells"][3]["raw_value"])
        self.assertTrue(all(
            conflict["type"] == "persisted_override_replay"
            for conflict in result["replay_conflicts"]
        ))

    def test_inactive_and_unsupported_overrides_are_not_applied(self):
        blocks = build_schedule_blocks({
            "ags": ["Replay School 0"],
            "mon_pm1": [self.activity.course_name],
            "tue_am1": ["empty"],
        })
        self.schedule.sched_data["manual_moves"] = [
            self.move_record(status="inactive"),
            self.move_record(move_type="multi_block"),
        ]

        result = apply_persisted_overrides(self.schedule, blocks)

        self.assertEqual(result["applied_overrides"], [])
        self.assertEqual(result["ignored_overrides"][0]["reason"], "inactive")
        self.assertEqual(len(result["replay_conflicts"]), 1)
        self.assertEqual(blocks[0]["cells"][0]["raw_value"], self.activity.course_name)

    def test_chained_overlap_then_move_primary_preserves_both_activities(self):
        displaced = Course.objects.create(
            course_name="Chained Displaced Activity",
            abriviation="CDA",
            course_len=1,
        )
        blocks = build_schedule_blocks({
            "ags": ["Replay School 0"],
            "mon_pm1": [self.activity.course_name],
            "mon_pm2": [displaced.course_name],
            "tue_am1": ["empty"],
        })
        self.schedule.sched_data["manual_moves"] = [
            self.move_record(target_slot_key="mon_pm2"),
            self.move_record(
                source_block_id="0:mon_pm2",
                source_slot_key="mon_pm2",
                target_slot_key="tue_am1",
                activity=displaced,
            ),
        ]
        stored_before = deepcopy(self.schedule.sched_data)

        result = apply_persisted_overrides(self.schedule, blocks)
        visible_activities = [
            block["raw_value"]
            for block in iter_schedule_blocks(blocks)
            if block["is_activity"]
        ]

        self.assertEqual(len(result["applied_overrides"]), 2)
        self.assertEqual(blocks[0]["cells"][1]["raw_value"], self.activity.course_name)
        self.assertEqual(blocks[0]["cells"][3]["raw_value"], displaced.course_name)
        self.assertEqual(visible_activities.count(self.activity.course_name), 1)
        self.assertEqual(visible_activities.count(displaced.course_name), 1)
        self.assertEqual(self.schedule.sched_data, stored_before)

    def test_moving_displaced_overlap_activity_elsewhere_preserves_primary(self):
        primary = Course.objects.create(
            course_name="Chained Primary Activity",
            abriviation="CPA",
            course_len=1,
        )
        blocks = build_schedule_blocks({
            "ags": ["Replay School 0"],
            "mon_pm1": [self.activity.course_name],
            "mon_pm2": [primary.course_name],
            "tue_am1": ["empty"],
        })
        overlap_block_id = "overlap:persisted:0:0:mon_pm2:0:mon_pm1"
        self.schedule.sched_data["manual_moves"] = [
            self.move_record(target_slot_key="mon_pm2"),
            self.move_record(
                source_block_id=overlap_block_id,
                source_slot_key="mon_pm2",
                target_slot_key="tue_am1",
                occurrence_id="occurrence:overlap:persisted:0:0:mon_pm2",
            ),
        ]

        result = apply_persisted_overrides(self.schedule, blocks)
        visible_activities = [
            block["raw_value"]
            for block in iter_schedule_blocks(blocks)
            if block["is_activity"]
        ]

        self.assertEqual(len(result["applied_overrides"]), 2)
        self.assertEqual(blocks[0]["cells"][1]["raw_value"], primary.course_name)
        self.assertEqual(blocks[0]["cells"][3]["raw_value"], self.activity.course_name)
        self.assertEqual(visible_activities.count(self.activity.course_name), 1)
        self.assertEqual(visible_activities.count(primary.course_name), 1)

    def test_superseded_stale_and_failed_replay_statuses_are_excluded(self):
        blocks = build_schedule_blocks({
            "ags": ["Replay School 0"],
            "mon_pm1": [self.activity.course_name],
            "tue_am1": ["empty"],
        })
        self.schedule.sched_data["manual_moves"] = [
            self.move_record(status="superseded"),
            self.move_record(status="stale"),
            self.move_record(status="failed_replay"),
        ]

        result = apply_persisted_overrides(self.schedule, blocks)

        self.assertEqual(result["applied_overrides"], [])
        self.assertEqual(
            [ignored["reason"] for ignored in result["ignored_overrides"]],
            ["superseded", "stale", "failed_replay"],
        )
        self.assertEqual(blocks[0]["cells"][0]["raw_value"], self.activity.course_name)

    def test_active_stale_and_failed_replay_overrides_report_in_memory_status(self):
        blocks = build_schedule_blocks({
            "ags": ["Replay School 0"],
            "mon_pm1": [self.activity.course_name],
            "tue_am1": ["empty"],
        })
        stale = self.move_record()
        stale["source_activity_name"] = "Changed Activity"
        failed = self.move_record(target_slot_key="invalid_slot")
        self.schedule.sched_data["manual_moves"] = [stale, failed]
        stored_before = deepcopy(self.schedule.sched_data)

        result = apply_persisted_overrides(self.schedule, blocks)

        self.assertEqual(
            [conflict["override_status"] for conflict in result["replay_conflicts"]],
            ["stale", "failed_replay"],
        )
        self.assertEqual(
            [ignored["reason"] for ignored in result["ignored_overrides"]],
            ["stale", "failed_replay"],
        )
        self.assertEqual(self.schedule.sched_data, stored_before)

    def test_displacement_mode_moves_occupied_target_into_non_grid_holding_area(self):
        displaced = Course.objects.create(
            course_name="Displaced Holding Activity",
            abriviation="DHA",
            course_len=1,
        )
        blocks = build_schedule_blocks({
            "ags": ["Replay School 0"],
            "mon_pm1": [self.activity.course_name],
            "mon_pm2": [displaced.course_name],
        })
        self.schedule.sched_data["manual_moves"] = [
            self.move_record(target_slot_key="mon_pm2", action_type="displacement_move"),
        ]
        stored_before = deepcopy(self.schedule.sched_data)

        result = apply_persisted_overrides(self.schedule, blocks, replay_mode="displacement")
        conflicts = validate_schedule_blocks(blocks)
        target = blocks[0]["cells"][1]
        holding_item = result["holding_area"][0]

        self.assertEqual(result["replay_mode"], "displacement")
        self.assertEqual(target["raw_value"], self.activity.course_name)
        self.assertEqual(target["activity_id"], self.activity.id)
        self.assertEqual(target["overlapping_blocks"], [])
        self.assertFalse(target["has_overlap"])
        self.assertEqual(holding_item["activity_name"], displaced.course_name)
        self.assertEqual(holding_item["activity_id"], displaced.id)
        self.assertEqual(result["applied_overrides"][0]["moved_activity_id"], self.activity.id)
        self.assertEqual(result["applied_overrides"][0]["displaced_activity_ids"], [displaced.id])
        self.assertEqual(holding_item["holding_status"], "awaiting_assignment")
        self.assertTrue(holding_item["is_holding"])
        self.assertNotIn("slot_key", holding_item)
        self.assertEqual(holding_item["origin_slot_key"], "mon_pm2")
        self.assertEqual(blocks[0]["cells"][0]["raw_value"], "empty")
        self.assertTrue(blocks[0]["cells"][0]["is_empty"])
        self.assertNotIn("duplicate_group_slot", {conflict["type"] for conflict in conflicts})
        self.assertNotIn(holding_item, list(iter_schedule_blocks(blocks)))
        self.assertEqual(self.schedule.sched_data, stored_before)

    def test_displacement_replay_is_stable_from_fresh_generated_blocks(self):
        displaced = Course.objects.create(
            course_name="Reload Stable Displaced Activity",
            abriviation="RSDA",
            course_len=1,
        )
        generated_schedule = {
            "ags": ["Replay School 0"],
            "mon_pm1": [self.activity.course_name],
            "mon_pm2": [displaced.course_name],
        }
        self.schedule.sched_data["manual_moves"] = [
            self.move_record(target_slot_key="mon_pm2", action_type="displacement_move"),
        ]

        first_blocks = build_schedule_blocks(generated_schedule)
        first_result = apply_persisted_overrides(
            self.schedule,
            first_blocks,
            replay_mode="displacement",
        )
        reloaded_blocks = build_schedule_blocks(generated_schedule)
        reloaded_result = apply_persisted_overrides(
            self.schedule,
            reloaded_blocks,
            replay_mode="displacement",
        )

        self.assertEqual(first_blocks, reloaded_blocks)
        self.assertEqual(first_result, reloaded_result)
        self.assertEqual(reloaded_blocks[0]["cells"][0]["raw_value"], "empty")
        self.assertEqual(reloaded_blocks[0]["cells"][1]["activity_id"], self.activity.id)
        self.assertEqual(reloaded_result["holding_area"][0]["activity_id"], displaced.id)

    def test_holding_reassignment_consumes_holding_item_and_restores_grid_visibility(self):
        displaced = Course.objects.create(
            course_name="Reassigned Holding Activity",
            abriviation="RHA",
            course_len=1,
        )
        generated_schedule = {
            "ags": ["Replay School 0"],
            "mon_pm1": [self.activity.course_name],
            "mon_pm2": [displaced.course_name],
            "tue_am1": ["empty"],
        }
        self.schedule.sched_data["manual_moves"] = [
            self.move_record(target_slot_key="mon_pm2", action_type="displacement_move"),
            self.holding_record(activity=displaced, target_slot_key="tue_am1"),
        ]
        blocks = build_schedule_blocks(generated_schedule)

        result = apply_persisted_overrides(self.schedule, blocks)

        self.assertEqual(len(result["applied_overrides"]), 2)
        self.assertEqual(result["holding_area"], [])
        self.assertEqual(blocks[0]["cells"][1]["activity_id"], self.activity.id)
        self.assertEqual(blocks[0]["cells"][3]["activity_id"], displaced.id)
        self.assertEqual(
            [move.get("source_kind") for move in self.schedule.sched_data["manual_moves"]],
            [None, "holding"],
        )

    def test_persisted_multi_block_override_replays_whole_occurrence(self):
        activity = Course.objects.create(
            course_name="Persisted Two Block Replay",
            abriviation="PTBR",
            course_len=2,
        )
        generated_schedule = {
            "ags": ["Replay School 0"],
            "tue_am1": [activity.course_name],
            "tue_am2": [activity.course_name],
            "wed_am1": ["empty"],
            "wed_am2": ["empty"],
        }
        self.schedule.sched_data["manual_moves"] = [
            self.move_record(
                source_block_id="0:tue_am1",
                source_group_index=0,
                source_slot_key="tue_am1",
                target_group_index=0,
                target_slot_key="wed_am1",
                activity=activity,
                occurrence_id="occurrence:0:tue_am1",
                move_type="occurrence",
                action_type="displacement_move",
            ),
        ]
        blocks = build_schedule_blocks(generated_schedule)

        result = apply_persisted_overrides(self.schedule, blocks)

        self.assertEqual(len(result["applied_overrides"]), 1)
        self.assertTrue(blocks[0]["cells"][3]["is_empty"])
        self.assertTrue(blocks[0]["cells"][4]["is_empty"])
        self.assertEqual(blocks[0]["cells"][8]["activity_id"], activity.id)
        self.assertEqual(blocks[0]["cells"][9]["activity_id"], activity.id)
        self.assertEqual(blocks[0]["cells"][8]["occurrence_id"], blocks[0]["cells"][9]["occurrence_id"])

    def test_single_block_displacement_moves_entire_multi_block_target_to_holding(self):
        source = Course.objects.create(
            course_name="Single Displaces Multi",
            abriviation="SDM",
            course_len=1,
        )
        displaced = Course.objects.create(
            course_name="Whole Target Two Block",
            abriviation="WTTB",
            course_len=2,
        )
        generated_schedule = {
            "ags": ["Replay School 0"],
            "mon_pm1": [source.course_name],
            "tue_am1": [displaced.course_name],
            "tue_am2": [displaced.course_name],
        }
        self.schedule.sched_data["manual_moves"] = [
            self.move_record(
                source_block_id="0:mon_pm1",
                source_slot_key="mon_pm1",
                target_slot_key="tue_am1",
                activity=source,
                action_type="displacement_move",
            ),
        ]
        blocks = build_schedule_blocks(generated_schedule)

        result = apply_persisted_overrides(self.schedule, blocks)

        self.assertEqual(blocks[0]["cells"][3]["activity_id"], source.id)
        self.assertTrue(blocks[0]["cells"][4]["is_empty"])
        self.assertEqual(len(result["holding_area"]), 1)
        self.assertEqual(result["holding_area"][0]["activity_id"], displaced.id)
        self.assertEqual(result["holding_area"][0]["occurrence_length"], 2)
        self.assertEqual(result["holding_area"][0]["source_slot_keys"], ["tue_am1", "tue_am2"])

    def test_multi_block_holding_reassignment_restores_whole_occurrence(self):
        source = Course.objects.create(
            course_name="Holding Multi Source",
            abriviation="HMS",
            course_len=1,
        )
        displaced = Course.objects.create(
            course_name="Holding Multi Target",
            abriviation="HMT",
            course_len=2,
        )
        generated_schedule = {
            "ags": ["Replay School 0"],
            "mon_pm1": [source.course_name],
            "mon_pm2": ["empty"],
            "tue_am1": [displaced.course_name],
            "tue_am2": [displaced.course_name],
            "wed_am1": ["empty"],
            "wed_am2": ["empty"],
        }
        self.schedule.sched_data["manual_moves"] = [
            self.move_record(
                source_block_id="0:mon_pm1",
                source_slot_key="mon_pm1",
                target_slot_key="tue_am1",
                activity=source,
                action_type="displacement_move",
            ),
            {
                **self.holding_record(
                    holding_id="holding:override:0:0:tue_am1:1",
                    activity=displaced,
                    occurrence_id="occurrence:0:tue_am1",
                    target_slot_key="wed_am1",
                    action_type="displacement_move",
                ),
                "move_type": "occurrence",
                "occurrence_length": 2,
            },
        ]
        blocks = build_schedule_blocks(generated_schedule)

        result = apply_persisted_overrides(self.schedule, blocks)

        self.assertEqual(result["holding_area"], [])
        self.assertEqual(blocks[0]["cells"][8]["activity_id"], displaced.id)
        self.assertEqual(blocks[0]["cells"][9]["activity_id"], displaced.id)
        self.assertEqual(blocks[0]["cells"][8]["occurrence_id"], blocks[0]["cells"][9]["occurrence_id"])

    def test_holding_reassignment_into_occupied_target_can_displace_again(self):
        displaced = Course.objects.create(
            course_name="Reassigned Displacing Holding",
            abriviation="RDH",
            course_len=1,
        )
        next_displaced = Course.objects.create(
            course_name="Second Holding Target",
            abriviation="SHT",
            course_len=1,
        )
        generated_schedule = {
            "ags": ["Replay School 0"],
            "mon_pm1": [self.activity.course_name],
            "mon_pm2": [displaced.course_name],
            "tue_am1": [next_displaced.course_name],
        }
        self.schedule.sched_data["manual_moves"] = [
            self.move_record(target_slot_key="mon_pm2", action_type="displacement_move"),
            self.holding_record(
                activity=displaced,
                target_slot_key="tue_am1",
                action_type="displacement_move",
            ),
        ]
        blocks = build_schedule_blocks(generated_schedule)

        result = apply_persisted_overrides(self.schedule, blocks)

        self.assertEqual(blocks[0]["cells"][3]["activity_id"], displaced.id)
        self.assertEqual(
            [item["activity_id"] for item in result["holding_area"]],
            [next_displaced.id],
        )

    def test_stale_holding_reassignment_fails_without_consuming_unresolved_holding(self):
        displaced = Course.objects.create(
            course_name="Still Holding Activity",
            abriviation="SHA",
            course_len=1,
        )
        generated_schedule = {
            "ags": ["Replay School 0"],
            "mon_pm1": [self.activity.course_name],
            "mon_pm2": [displaced.course_name],
            "tue_am1": ["empty"],
        }
        self.schedule.sched_data["manual_moves"] = [
            self.move_record(target_slot_key="mon_pm2", action_type="displacement_move"),
            self.holding_record(
                holding_id="holding:missing",
                activity=displaced,
                target_slot_key="tue_am1",
            ),
        ]
        blocks = build_schedule_blocks(generated_schedule)

        result = apply_persisted_overrides(self.schedule, blocks)

        self.assertEqual(len(result["applied_overrides"]), 1)
        self.assertEqual(result["replay_conflicts"][0]["override_status"], "stale")
        self.assertEqual(result["holding_area"][0]["activity_id"], displaced.id)
        self.assertEqual(blocks[0]["cells"][3]["raw_value"], "empty")

    def test_holding_reassignment_proposal_removes_item_from_preview_holding_area(self):
        displaced = Course.objects.create(
            course_name="Proposal Holding Activity",
            abriviation="PHA",
            course_len=1,
        )
        generated_schedule = {
            "ags": ["Replay School 0"],
            "mon_pm1": [self.activity.course_name],
            "mon_pm2": [displaced.course_name],
            "tue_am1": ["empty"],
        }
        self.schedule.sched_data["manual_moves"] = [
            self.move_record(target_slot_key="mon_pm2", action_type="displacement_move"),
        ]
        blocks = build_schedule_blocks(generated_schedule)
        replay_result = apply_persisted_overrides(self.schedule, blocks)

        proposal_result = apply_holding_reassignment_proposal(
            blocks,
            replay_result["holding_area"],
            {
                "source_holding_id": "holding:override:0:0:mon_pm2:1",
                "source_activity_id": displaced.id,
                "source_activity_name": displaced.course_name,
                "source_occurrence_id": "occurrence:0:mon_pm2",
                "target_group_index": 0,
                "target_slot_key": "tue_am1",
                "action_type": "overlap_move",
            },
        )

        self.assertTrue(proposal_result["applied"])
        self.assertEqual(replay_result["holding_area"], [])
        self.assertEqual(blocks[0]["cells"][3]["activity_id"], displaced.id)

    def test_displacement_mode_preserves_all_existing_target_occupants_in_holding(self):
        primary = Course.objects.create(
            course_name="Holding Primary",
            abriviation="HP",
            course_len=1,
        )
        overlap = Course.objects.create(
            course_name="Holding Existing Overlap",
            abriviation="HEO",
            course_len=1,
        )
        blocks = build_schedule_blocks({
            "ags": ["Replay School 0"],
            "mon_pm1": [self.activity.course_name],
            "mon_pm2": [primary.course_name],
        })
        target = blocks[0]["cells"][1]
        target["overlapping_blocks"].append({
            **deepcopy(target),
            "block_id": "existing-overlap",
            "raw_value": overlap.course_name,
            "display_value": overlap.course_name,
            "activity_id": overlap.id,
            "overlapping_blocks": [],
            "has_overlap": False,
            "conflicts": [],
            "replay_conflicts": [],
        })
        target["has_overlap"] = True
        self.schedule.sched_data["manual_moves"] = [
            self.move_record(target_slot_key="mon_pm2", action_type="displacement_move"),
        ]

        result = apply_persisted_overrides(self.schedule, blocks, replay_mode="displacement")

        self.assertEqual(
            [item["activity_name"] for item in result["holding_area"]],
            [primary.course_name, overlap.course_name],
        )
        self.assertEqual(target["raw_value"], self.activity.course_name)
        self.assertEqual(target["overlapping_blocks"], [])

    def test_displacement_promotion_does_not_put_moved_overlap_in_holding(self):
        primary = Course.objects.create(
            course_name="Promotion Primary Target",
            abriviation="PPT",
            course_len=1,
        )
        blocks = build_schedule_blocks({
            "ags": ["Replay School 0"],
            "mon_pm2": [primary.course_name],
        })
        target = next(
            block
            for block in blocks[0]["cells"]
            if block["slot_key"] == "mon_pm2"
        )
        source = {
            **deepcopy(target),
            "block_id": "existing-overlap-source",
            "raw_value": self.activity.course_name,
            "display_value": self.activity.course_name,
            "activity_id": self.activity.id,
            "occurrence_id": "occurrence:existing-overlap-source",
            "overlapping_blocks": [],
            "has_overlap": False,
            "conflicts": [],
            "replay_conflicts": [],
        }
        target["overlapping_blocks"].append(source)
        target["has_overlap"] = True
        self.schedule.sched_data["manual_moves"] = [
            self.move_record(
                source_block_id=source["block_id"],
                source_slot_key="mon_pm2",
                target_slot_key="mon_pm2",
                occurrence_id=source["occurrence_id"],
                action_type="displacement_move",
            ),
        ]

        result = apply_persisted_overrides(self.schedule, blocks, replay_mode="displacement")

        self.assertEqual(target["activity_id"], self.activity.id)
        self.assertEqual(target["overlapping_blocks"], [])
        self.assertEqual(
            [item["activity_id"] for item in result["holding_area"]],
            [primary.id],
        )
        self.assertEqual(
            result["applied_overrides"][0]["displaced_activity_ids"],
            [primary.id],
        )
        self.assertNotIn(
            self.activity.id,
            [item["activity_id"] for item in result["holding_area"]],
        )

    def test_overlap_mode_remains_default_fallback(self):
        occupied = Course.objects.create(
            course_name="Fallback Overlap Target",
            abriviation="FOT",
            course_len=1,
        )
        blocks = build_schedule_blocks({
            "ags": ["Replay School 0"],
            "mon_pm1": [self.activity.course_name],
            "mon_pm2": [occupied.course_name],
        })
        self.schedule.sched_data["manual_moves"] = [
            self.move_record(target_slot_key="mon_pm2"),
        ]

        result = apply_persisted_overrides(self.schedule, blocks)

        self.assertEqual(result["replay_mode"], "overlap")
        self.assertEqual(result["holding_area"], [])
        self.assertEqual(blocks[0]["cells"][1]["raw_value"], occupied.course_name)
        self.assertEqual(
            blocks[0]["cells"][1]["overlapping_blocks"][0]["raw_value"],
            self.activity.course_name,
        )

    def test_mixed_explicit_overlap_and_displacement_history_replays_in_order(self):
        overlap_target = Course.objects.create(
            course_name="Mixed History Target",
            abriviation="MHT",
            course_len=1,
        )
        displacement_source = Course.objects.create(
            course_name="Mixed History Displacement Source",
            abriviation="MHDS",
            course_len=1,
        )
        blocks = build_schedule_blocks({
            "ags": ["Replay School 0"],
            "mon_pm1": [self.activity.course_name],
            "mon_pm2": [overlap_target.course_name],
            "tue_am1": [displacement_source.course_name],
        })
        self.schedule.sched_data["manual_moves"] = [
            self.move_record(target_slot_key="mon_pm2", action_type="overlap_move"),
            self.move_record(
                source_block_id="0:tue_am1",
                source_slot_key="tue_am1",
                target_slot_key="mon_pm2",
                activity=displacement_source,
                action_type="displacement_move",
            ),
        ]

        result = apply_persisted_overrides(self.schedule, blocks)
        target = blocks[0]["cells"][1]

        self.assertEqual([item["activity_id"] for item in result["holding_area"]], [
            overlap_target.id,
            self.activity.id,
        ])
        self.assertEqual(target["activity_id"], displacement_source.id)
        self.assertEqual(target["overlapping_blocks"], [])
        self.assertEqual(
            [applied["action_type"] for applied in result["applied_overrides"]],
            ["overlap_move", "displacement_move"],
        )

    def test_unknown_action_type_fails_safely_without_mutating_blocks(self):
        blocks = build_schedule_blocks({
            "ags": ["Replay School 0"],
            "mon_pm1": [self.activity.course_name],
            "tue_am1": ["empty"],
        })
        before = deepcopy(blocks)
        self.schedule.sched_data["manual_moves"] = [
            self.move_record(action_type="future_move_type"),
        ]

        result = apply_persisted_overrides(self.schedule, blocks)

        self.assertEqual(blocks, before)
        self.assertEqual(result["applied_overrides"], [])
        self.assertEqual(result["ignored_overrides"][0]["reason"], "failed_replay")
        self.assertIn("unsupported action type", result["replay_conflicts"][0]["message"])

    def test_unknown_replay_mode_is_rejected_without_mutating_blocks(self):
        blocks = build_schedule_blocks({
            "ags": ["Replay School 0"],
            "mon_pm1": [self.activity.course_name],
        })
        blocks_before = deepcopy(blocks)

        with self.assertRaisesMessage(ValueError, "Unsupported persisted override replay mode"):
            apply_persisted_overrides(self.schedule, blocks, replay_mode="unknown")

        self.assertEqual(blocks, blocks_before)

    def test_non_first_row_overlap_replay_preserves_row_identity(self):
        first_row = Course.objects.create(course_name="Replay First Row", abriviation="RFR", course_len=1)
        occupied = Course.objects.create(course_name="Replay Second Occupied", abriviation="RSO", course_len=1)
        blocks = build_schedule_blocks({
            "ags": ["Replay School 0", "Replay School 1"],
            "mon_pm1": [first_row.course_name, self.activity.course_name],
            "mon_pm2": ["empty", occupied.course_name],
        })
        self.schedule.sched_data["manual_moves"] = [
            self.move_record(
                source_block_id="1:mon_pm1",
                source_group_index=1,
                target_group_index=1,
                target_slot_key="mon_pm2",
            ),
        ]

        result = apply_persisted_overrides(self.schedule, blocks)

        self.assertEqual(len(result["applied_overrides"]), 1)
        self.assertEqual(blocks[0]["cells"][0]["raw_value"], first_row.course_name)
        self.assertEqual(blocks[0]["cells"][1]["raw_value"], "empty")
        self.assertEqual(blocks[1]["cells"][1]["raw_value"], occupied.course_name)
        overlap = blocks[1]["cells"][1]["overlapping_blocks"][0]
        self.assertEqual(overlap["raw_value"], self.activity.course_name)
        self.assertEqual(overlap["group_index"], 1)
        self.assertEqual(overlap["slot_key"], "mon_pm2")

    def test_non_first_row_displacement_and_holding_preserve_group_identity(self):
        first_row = Course.objects.create(course_name="Displacement First Row", abriviation="DFR", course_len=1)
        displaced = Course.objects.create(course_name="Displacement Second Row", abriviation="DSR", course_len=1)
        blocks = build_schedule_blocks({
            "ags": ["Replay School 0", "Replay School 1"],
            "mon_pm1": [first_row.course_name, self.activity.course_name],
            "mon_pm2": ["empty", displaced.course_name],
        })
        self.schedule.sched_data["manual_moves"] = [
            self.move_record(
                source_block_id="1:mon_pm1",
                source_group_index=1,
                target_group_index=1,
                target_slot_key="mon_pm2",
                action_type="displacement_move",
            ),
        ]

        result = apply_persisted_overrides(self.schedule, blocks, replay_mode="displacement")
        holding_item = result["holding_area"][0]

        self.assertEqual(blocks[0]["cells"][0]["raw_value"], first_row.course_name)
        self.assertEqual(blocks[0]["cells"][1]["raw_value"], "empty")
        self.assertEqual(blocks[1]["cells"][1]["raw_value"], self.activity.course_name)
        self.assertEqual(blocks[1]["cells"][1]["group_index"], 1)
        self.assertEqual(holding_item["activity_name"], displaced.course_name)
        self.assertEqual(holding_item["origin_group_index"], 1)
        self.assertEqual(holding_item["origin_group_label"], "Replay School 1")
        self.assertEqual(holding_item["origin_slot_key"], "mon_pm2")


class MoveProposalSourceIdentityTests(TestCase):
    def setUp(self):
        self.activity = Course.objects.create(
            course_name="Identity Activity",
            abriviation="IA",
            course_len=1,
        )
        self.blocks = build_schedule_blocks({
            "ags": ["Identity School 0"],
            "mon_pm1": [self.activity.course_name],
            "tue_am1": ["empty"],
        })
        self.proposal = {
            "source_block_id": "0:mon_pm1",
            "source_activity_id": self.activity.id,
            "source_activity_name": self.activity.course_name,
            "source_occurrence_id": "occurrence:0:mon_pm1",
            "target_slot_key": "tue_am1",
            "target_group_index": 0,
        }

    def test_matching_source_identity_passes_verification_and_save_readiness(self):
        verification = verify_move_proposal_source(self.blocks, self.proposal)
        proposal_result = apply_move_proposal(self.blocks, self.proposal)
        proposal_result["conflicts"] = validate_schedule_blocks(self.blocks)
        save_readiness = evaluate_move_proposal_for_save(proposal_result)

        self.assertTrue(verification["verified"])
        self.assertTrue(proposal_result["source_identity_verified"])
        self.assertTrue(save_readiness["can_save"])

    def test_mismatched_activity_id_blocks_save_readiness(self):
        self.proposal["source_activity_id"] = self.activity.id + 1

        self.assert_identity_mismatch_blocks_save()

    def test_mismatched_activity_name_blocks_save_readiness(self):
        self.proposal["source_activity_name"] = "Changed Activity"

        self.assert_identity_mismatch_blocks_save()

    def test_mismatched_occurrence_id_blocks_save_readiness(self):
        self.proposal["source_occurrence_id"] = "occurrence:changed"

        self.assert_identity_mismatch_blocks_save()

    def test_missing_source_block_blocks_save_readiness(self):
        self.proposal["source_block_id"] = "missing"

        proposal_result = apply_move_proposal(self.blocks, self.proposal)
        save_readiness = evaluate_move_proposal_for_save(proposal_result)

        self.assertFalse(proposal_result["applied"])
        self.assertEqual(proposal_result["error"], "invalid_source")
        self.assertFalse(save_readiness["can_save"])

    def test_multi_block_source_requires_valid_target_footprint(self):
        multi_block = Course.objects.create(
            course_name="Identity Multi Block",
            abriviation="IMB",
            course_len=2,
        )
        blocks = build_schedule_blocks({
            "ags": ["Identity School 0"],
            "tue_am1": [multi_block.course_name],
            "tue_am2": [multi_block.course_name],
            "wed_am1": ["empty"],
            "wed_am2": ["empty"],
        })
        proposal = {
            "source_block_id": "0:tue_am1",
            "source_activity_id": multi_block.id,
            "source_activity_name": multi_block.course_name,
            "source_occurrence_id": "occurrence:0:tue_am1",
            "target_slot_key": "wed_am1",
            "target_group_index": 0,
        }

        proposal_result = apply_move_proposal(blocks, proposal)
        save_readiness = evaluate_move_proposal_for_save(proposal_result)

        self.assertTrue(proposal_result["applied"])
        self.assertEqual(proposal_result["move_type"], "occurrence")
        self.assertEqual(proposal_result["occurrence_length"], 2)
        self.assertTrue(save_readiness["can_save"])

    def assert_identity_mismatch_blocks_save(self):
        proposal_result = apply_move_proposal(self.blocks, self.proposal)
        save_readiness = evaluate_move_proposal_for_save(proposal_result)

        self.assertFalse(proposal_result["applied"])
        self.assertEqual(proposal_result["error"], "source_identity_mismatch")
        self.assertFalse(save_readiness["can_save"])
        self.assertEqual(
            save_readiness["operator_message"],
            "This proposal cannot be saved because the generated schedule changed since selection.",
        )


class ManualMovePersistenceTests(TestCase):
    def setUp(self):
        self.activity = Course.objects.create(
            course_name="Persisted Move Activity",
            abriviation="PMA",
            course_len=1,
        )
        self.schedule = TheSched.objects.create(
            sched_name="Manual Move Persistence Schedule",
            sched_data=None,
        )

    def build_saveable_result(self, target_slot_key="tue_am1"):
        blocks = build_schedule_blocks({
            "ags": ["Persistence School 0"],
            "mon_pm1": [self.activity.course_name],
            "tue_am1": ["empty"],
            "wed_am1": ["empty"],
        })
        proposal_result = apply_move_proposal(blocks, {
            "source_block_id": "0:mon_pm1",
            "source_activity_id": self.activity.id,
            "source_activity_name": self.activity.course_name,
            "source_occurrence_id": "occurrence:0:mon_pm1",
            "target_slot_key": target_slot_key,
            "target_group_index": 0,
        })
        proposal_result["conflicts"] = validate_schedule_blocks(blocks)
        return proposal_result

    def test_persists_one_verified_saveable_move(self):
        proposal_result = self.build_saveable_result()

        move_record = persist_manual_move(self.schedule, proposal_result)

        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data["version"], 1)
        self.assertEqual(self.schedule.sched_data["manual_moves"], [move_record])
        self.assertEqual(move_record["source_block_id"], "0:mon_pm1")
        self.assertEqual(move_record["source_activity_id"], self.activity.id)
        self.assertEqual(move_record["source_activity_name"], self.activity.course_name)
        self.assertEqual(move_record["source_occurrence_id"], "occurrence:0:mon_pm1")
        self.assertEqual(move_record["source_group_index"], 0)
        self.assertEqual(move_record["source_slot_key"], "mon_pm1")
        self.assertEqual(move_record["target_group_index"], 0)
        self.assertEqual(move_record["target_slot_key"], "tue_am1")
        self.assertEqual(move_record["move_type"], "single_block")
        self.assertEqual(move_record["action_type"], "displacement_move")
        self.assertEqual(move_record["status"], "active")
        self.assertTrue(move_record["created_at"].endswith("Z"))

    def test_persists_optional_location_metadata_for_future_location_moves(self):
        proposal_result = self.build_saveable_result()
        proposal_result.update({
            "source_location_id": 11,
            "source_location_name": "Original Range",
            "target_location_id": 12,
            "target_location_name": "Backup Range",
        })

        move_record = persist_manual_move(self.schedule, proposal_result)

        self.assertEqual(move_record["source_location_id"], 11)
        self.assertEqual(move_record["source_location_name"], "Original Range")
        self.assertEqual(move_record["target_location_id"], 12)
        self.assertEqual(move_record["target_location_name"], "Backup Range")
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data["manual_moves"][0], move_record)

    def test_normalizes_none_sched_data(self):
        self.assertEqual(
            normalize_sched_data_structure(None),
            {"version": 1, "manual_moves": []},
        )

    def test_normalizes_empty_sched_data(self):
        self.assertEqual(
            normalize_sched_data_structure({}),
            {"version": 1, "manual_moves": []},
        )

    def test_normalizes_missing_manual_moves_and_preserves_unrelated_keys(self):
        source = {"version": 7, "source": "existing", "nested": {"keep": True}}

        normalized = normalize_sched_data_structure(source)

        self.assertEqual(normalized, {
            "version": 7,
            "source": "existing",
            "nested": {"keep": True},
            "manual_moves": [],
        })
        self.assertEqual(source, {"version": 7, "source": "existing", "nested": {"keep": True}})

    def test_rejects_malformed_non_dict_sched_data_without_mutation(self):
        for malformed in (["existing"], "existing"):
            with self.subTest(malformed=malformed):
                original = deepcopy(malformed)
                with self.assertRaisesMessage(
                    ValueError,
                    "This schedule contains legacy operational data that must be repaired",
                ):
                    normalize_sched_data_structure(malformed)
                self.assertEqual(malformed, original)

    def test_rejects_malformed_manual_moves_without_mutation(self):
        malformed = {"source": "existing", "manual_moves": "invalid"}
        original = deepcopy(malformed)

        with self.assertRaisesMessage(
            ValueError,
            "This schedule contains legacy operational data that must be repaired",
        ):
            normalize_sched_data_structure(malformed)

        self.assertEqual(malformed, original)

    def test_diagnoses_recoverable_and_malformed_sched_data(self):
        self.assertEqual(diagnose_sched_data_structure(None)["status"], "uninitialized")
        self.assertTrue(diagnose_sched_data_structure({})["recoverable"])
        malformed = diagnose_sched_data_structure(["legacy"])
        self.assertEqual(malformed["status"], "malformed")
        self.assertFalse(malformed["recoverable"])
        self.assertEqual(malformed["value_type"], "list")
        self.assertIn("must be repaired before schedule edits can be saved", malformed["message"])
        self.assertIn("found list", malformed["debug_detail"])

    def test_admin_safe_repair_normalizes_recoverable_sched_data(self):
        repaired = repair_sched_data_structure(self.schedule)

        self.schedule.refresh_from_db()
        self.assertEqual(repaired, {"version": 1, "manual_moves": []})
        self.assertEqual(self.schedule.sched_data, repaired)

    def test_admin_safe_repair_rejects_malformed_sched_data(self):
        self.schedule.sched_data = ["legacy"]
        self.schedule.save(update_fields=["sched_data"])

        with self.assertRaisesMessage(
            ValueError,
            "This schedule contains legacy operational data that must be repaired",
        ):
            repair_sched_data_structure(self.schedule)

        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data, ["legacy"])

    def test_explicit_malformed_repair_initializes_none_and_blank_values(self):
        for legacy_value in (None, "", "   "):
            with self.subTest(legacy_value=legacy_value):
                self.schedule.sched_data = legacy_value
                self.schedule.save(update_fields=["sched_data"])

                repaired = repair_malformed_sched_data(self.schedule)

                self.schedule.refresh_from_db()
                self.assertEqual(repaired, {"version": 1, "manual_moves": []})
                self.assertEqual(self.schedule.sched_data, repaired)

    def test_explicit_malformed_repair_rejects_populated_invalid_values(self):
        for legacy_value in ("legacy", ["legacy"], {"manual_moves": "legacy"}):
            with self.subTest(legacy_value=legacy_value):
                self.schedule.sched_data = legacy_value
                self.schedule.save(update_fields=["sched_data"])

                with self.assertRaisesMessage(
                    ValueError,
                    "This schedule contains legacy operational data that must be repaired",
                ):
                    repair_malformed_sched_data(self.schedule)

                self.schedule.refresh_from_db()
                self.assertEqual(self.schedule.sched_data, legacy_value)

    def test_first_save_initializes_empty_sched_data_cleanly(self):
        self.schedule.sched_data = {}
        self.schedule.save(update_fields=["sched_data"])

        move_record = persist_manual_move(self.schedule, self.build_saveable_result())

        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data, {
            "version": 1,
            "manual_moves": [move_record],
        })

    def test_missing_manual_moves_initializes_during_persistence(self):
        self.schedule.sched_data = {"version": 1, "source": "existing"}
        self.schedule.save(update_fields=["sched_data"])

        move_record = persist_manual_move(self.schedule, self.build_saveable_result())

        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data["source"], "existing")
        self.assertEqual(self.schedule.sched_data["manual_moves"], [move_record])

    def test_appends_multiple_moves_without_destroying_old_ones(self):
        first_move = persist_manual_move(self.schedule, self.build_saveable_result("tue_am1"))
        second_move = persist_manual_move(self.schedule, self.build_saveable_result("wed_am1"))

        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data["manual_moves"], [first_move, second_move])

    def test_preserves_unrelated_sched_data_keys(self):
        self.schedule.sched_data = {
            "source": "existing",
            "nested": {"keep": True},
        }
        self.schedule.save(update_fields=["sched_data"])

        persist_manual_move(self.schedule, self.build_saveable_result())

        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data["source"], "existing")
        self.assertEqual(self.schedule.sched_data["nested"], {"keep": True})
        self.assertEqual(self.schedule.sched_data["version"], 1)
        self.assertEqual(len(self.schedule.sched_data["manual_moves"]), 1)

    def test_rejects_unverified_proposal_result(self):
        proposal_result = self.build_saveable_result()
        proposal_result["source_identity_verified"] = False

        with self.assertRaisesMessage(ValueError, "verified source identity"):
            persist_manual_move(self.schedule, proposal_result)

        self.schedule.refresh_from_db()
        self.assertIsNone(self.schedule.sched_data)

    def test_rejects_unsaveable_proposal_result(self):
        proposal_result = self.build_saveable_result()
        proposal_result["conflicts"] = [{
            "type": "broken_multi_block",
            "severity": "error",
            "message": "Broken occurrence.",
            "related_block_ids": ["0:tue_am1"],
        }]

        with self.assertRaisesMessage(ValueError, "saveable proposal result"):
            persist_manual_move(self.schedule, proposal_result)

        self.schedule.refresh_from_db()
        self.assertIsNone(self.schedule.sched_data)

    def test_rejects_unsupported_action_type(self):
        proposal_result = self.build_saveable_result()
        proposal_result["action_type"] = "future_move_type"

        with self.assertRaisesMessage(ValueError, "supported operational move actions"):
            persist_manual_move(self.schedule, proposal_result)

        self.schedule.refresh_from_db()
        self.assertIsNone(self.schedule.sched_data)

    def test_rejects_proposal_result_without_validation_payload(self):
        proposal_result = self.build_saveable_result()
        proposal_result.pop("conflicts")

        with self.assertRaisesMessage(ValueError, "validated proposal result"):
            persist_manual_move(self.schedule, proposal_result)

        self.schedule.refresh_from_db()
        self.assertIsNone(self.schedule.sched_data)

    def test_rejects_malformed_sched_data_without_overwrite(self):
        for malformed in (["existing"], "existing"):
            with self.subTest(malformed=malformed):
                self.schedule.sched_data = malformed
                self.schedule.save(update_fields=["sched_data"])

                with self.assertRaisesMessage(ValueError, "Existing schedule data was left unchanged"):
                    persist_manual_move(self.schedule, self.build_saveable_result())

                self.schedule.refresh_from_db()
                self.assertEqual(self.schedule.sched_data, malformed)

    def test_persistence_does_not_modify_generated_schedule_output(self):
        generated_schedule = {
            "ags": ["Persistence School 0"],
            "mon_pm1": [self.activity.course_name],
            "tue_am1": ["empty"],
        }
        generated_schedule_before = deepcopy(generated_schedule)
        blocks = build_schedule_blocks(generated_schedule)
        proposal_result = apply_move_proposal(blocks, {
            "source_block_id": "0:mon_pm1",
            "source_activity_id": self.activity.id,
            "source_activity_name": self.activity.course_name,
            "source_occurrence_id": "occurrence:0:mon_pm1",
            "target_slot_key": "tue_am1",
            "target_group_index": 0,
        })
        proposal_result["conflicts"] = validate_schedule_blocks(blocks)

        persist_manual_move(self.schedule, proposal_result)

        self.assertEqual(generated_schedule, generated_schedule_before)

    def test_display_schedule_result_applies_manual_moves_to_stored_generation_copy(self):
        generated_schedule = {
            "ags": ["Persistence School 0"],
            "mon_pm1": [self.activity.course_name],
            "tue_am1": ["empty"],
        }
        self.schedule.store_generated_schedule(generated_schedule)
        persist_manual_move(self.schedule, self.build_saveable_result())
        stored_before = deepcopy(self.schedule.sched_data["generated_schedule"])

        result = self.schedule.get_display_schedule_result()

        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data["generated_schedule"], stored_before)
        self.assertEqual(result["generated_schedule"], stored_before)
        self.assertEqual(result["manual_moves"], self.schedule.sched_data["manual_moves"])
        self.assertEqual(len(result["override_replay_result"]["applied_overrides"]), 1)
        self.assertEqual(result["schedule_rows"][0]["cells"][0]["raw_value"], "empty")
        self.assertEqual(result["schedule_rows"][0]["cells"][3]["raw_value"], self.activity.course_name)


class MoveProposalSavePolicyTests(TestCase):
    def conflict(self, conflict_type, severity="error"):
        return {
            "type": conflict_type,
            "severity": severity,
            "message": f"{conflict_type} message",
            "related_block_ids": ["0:mon_pm1"],
        }

    def test_valid_proposal_is_saveable(self):
        result = evaluate_move_proposal_for_save({
            "applied": True,
            "conflicts": [],
        })

        self.assertTrue(result["can_save"])
        self.assertEqual(result["blocking_conflicts"], [])
        self.assertEqual(result["warning_conflicts"], [])
        self.assertEqual(result["informational_conflicts"], [])
        self.assertEqual(result["operator_message"], "This proposed move would be saveable.")

    def test_blocking_conflict_prevents_save(self):
        result = evaluate_move_proposal_for_save({
            "applied": True,
            "conflicts": [self.conflict("broken_multi_block")],
        })

        self.assertFalse(result["can_save"])
        self.assertEqual([conflict["type"] for conflict in result["blocking_conflicts"]], ["broken_multi_block"])
        self.assertEqual(result["warning_conflicts"], [])
        self.assertEqual(result["operator_message"], "This proposed move cannot be saved.")

    def test_duplicate_group_slot_allows_save_with_warning(self):
        result = evaluate_move_proposal_for_save({
            "applied": True,
            "conflicts": [self.conflict("duplicate_group_slot")],
        })

        self.assertTrue(result["can_save"])
        self.assertEqual(result["blocking_conflicts"], [])
        self.assertEqual([conflict["type"] for conflict in result["warning_conflicts"]], ["duplicate_group_slot"])
        self.assertEqual(result["operator_message"], "This proposed move would save with warnings.")

    def test_warning_conflict_allows_save_with_warning(self):
        result = evaluate_move_proposal_for_save({
            "applied": True,
            "conflicts": [self.conflict("invalid_time_slot")],
        })

        self.assertTrue(result["can_save"])
        self.assertEqual(result["blocking_conflicts"], [])
        self.assertEqual([conflict["type"] for conflict in result["warning_conflicts"]], ["invalid_time_slot"])
        self.assertEqual(result["warning_conflicts"][0]["severity"], "warning")
        self.assertEqual(result["operator_message"], "This proposed move would save with warnings.")

    def test_conflicts_are_bucketed_by_save_policy_severity(self):
        result = evaluate_move_proposal_for_save({
            "applied": True,
            "conflicts": [
                self.conflict("broken_multi_block"),
                self.conflict("invalid_time_slot"),
                self.conflict("proposal_context", severity="info"),
            ],
        })

        self.assertEqual([conflict["type"] for conflict in result["blocking_conflicts"]], ["broken_multi_block"])
        self.assertEqual([conflict["type"] for conflict in result["warning_conflicts"]], ["invalid_time_slot"])
        self.assertEqual([conflict["type"] for conflict in result["informational_conflicts"]], ["proposal_context"])

    def test_rejected_proposal_is_not_saveable(self):
        result = evaluate_move_proposal_for_save({
            "applied": False,
            "error": "invalid_target_slot",
            "message": "The target is invalid.",
            "source_block_id": "0:mon_pm1",
            "target_block_id": None,
        })

        self.assertFalse(result["can_save"])
        self.assertEqual(result["blocking_conflicts"][0]["type"], "invalid_target_slot")
        self.assertEqual(result["operator_message"], "This proposed move cannot be saved.")


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

    def store_generated_schedule(
        self,
        generated_schedule,
        generation_complete=True,
        generation_diagnostics=None,
        generation_runtime_diagnostics=None,
    ):
        sched_data = dict(self.schedule.sched_data or {})
        sched_data.setdefault("version", 1)
        sched_data.setdefault("manual_moves", [])
        sched_data.update({
            "generated_schedule": generated_schedule,
            "generation_complete": generation_complete,
            "generation_diagnostics": generation_diagnostics or [],
            "generation_runtime_diagnostics": generation_runtime_diagnostics or [],
        })
        self.schedule.sched_data = sched_data
        self.schedule.save(update_fields=["sched_data"])

    def manual_move_payload(self, activity, **overrides):
        payload = {
            "schedule_id": self.schedule.id,
            "source_schedule_id": self.schedule.id,
            "target_schedule_id": self.schedule.id,
            "source_block_id": "0:mon_pm1",
            "source_activity_id": activity.id,
            "source_activity_name": activity.course_name,
            "source_occurrence_id": "occurrence:0:mon_pm1",
            "source_group_index": 0,
            "source_slot_key": "mon_pm1",
            "target_group_index": 0,
            "target_slot_key": "tue_am1",
            "action_type": "displacement_move",
        }
        payload.update(overrides)
        return payload

    def post_manual_move(self, payload):
        return self.client.post(
            reverse("sched-manual-move", args=[self.schedule.id]),
            data=json.dumps(payload),
            content_type="application/json",
        )

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
            "View stored generated output, or intentionally regenerate a Schedule from its selected Schools.",
        )
        self.assertContains(response, "Create Schedule")
        self.assertContains(response, "View Schedule")
        self.assertContains(response, "Generate Schedule")
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
            "mon_pm2": ["empty"],
            "mon_night": ["g_box"],
        }
        self.store_generated_schedule(generated_schedule)
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
        self.assertContains(response, 'class="schedule-row-accent-1"', html=False)
        self.assertContains(response, 'class="schedule-row-header"', html=False)
        self.assertContains(response, "Group 1")
        self.assertContains(response, "Archery")
        self.assertContains(
            response,
            '<a href="?selected_block=0%3Amon_pm1#schedule-workspace" class="schedule-activity-card" draggable="false" title="Archery">',
            html=False,
        )
        self.assertContains(response, '<span class="schedule-drag-handle" aria-hidden="true"></span>', html=False)
        self.assertContains(response, "****")
        self.assertContains(response, "/////")
        self.assertEqual(response.context["schedule_rows"][0]["cells"][1]["display_value"], "****")
        self.assertEqual(response.context["schedule_rows"][0]["cells"][2]["display_value"], "/////")
        self.assertContains(response, "How this Schedule record works")
        self.assertContains(response, 'id="schedule-workspace"', html=False)

    def test_schedule_detail_without_stored_output_never_generates(self):
        self.schedule.sched_data = {}
        self.schedule.save(update_fields=["sched_data"])

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.side_effect = AssertionError("Viewing must not generate schedules")
            response = self.client.get(reverse("sched-detail", args=[self.schedule.id]))

        self.schedule.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No Generated Schedule Yet")
        self.assertContains(response, "Viewing this page does not generate output.")
        self.assertEqual(self.schedule.sched_data, {})
        self.assertEqual(create_sched.call_count, 0)

    def test_generate_schedule_post_runs_scheduler_and_stores_output(self):
        generated_schedule = {
            "ags": ["Generated School 0"],
            "mon_pm1": ["Stored Activity"],
            "mon_pm2": ["empty"],
        }

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.post(reverse("sched-generate", args=[self.schedule.id]))

        self.schedule.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("sched-detail", args=[self.schedule.id]))
        self.assertEqual(create_sched.call_count, 1)
        self.assertEqual(self.schedule.sched_data["generated_schedule"], generated_schedule)
        self.assertEqual(self.schedule.sched_data["manual_moves"], [])
        self.assertTrue(self.schedule.sched_data["generation_complete"])

    def test_manual_move_endpoint_persists_valid_same_schedule_move(self):
        activity = Course.objects.create(
            course_name="Endpoint Move Activity",
            abriviation="EMA",
            course_len=1,
        )
        generated_schedule = {
            "ags": ["Endpoint School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["empty"],
        }
        self.store_generated_schedule(generated_schedule)
        payload = self.manual_move_payload(
            activity,
            source_location_id=1,
            source_location_name="Original Field",
            target_location_id=2,
            target_location_name="Backup Field",
        )

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.side_effect = AssertionError("Manual move endpoint must not regenerate schedules")
            response = self.post_manual_move(payload)

        self.schedule.refresh_from_db()
        response_data = response.json()
        saved_move = self.schedule.sched_data["manual_moves"][0]
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response_data["ok"])
        self.assertEqual(len(self.schedule.sched_data["manual_moves"]), 1)
        self.assertEqual(saved_move["target_slot_key"], "tue_am1")
        self.assertEqual(saved_move["source_location_name"], "Original Field")
        self.assertEqual(saved_move["target_location_name"], "Backup Field")
        self.assertEqual(response_data["manual_move"], saved_move)
        self.assertEqual(create_sched.call_count, 0)

    def test_manual_move_endpoint_rejects_cross_schedule_move(self):
        activity = Course.objects.create(
            course_name="Cross Schedule Activity",
            abriviation="CSA",
            course_len=1,
        )
        other_schedule = TheSched.objects.create(sched_name="Other Manual Move Schedule", sched_data={})
        generated_schedule = {
            "ags": ["Endpoint School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["empty"],
        }
        self.store_generated_schedule(generated_schedule)
        payload = self.manual_move_payload(activity, target_schedule_id=other_schedule.id)

        response = self.post_manual_move(payload)

        self.schedule.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["ok"])
        self.assertEqual(response.json()["error"]["code"], "cross_schedule_move")
        self.assertEqual(self.schedule.sched_data["manual_moves"], [])

    def test_manual_move_endpoint_does_not_mutate_generated_schedule(self):
        activity = Course.objects.create(
            course_name="Endpoint Immutable Activity",
            abriviation="EIA",
            course_len=1,
        )
        generated_schedule = {
            "ags": ["Endpoint School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["empty"],
        }
        self.store_generated_schedule(generated_schedule)
        stored_generated_before = deepcopy(self.schedule.sched_data["generated_schedule"])

        response = self.post_manual_move(self.manual_move_payload(activity))

        self.schedule.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.schedule.sched_data["generated_schedule"], stored_generated_before)

    def test_manual_move_endpoint_returns_updated_displayed_schedule(self):
        activity = Course.objects.create(
            course_name="Endpoint Display Activity",
            abriviation="EDA",
            course_len=1,
        )
        generated_schedule = {
            "ags": ["Endpoint School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["empty"],
        }
        self.store_generated_schedule(generated_schedule)

        response = self.post_manual_move(self.manual_move_payload(activity))

        response_data = response.json()
        displayed_schedule = response_data["displayed_schedule"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(displayed_schedule["manual_moves"]), 1)
        self.assertEqual(len(displayed_schedule["override_replay_result"]["applied_overrides"]), 1)
        self.assertEqual(displayed_schedule["schedule_rows"][0]["cells"][0]["raw_value"], "empty")
        self.assertEqual(displayed_schedule["schedule_rows"][0]["cells"][3]["raw_value"], activity.course_name)

    def test_manual_move_endpoint_persists_holding_source_reassignment_with_displacement(self):
        source = Course.objects.create(course_name="Endpoint Holding Source", abriviation="EHS", course_len=1)
        displaced = Course.objects.create(course_name="Endpoint Holding Displaced", abriviation="EHD", course_len=1)
        occupied = Course.objects.create(course_name="Endpoint Holding Occupied", abriviation="EHO", course_len=1)
        generated_schedule = {
            "ags": ["Endpoint School 0"],
            "mon_pm1": [source.course_name],
            "mon_pm2": [displaced.course_name],
            "tue_am1": [occupied.course_name],
        }
        self.schedule.sched_data = {
            "version": 1,
            "manual_moves": [{
                "source_block_id": "0:mon_pm1",
                "source_activity_id": source.id,
                "source_activity_name": source.course_name,
                "source_occurrence_id": "occurrence:0:mon_pm1",
                "source_group_index": 0,
                "source_slot_key": "mon_pm1",
                "target_group_index": 0,
                "target_slot_key": "mon_pm2",
                "move_type": "single_block",
                "action_type": "displacement_move",
                "created_at": "2026-06-14T12:00:00Z",
                "status": "active",
            }],
            "generated_schedule": generated_schedule,
            "generation_complete": True,
            "generation_diagnostics": [],
            "generation_runtime_diagnostics": [],
        }
        self.schedule.save(update_fields=["sched_data"])
        payload = {
            "schedule_id": self.schedule.id,
            "source_schedule_id": self.schedule.id,
            "target_schedule_id": self.schedule.id,
            "source_kind": "holding",
            "source_holding_id": "holding:override:0:0:mon_pm2:1",
            "source_activity_id": displaced.id,
            "source_activity_name": displaced.course_name,
            "source_occurrence_id": "occurrence:0:mon_pm2",
            "target_group_index": 0,
            "target_slot_key": "tue_am1",
            "action_type": "displacement_move",
        }

        response = self.post_manual_move(payload)

        self.schedule.refresh_from_db()
        response_data = response.json()
        saved_move = self.schedule.sched_data["manual_moves"][1]
        holding_area = response_data["displayed_schedule"]["override_replay_result"]["holding_area"]
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response_data["ok"])
        self.assertEqual(saved_move["source_kind"], "holding")
        self.assertEqual(saved_move["source_holding_id"], "holding:override:0:0:mon_pm2:1")
        self.assertEqual(saved_move["action_type"], "displacement_move")
        self.assertEqual(response_data["displayed_schedule"]["schedule_rows"][0]["cells"][3]["activity_id"], displaced.id)
        self.assertEqual(holding_area[0]["activity_id"], occupied.id)

    def test_manual_move_endpoint_rejects_holding_source_cross_group_move(self):
        source = Course.objects.create(course_name="Endpoint Holding Cross Source", abriviation="EHCS", course_len=1)
        displaced = Course.objects.create(course_name="Endpoint Holding Cross Displaced", abriviation="EHCD", course_len=1)
        generated_schedule = {
            "ags": ["Endpoint School 0", "Endpoint School 1"],
            "mon_pm1": [source.course_name, "empty"],
            "mon_pm2": [displaced.course_name, "empty"],
            "tue_am1": ["empty", "empty"],
        }
        self.schedule.sched_data = {
            "version": 1,
            "manual_moves": [{
                "source_block_id": "0:mon_pm1",
                "source_activity_id": source.id,
                "source_activity_name": source.course_name,
                "source_occurrence_id": "occurrence:0:mon_pm1",
                "source_group_index": 0,
                "source_slot_key": "mon_pm1",
                "target_group_index": 0,
                "target_slot_key": "mon_pm2",
                "move_type": "single_block",
                "action_type": "displacement_move",
                "created_at": "2026-06-14T12:00:00Z",
                "status": "active",
            }],
            "generated_schedule": generated_schedule,
            "generation_complete": True,
            "generation_diagnostics": [],
            "generation_runtime_diagnostics": [],
        }
        self.schedule.save(update_fields=["sched_data"])
        payload = {
            "schedule_id": self.schedule.id,
            "source_kind": "holding",
            "source_holding_id": "holding:override:0:0:mon_pm2:1",
            "source_activity_id": displaced.id,
            "source_activity_name": displaced.course_name,
            "source_occurrence_id": "occurrence:0:mon_pm2",
            "target_group_index": 1,
            "target_slot_key": "tue_am1",
        }

        response = self.post_manual_move(payload)

        self.schedule.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "cross_group_move")
        self.assertEqual(len(self.schedule.sched_data["manual_moves"]), 1)

    def test_manual_move_endpoint_rejects_missing_payload_fields(self):
        generated_schedule = {
            "ags": ["Endpoint School 0"],
            "mon_pm1": ["Missing Payload Activity"],
            "tue_am1": ["empty"],
        }
        self.store_generated_schedule(generated_schedule)

        response = self.post_manual_move({})

        response_data = response.json()
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response_data["ok"])
        self.assertEqual(response_data["error"]["code"], "missing_field")
        self.assertIn("source_block_id", response_data["error"]["fields"])
        self.assertIn("target_slot_key", response_data["error"]["fields"])

    def test_schedule_detail_renders_drag_and_drop_hooks_for_activity_cells(self):
        activity = Course.objects.create(
            course_name="Draggable Activity",
            abriviation="DA",
            course_len=1,
        )
        generated_schedule = {
            "ags": ["Endpoint School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["empty"],
        }
        self.store_generated_schedule(generated_schedule)

        response = self.client.get(reverse("sched-detail", args=[self.schedule.id]))

        self.assertContains(response, 'draggable="true"', html=False)
        self.assertContains(response, 'data-draggable-activity="true"', html=False)
        self.assertContains(response, 'class="schedule-activity-card"', html=False)
        self.assertContains(response, 'class="schedule-drag-handle"', html=False)
        self.assertContains(response, 'class="schedule-activity-code"', html=False)
        self.assertContains(response, "DA")
        self.assertContains(response, "text-overflow: ellipsis", html=False)
        self.assertContains(response, "white-space: nowrap", html=False)
        self.assertContains(response, "border-left-width: 0.35rem", html=False)
        self.assertContains(response, ".schedule-group-badge", html=False)
        self.assertContains(response, 'cursor: grab', html=False)
        self.assertContains(response, '.schedule-table td.schedule-drop-eligible', html=False)
        self.assertContains(response, f'data-activity-id="{activity.id}"', html=False)
        self.assertContains(response, 'data-source-group-index="0"', html=False)
        self.assertContains(response, 'data-source-slot-key="mon_pm1"', html=False)
        self.assertContains(response, 'data-drop-target="true"', html=False)
        self.assertContains(response, 'data-slot-key="tue_am1"', html=False)

    def test_schedule_detail_renders_multi_block_occurrence_as_single_draggable_object(self):
        activity = Course.objects.create(
            course_name="Draggable Two Block",
            abriviation="D2B",
            course_len=2,
        )
        generated_schedule = {
            "ags": ["Endpoint School 0"],
            "tue_am1": [activity.course_name],
            "tue_am2": [activity.course_name],
            "wed_am1": ["empty"],
            "wed_am2": ["empty"],
        }
        self.store_generated_schedule(generated_schedule)

        response = self.client.get(reverse("sched-detail", args=[self.schedule.id]))

        first_block, second_block = response.context["schedule_rows"][0]["cells"][3:5]
        self.assertTrue(first_block["is_multi_block"])
        self.assertEqual(first_block["occurrence_position"], 1)
        self.assertEqual(second_block["occurrence_position"], 2)
        self.assertContains(response, 'data-block-id="0:tue_am1"', html=False)
        self.assertContains(response, 'data-source-slot-key="tue_am1"', html=False)
        self.assertContains(response, 'data-occurrence-length="2"', html=False)
        self.assertContains(response, 'draggable="true"', count=1, html=False)
        self.assertContains(response, 'class="schedule-activity-card schedule-activity-card-continuation"', html=False)
        self.assertContains(response, 'title="Draggable Two Block"', html=False)
        self.assertNotContains(response, 'class="schedule-activity-length"', html=False)
        self.assertNotContains(response, "(continues)")

    def test_schedule_detail_renders_manual_move_fetch_workflow(self):
        activity = Course.objects.create(
            course_name="Fetch Workflow Activity",
            abriviation="FWA",
            course_len=1,
        )
        generated_schedule = {
            "ags": ["Endpoint School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["empty"],
        }
        self.store_generated_schedule(generated_schedule)

        response = self.client.get(reverse("sched-detail", args=[self.schedule.id]))

        self.assertContains(response, reverse("sched-manual-move", args=[self.schedule.id]))
        self.assertContains(response, "fetch(manualMoveUrl", html=False)
        self.assertContains(response, "'X-CSRFToken': getCookie('csrftoken')", html=False)
        self.assertContains(response, "targetCell.dataset.groupIndex === draggedSource.dataset.sourceGroupIndex", html=False)
        self.assertContains(response, "source_kind: draggedSource.dataset.sourceKind || 'grid'", html=False)
        self.assertContains(response, "source_holding_id: draggedSource.dataset.sourceHoldingId", html=False)
        self.assertContains(response, "showEligibleDropTargets()", html=False)
        self.assertContains(response, "cell.classList.add('schedule-drop-eligible')", html=False)
        self.assertContains(response, "window.location.reload()", html=False)

    def test_schedule_detail_places_operational_workspace_before_secondary_details(self):
        generated_schedule = {
            "ags": ["Example School 0"],
            "mon_pm1": ["Archery"],
            "mon_pm2": ["empty"],
        }

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.get(reverse("sched-detail", args=[self.schedule.id]))

        content = response.content.decode()
        self.assertLess(content.index("Generated Schedule"), content.index("schedule-table"))
        self.assertLess(content.index("schedule-table"), content.index("Operational Editing Controls"))
        self.assertLess(content.index("Operational Editing Controls"), content.index("Schedule Details"))
        self.assertLess(content.index("Schedule Details"), content.index("Diagnostics"))
        self.assertLess(content.index("Diagnostics"), content.index("Generation Status"))

    def test_schedule_detail_messages_render_before_operational_workspace(self):
        generated_schedule = {
            "ags": ["Example School 0"],
            "mon_pm1": ["Archery"],
        }

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.post(
                reverse("sched-move-confirm", args=[self.schedule.id]),
                {
                    "source_block": "invalid",
                    "target_slot": "mon_pm2",
                    "target_group": "0",
                },
                follow=True,
            )

        content = response.content.decode()
        self.assertContains(response, "The selected source block ID is invalid.")
        self.assertLess(content.index("System messages"), content.index("schedule-table"))
        self.assertContains(response, "View Schedule loads stored generated output without running the scheduler.")
        self.assertContains(response, "/////</code> = unavailable or not present", html=False)
        self.assertContains(response, "****</code> = unassigned available block", html=False)
        self.assertEqual(response.context["conflict_summary_groups"], [])
        self.assertNotContains(response, "Operational Conflict Summary")
        block = response.context["schedule_rows"][0]["cells"][0]
        self.assertEqual(block["block_id"], "0:mon_pm1")
        self.assertEqual(block["display_value"], "Archery")
        self.assertTrue(block["is_activity"])

    def test_schedule_detail_selects_valid_activity_block_and_renders_metadata(self):
        activity = Course.objects.create(course_name="Selectable Activity", abriviation="SA", course_len=1)
        generated_schedule = {
            "ags": ["Selectable School 0"],
            "mon_pm1": [activity.course_name],
        }
        url = f'{reverse("sched-detail", args=[self.schedule.id])}?selected_block=0:mon_pm1'

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_block"]["block_id"], "0:mon_pm1")
        self.assertEqual(response.context["selected_occurrence_id"], "occurrence:0:mon_pm1")
        self.assertContains(response, "Selected Activity Block")
        self.assertContains(response, "Selectable School 0")
        self.assertContains(response, "mon_pm1")
        self.assertContains(response, "Selectable Activity")
        self.assertContains(response, str(activity.id))
        self.assertContains(response, 'class="table-primary"', count=1, html=False)
        self.assertNotContains(response, "Multi-block activity occurrence")
        self.assertContains(response, 'method="get"', html=False)
        self.assertContains(response, f'name="source_activity_id" value="{activity.id}"', html=False)
        self.assertContains(response, f'name="source_activity_name" value="{activity.course_name}"', html=False)
        self.assertContains(response, 'name="source_occurrence_id" value="occurrence:0:mon_pm1"', html=False)
        self.assertContains(response, 'name="target_group"', html=False)
        self.assertContains(response, 'name="target_slot"', html=False)
        self.assertContains(
            response,
            '<option value="displacement_move" selected>Replace activity and move current activity to holding</option>',
            html=True,
        )
        self.assertContains(response, "Advanced: allow overlap / double-book warning")
        self.assertContains(response, "Preview Move")

    def test_schedule_detail_exposes_and_renders_selected_block_conflicts(self):
        activity = Course.objects.create(course_name="Misplaced Selected Night", abriviation="MSN", course_len=0)
        generated_schedule = {
            "ags": ["Selectable School 0"],
            "mon_pm1": [activity.course_name],
        }
        url = f'{reverse("sched-detail", args=[self.schedule.id])}?selected_block=0:mon_pm1'

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.get(url)

        selected_block = response.context["selected_block"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["conflict_summaries"]), 1)
        self.assertEqual(response.context["conflict_summaries"][0]["type"], "invalid_time_slot")
        self.assertEqual(selected_block["conflicts"][0]["type"], "invalid_time_slot")
        self.assertContains(response, "Conflicts")
        self.assertContains(response, "invalid_time_slot")
        self.assertContains(response, "night activity placed in a daytime slot")
        self.assertContains(response, 'class="table-danger border border-primary border-2"', html=False)
        self.assertContains(response, "Operational Conflict Summary")

    def test_schedule_detail_renders_grouped_operational_conflict_summary(self):
        night = Course.objects.create(course_name="Summary Night", abriviation="SN", course_len=0)
        daytime = Course.objects.create(course_name="Summary Daytime", abriviation="SD", course_len=1)
        generated_schedule = {
            "ags": ["Summary School 0"],
            "mon_pm1": [night.course_name],
            "mon_night": [daytime.course_name],
        }

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.get(reverse("sched-detail", args=[self.schedule.id]))

        summary_groups = response.context["conflict_summary_groups"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(summary_groups), 1)
        self.assertEqual(summary_groups[0]["severity"], "error")
        self.assertEqual(summary_groups[0]["type"], "invalid_time_slot")
        self.assertEqual(len(summary_groups[0]["conflicts"]), 2)
        self.assertEqual(
            summary_groups[0]["conflicts"][0]["related_blocks"][0],
            {
                "block_id": "0:mon_pm1",
                "group_label": "Summary School 0",
                "slot_label": "PM1",
                "slot_key": "mon_pm1",
            },
        )
        self.assertContains(response, "Operational Conflict Summary")
        self.assertContains(response, "Error:")
        self.assertContains(response, "invalid_time_slot")
        self.assertContains(response, "night activity placed in a daytime slot")
        self.assertContains(response, "daytime activity placed in a night slot")
        self.assertContains(response, "Summary School 0")
        self.assertContains(response, "mon_pm1")
        self.assertContains(response, "mon_night")

    def test_schedule_detail_applies_valid_move_proposal_in_memory(self):
        activity = Course.objects.create(course_name="Rendered Proposal", abriviation="RP", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["empty"],
        }
        url = (
            f'{reverse("sched-detail", args=[self.schedule.id])}'
            f'?selected_block=0:mon_pm1&source_activity_id={activity.id}'
            f'&source_activity_name={activity.course_name}&source_occurrence_id=occurrence:0:mon_pm1'
            '&target_slot=tue_am1&target_group=0'
        )

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.get(url)

        proposal_result = response.context["proposal_result"]
        source, target = response.context["schedule_rows"][0]["cells"][0], response.context["schedule_rows"][0]["cells"][3]
        self.assertEqual(response.status_code, 200)
        self.assertTrue(proposal_result["applied"])
        self.assertEqual(response.context["selected_block"]["block_id"], "0:tue_am1")
        self.assertTrue(source["is_proposed_source"])
        self.assertTrue(target["is_proposed_target"])
        self.assertEqual(target["raw_value"], activity.course_name)
        self.assertContains(response, "Temporary Move Proposal")
        self.assertContains(response, "Not saved")
        self.assertContains(response, "Rendered Proposal")
        self.assertContains(response, "(moved)")
        self.assertContains(response, "(proposed)")
        self.assertContains(response, 'class="table-warning"', html=False)
        self.assertContains(response, 'class="table-success border border-primary border-2"', html=False)
        self.assertTrue(response.context["save_readiness"]["can_save"])
        self.assertContains(response, "Future Save Readiness")
        self.assertContains(response, "This proposed move would be saveable.")
        self.assertContains(response, "Preview only. Confirm the proposal server-side before saving.")
        self.assertNotContains(response, "Save Move")
        self.assertContains(response, f'name="source_activity_id" value="{activity.id}"', html=False)
        self.assertContains(response, f'name="source_activity_name" value="{activity.course_name}"', html=False)
        self.assertContains(response, 'name="source_occurrence_id" value="occurrence:0:mon_pm1"', html=False)
        self.assertContains(response, "Activity links in the schedule grid are paused while this proposal is active.")
        self.assertContains(response, "Cancel / Choose Different Activity")
        self.assertNotContains(response, 'href="?selected_block=0%3Atue_am1#schedule-workspace"', html=False)

    def test_schedule_detail_rejects_invalid_target_slot_safely(self):
        activity = Course.objects.create(course_name="Rejected Proposal", abriviation="RJP", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [activity.course_name],
        }
        url = (
            f'{reverse("sched-detail", args=[self.schedule.id])}'
            f'?selected_block=0:mon_pm1&source_activity_id={activity.id}'
            f'&source_activity_name={activity.course_name}&source_occurrence_id=occurrence:0:mon_pm1'
            '&target_slot=invalid_slot&target_group=0'
        )

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["proposal_result"]["applied"])
        self.assertEqual(response.context["proposal_result"]["error"], "invalid_target_slot")
        self.assertEqual(response.context["schedule_rows"][0]["cells"][0]["raw_value"], activity.course_name)
        self.assertFalse(response.context["save_readiness"]["can_save"])
        self.assertContains(response, "Temporary Move Proposal")
        self.assertContains(response, "invalid_target_slot")
        self.assertContains(response, "This proposed move cannot be saved.")

    def test_schedule_detail_moves_multi_block_occurrence_as_unit(self):
        activity = Course.objects.create(course_name="Moved Two Block", abriviation="MTB", course_len=2)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "tue_am1": [activity.course_name],
            "tue_am2": [activity.course_name],
            "wed_am1": ["empty"],
            "wed_am2": ["empty"],
        }
        url = (
            f'{reverse("sched-detail", args=[self.schedule.id])}'
            f'?selected_block=0:tue_am1&source_activity_id={activity.id}'
            f'&source_activity_name={activity.course_name}&source_occurrence_id=occurrence:0:tue_am1'
            '&target_slot=wed_am1&target_group=0'
        )

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["proposal_result"]["applied"])
        self.assertEqual(response.context["proposal_result"]["move_type"], "occurrence")
        self.assertEqual(response.context["proposal_result"]["occurrence_length"], 2)
        source_first, source_second = response.context["schedule_rows"][0]["cells"][3:5]
        target_first, target_second = response.context["schedule_rows"][0]["cells"][8:10]
        self.assertTrue(source_first["is_empty"])
        self.assertTrue(source_second["is_empty"])
        self.assertEqual(target_first["activity_id"], activity.id)
        self.assertEqual(target_second["activity_id"], activity.id)
        self.assertEqual(target_first["occurrence_id"], target_second["occurrence_id"])
        self.assertContains(response, "Temporary Move Proposal")

    def test_schedule_detail_reruns_validation_after_move_proposal(self):
        activity = Course.objects.create(course_name="Proposal Daytime", abriviation="PD", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [activity.course_name],
            "mon_night": ["empty"],
        }
        url = (
            f'{reverse("sched-detail", args=[self.schedule.id])}'
            f'?selected_block=0:mon_pm1&source_activity_id={activity.id}'
            f'&source_activity_name={activity.course_name}&source_occurrence_id=occurrence:0:mon_pm1'
            '&target_slot=mon_night&target_group=0'
        )

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.get(url)

        target = response.context["schedule_rows"][0]["cells"][2]
        self.assertTrue(response.context["proposal_result"]["applied"])
        self.assertEqual(target["conflicts"][0]["type"], "invalid_time_slot")
        self.assertEqual(response.context["conflict_summaries"][0]["type"], "invalid_time_slot")
        self.assertTrue(response.context["save_readiness"]["can_save"])
        self.assertEqual(
            response.context["save_readiness"]["warning_conflicts"][0]["type"],
            "invalid_time_slot",
        )
        self.assertContains(response, "Operational Conflict Summary")
        self.assertContains(response, "This proposed move would save with warnings.")
        self.assertContains(response, "Warning conflicts:")

    def test_move_proposal_does_not_mutate_generated_schedule_or_persist(self):
        activity = Course.objects.create(course_name="Temporary Proposal", abriviation="TP", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["empty"],
        }
        proposal_url = (
            f'{reverse("sched-detail", args=[self.schedule.id])}'
            f'?selected_block=0:mon_pm1&source_activity_id={activity.id}'
            f'&source_activity_name={activity.course_name}&source_occurrence_id=occurrence:0:mon_pm1'
            '&target_slot=tue_am1&target_group=0'
        )

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            proposal_response = self.client.get(proposal_url)
            reload_response = self.client.get(reverse("sched-detail", args=[self.schedule.id]))

        self.schedule.refresh_from_db()
        self.assertTrue(proposal_response.context["proposal_result"]["applied"])
        self.assertIsNone(reload_response.context["proposal_result"])
        self.assertEqual(reload_response.context["schedule_rows"][0]["cells"][0]["raw_value"], activity.course_name)
        self.assertEqual(reload_response.context["schedule_rows"][0]["cells"][3]["raw_value"], "empty")
        self.assertEqual(generated_schedule["mon_pm1"], [activity.course_name])
        self.assertEqual(generated_schedule["tue_am1"], ["empty"])
        self.assertEqual(self.schedule.sched_data["source"], "test")

    def test_post_move_confirmation_recomputes_without_prior_get_state(self):
        activity = Course.objects.create(course_name="Confirmed Proposal", abriviation="CP", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["empty"],
        }

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.post(reverse("sched-move-confirm", args=[self.schedule.id]), {
                "source_block": "0:mon_pm1",
                "source_activity_id": activity.id,
                "source_activity_name": activity.course_name,
                "source_occurrence_id": "occurrence:0:mon_pm1",
                "target_slot": "tue_am1",
                "target_group": "0",
            }, follow=True)

        source, target = response.context["schedule_rows"][0]["cells"][0], response.context["schedule_rows"][0]["cells"][3]
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["proposal_result"]["applied"])
        self.assertTrue(response.context["proposal_recomputed_server_side"])
        self.assertTrue(source["is_proposed_source"])
        self.assertTrue(target["is_proposed_target"])
        self.assertEqual(target["raw_value"], activity.course_name)
        self.assertContains(response, "Proposal recomputed server-side.")
        self.assertContains(response, "This proposed move would be saveable.")
        self.assertContains(response, "Save Move")

    def test_move_confirmation_uses_prg_and_preserves_proposal_context(self):
        activity = Course.objects.create(course_name="PRG Confirmed Proposal", abriviation="PCP", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["empty"],
        }

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            post_response = self.client.post(
                reverse("sched-move-confirm", args=[self.schedule.id]),
                self.move_post_data(activity),
            )
            redirected_response = self.client.get(post_response["Location"])

        self.assertEqual(post_response.status_code, 302)
        self.assertIn("proposal_confirmed=1", post_response["Location"])
        self.assertEqual(redirected_response.status_code, 200)
        self.assertTrue(redirected_response.context["proposal_result"]["applied"])
        self.assertTrue(redirected_response.context["proposal_recomputed_server_side"])
        self.assertContains(redirected_response, "Proposal confirmed server-side and is ready to save.")
        self.assertContains(redirected_response, "Save Move")

    def test_post_move_confirmation_ignores_manipulated_client_readiness(self):
        activity = Course.objects.create(course_name="Authoritative Proposal", abriviation="AP", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["empty"],
        }

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.post(reverse("sched-move-confirm", args=[self.schedule.id]), {
                "source_block": "0:mon_pm1",
                "source_activity_id": activity.id,
                "source_activity_name": activity.course_name,
                "source_occurrence_id": "occurrence:0:mon_pm1",
                "target_slot": "tue_am1",
                "target_group": "0",
                "can_save": "false",
                "blocking_conflicts": "manipulated",
                "operator_message": "Trust the client",
            }, follow=True)

        self.assertTrue(response.context["save_readiness"]["can_save"])
        self.assertEqual(response.context["save_readiness"]["blocking_conflicts"], [])
        self.assertContains(response, "This proposed move would be saveable.")
        self.assertNotContains(response, "Trust the client")
        self.assertNotContains(response, "manipulated")

    def test_post_move_confirmation_rejects_mismatched_source_identity_without_persistence(self):
        activity = Course.objects.create(course_name="Stale Identity Proposal", abriviation="SIP", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["empty"],
        }
        mismatch_cases = [
            ("activity id", {"source_activity_id": activity.id + 1}),
            ("activity name", {"source_activity_name": "Changed Activity"}),
            ("occurrence id", {"source_occurrence_id": "occurrence:changed"}),
        ]

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            for label, override in mismatch_cases:
                with self.subTest(label):
                    post_data = {
                        "source_block": "0:mon_pm1",
                        "source_activity_id": activity.id,
                        "source_activity_name": activity.course_name,
                        "source_occurrence_id": "occurrence:0:mon_pm1",
                        "target_slot": "tue_am1",
                        "target_group": "0",
                        **override,
                    }
                    response = self.client.post(
                        reverse("sched-move-confirm", args=[self.schedule.id]),
                        post_data,
                        follow=True,
                    )
                    self.assertFalse(response.context["proposal_result"]["applied"])
                    self.assertEqual(response.context["proposal_result"]["error"], "source_identity_mismatch")
                    self.assertFalse(response.context["save_readiness"]["can_save"])
                    self.assertContains(
                        response,
                        "This proposal cannot be saved because the generated schedule changed since selection.",
                    )

        self.schedule.refresh_from_db()
        self.assertEqual(generated_schedule["mon_pm1"], [activity.course_name])
        self.assertEqual(generated_schedule["tue_am1"], ["empty"])
        self.assertEqual(self.schedule.sched_data["source"], "test")

    def test_post_move_confirmation_safely_rejects_invalid_requests(self):
        one_block = Course.objects.create(course_name="POST One Block", abriviation="POB", course_len=1)
        occupied = Course.objects.create(course_name="POST Occupied", abriviation="PO", course_len=1)
        multi_block = Course.objects.create(course_name="POST Multi Block", abriviation="PMB", course_len=2)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [one_block.course_name],
            "mon_pm2": [occupied.course_name],
            "tue_am1": [multi_block.course_name],
            "tue_am2": [multi_block.course_name],
            "wed_am1": ["empty"],
        }
        cases = [
            (
                "invalid block id",
                {
                    "source_block": "invalid",
                    "source_activity_id": one_block.id,
                    "source_activity_name": one_block.course_name,
                    "target_slot": "wed_am1",
                    "target_group": "0",
                },
                "invalid_source",
            ),
            (
                "stale selection",
                {
                    "source_block": "0:wed_am1",
                    "source_activity_id": one_block.id,
                    "source_activity_name": one_block.course_name,
                    "target_slot": "mon_pm1",
                    "target_group": "0",
                },
                "stale_source",
            ),
            (
                "invalid target slot",
                {
                    "source_block": "0:mon_pm1",
                    "source_activity_id": one_block.id,
                    "source_activity_name": one_block.course_name,
                    "source_occurrence_id": "occurrence:0:mon_pm1",
                    "target_slot": "invalid",
                    "target_group": "0",
                },
                "invalid_target_slot",
            ),
            (
                "invalid target group",
                {
                    "source_block": "0:mon_pm1",
                    "source_activity_id": one_block.id,
                    "source_activity_name": one_block.course_name,
                    "source_occurrence_id": "occurrence:0:mon_pm1",
                    "target_slot": "wed_am1",
                    "target_group": "99",
                },
                "invalid_target_group",
            ),
        ]

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            for label, post_data, expected_error in cases:
                with self.subTest(label):
                    response = self.client.post(
                        reverse("sched-move-confirm", args=[self.schedule.id]),
                        post_data,
                        follow=True,
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.redirect_chain[0][1], 302)
                    self.assertFalse(response.context["proposal_result"]["applied"])
                    self.assertEqual(response.context["proposal_result"]["error"], expected_error)
                    self.assertFalse(response.context["save_readiness"]["can_save"])
                    self.assertTrue(response.context["proposal_recomputed_server_side"])
                    self.assertContains(response, "Proposal recomputed server-side.")

    def test_post_move_confirmation_does_not_mutate_schedule_or_persist(self):
        activity = Course.objects.create(course_name="POST Temporary", abriviation="PT", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["empty"],
        }

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            proposal_response = self.client.post(reverse("sched-move-confirm", args=[self.schedule.id]), {
                "source_block": "0:mon_pm1",
                "source_activity_id": activity.id,
                "source_activity_name": activity.course_name,
                "source_occurrence_id": "occurrence:0:mon_pm1",
                "target_slot": "tue_am1",
                "target_group": "0",
            }, follow=True)
            reload_response = self.client.get(reverse("sched-detail", args=[self.schedule.id]))

        self.schedule.refresh_from_db()
        self.assertTrue(proposal_response.context["proposal_result"]["applied"])
        self.assertIsNone(reload_response.context["proposal_result"])
        self.assertEqual(reload_response.context["schedule_rows"][0]["cells"][0]["raw_value"], activity.course_name)
        self.assertEqual(reload_response.context["schedule_rows"][0]["cells"][3]["raw_value"], "empty")
        self.assertEqual(generated_schedule["mon_pm1"], [activity.course_name])
        self.assertEqual(generated_schedule["tue_am1"], ["empty"])
        self.assertEqual(self.schedule.sched_data["source"], "test")

    def test_post_confirmation_renders_same_proposal_state_as_get_preview(self):
        activity = Course.objects.create(course_name="Matching Proposal", abriviation="MP", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["empty"],
        }
        get_url = (
            f'{reverse("sched-detail", args=[self.schedule.id])}'
            f'?selected_block=0:mon_pm1&source_activity_id={activity.id}'
            f'&source_activity_name={activity.course_name}&source_occurrence_id=occurrence:0:mon_pm1'
            '&target_slot=tue_am1&target_group=0'
        )

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            get_response = self.client.get(get_url)
            post_response = self.client.post(reverse("sched-move-confirm", args=[self.schedule.id]), {
                "source_block": "0:mon_pm1",
                "source_activity_id": activity.id,
                "source_activity_name": activity.course_name,
                "source_occurrence_id": "occurrence:0:mon_pm1",
                "target_slot": "tue_am1",
                "target_group": "0",
            }, follow=True)

        for field in ("applied", "activity", "source_block_id", "target_block_id", "target_slot_key", "target_group_index"):
            self.assertEqual(
                post_response.context["proposal_result"][field],
                get_response.context["proposal_result"][field],
            )
        self.assertEqual(post_response.context["conflict_summaries"], get_response.context["conflict_summaries"])
        self.assertEqual(post_response.context["save_readiness"], get_response.context["save_readiness"])
        self.assertContains(post_response, "Temporary Move Proposal")
        self.assertContains(post_response, "(moved)")
        self.assertContains(post_response, "(proposed)")

    def test_save_move_endpoint_persists_verified_proposal(self):
        activity = Course.objects.create(course_name="Saved Endpoint Move", abriviation="SEM", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["empty"],
        }

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.post(
                reverse("sched-move-save", args=[self.schedule.id]),
                self.move_post_data(activity),
                follow=True,
            )

        self.schedule.refresh_from_db()
        saved_move = self.schedule.sched_data["manual_moves"][0]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.redirect_chain,
            [(f'{reverse("sched-detail", args=[self.schedule.id])}#schedule-workspace', 302)],
        )
        self.assertEqual(self.schedule.sched_data["source"], "test")
        self.assertEqual(self.schedule.sched_data["version"], 1)
        self.assertEqual(saved_move["source_block_id"], "0:mon_pm1")
        self.assertEqual(saved_move["source_activity_id"], activity.id)
        self.assertEqual(saved_move["source_activity_name"], activity.course_name)
        self.assertEqual(saved_move["source_occurrence_id"], "occurrence:0:mon_pm1")
        self.assertEqual(saved_move["source_group_index"], 0)
        self.assertEqual(saved_move["source_slot_key"], "mon_pm1")
        self.assertEqual(saved_move["target_group_index"], 0)
        self.assertEqual(saved_move["target_slot_key"], "tue_am1")
        self.assertEqual(saved_move["move_type"], "single_block")
        self.assertEqual(saved_move["status"], "active")
        self.assertContains(response, "Move saved as a manual override.")
        self.assertContains(response, "It is now applied to the operational schedule.")

    def test_successful_save_uses_prg_and_refresh_does_not_resubmit(self):
        activity = Course.objects.create(course_name="PRG Saved Move", abriviation="PSM", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["empty"],
        }

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            post_response = self.client.post(
                reverse("sched-move-save", args=[self.schedule.id]),
                self.move_post_data(activity),
            )
            first_get = self.client.get(post_response["Location"])
            refreshed_get = self.client.get(post_response["Location"])

        self.schedule.refresh_from_db()
        self.assertEqual(post_response.status_code, 302)
        self.assertEqual(post_response["Location"], f'{reverse("sched-detail", args=[self.schedule.id])}#schedule-workspace')
        self.assertContains(first_get, "Move saved as a manual override.")
        self.assertNotContains(refreshed_get, "Move saved as a manual override.")
        self.assertEqual(len(self.schedule.sched_data["manual_moves"]), 1)

    def test_first_save_endpoint_initializes_none_sched_data_cleanly(self):
        activity = Course.objects.create(course_name="First Saved Endpoint Move", abriviation="FSEM", course_len=1)
        self.schedule.sched_data = None
        self.schedule.save(update_fields=["sched_data"])
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["empty"],
        }

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.post(
                reverse("sched-move-save", args=[self.schedule.id]),
                self.move_post_data(activity),
                follow=True,
            )

        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data["version"], 1)
        self.assertEqual(len(self.schedule.sched_data["manual_moves"]), 1)
        self.assertContains(response, "Move saved as a manual override.")

    def test_save_endpoint_reports_malformed_sched_data_without_overwrite(self):
        activity = Course.objects.create(course_name="Malformed Save Data", abriviation="MSD", course_len=1)
        self.schedule.sched_data = ["existing"]
        self.schedule.save(update_fields=["sched_data"])
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["empty"],
        }

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.post(
                reverse("sched-move-save", args=[self.schedule.id]),
                self.move_post_data(activity),
                follow=True,
            )

        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data, ["existing"])
        self.assertTrue(response.redirect_chain)
        self.assertContains(
            response,
            "This schedule contains legacy operational data that must be repaired before schedule edits can be saved.",
        )
        self.assertNotContains(response, "Expected sched_data to be a JSON object")

    def test_schedule_detail_shows_repair_action_for_malformed_sched_data(self):
        self.schedule.sched_data = ""
        self.schedule.save(update_fields=["sched_data"])

        response = self.client.get(reverse("sched-detail", args=[self.schedule.id]))

        self.assertEqual(response.context["sched_data_diagnostic"]["status"], "malformed")
        self.assertTrue(response.context["sched_data_diagnostic"]["repairable"])
        self.assertContains(response, "Legacy Schedule Data Requires Repair")
        self.assertContains(
            response,
            "This schedule contains legacy operational data that must be repaired before schedule edits can be saved.",
        )
        self.assertContains(response, "Repair Legacy Operational Data")
        self.assertNotContains(response, "Administrator diagnostic:")

    def test_staff_sees_legacy_sched_data_debug_detail(self):
        staff = get_user_model().objects.create_user(
            username="schedule-admin",
            password="test-password",
            is_staff=True,
        )
        self.client.force_login(staff)
        self.schedule.sched_data = ["legacy"]
        self.schedule.save(update_fields=["sched_data"])

        response = self.client.get(reverse("sched-detail", args=[self.schedule.id]))

        self.assertContains(response, "Administrator diagnostic:")
        self.assertContains(response, "Expected sched_data to be a JSON object; found list.")

    def test_repair_action_repairs_blank_data_with_prg(self):
        self.schedule.sched_data = "   "
        self.schedule.save(update_fields=["sched_data"])

        response = self.client.post(
            reverse("sched-data-repair", args=[self.schedule.id]),
            follow=True,
        )

        self.schedule.refresh_from_db()
        self.assertEqual(
            response.redirect_chain,
            [(reverse("sched-detail", args=[self.schedule.id]), 302)],
        )
        self.assertEqual(self.schedule.sched_data, {"version": 1, "manual_moves": []})
        self.assertContains(response, "Legacy operational data was repaired.")
        self.assertNotContains(response, "Repair Legacy Operational Data")

    def test_repair_action_requires_post(self):
        self.schedule.sched_data = ""
        self.schedule.save(update_fields=["sched_data"])

        response = self.client.get(reverse("sched-data-repair", args=[self.schedule.id]))

        self.schedule.refresh_from_db()
        self.assertEqual(response.status_code, 405)
        self.assertEqual(self.schedule.sched_data, "")

    def test_repair_action_refuses_populated_invalid_data_with_prg(self):
        self.schedule.sched_data = "legacy"
        self.schedule.save(update_fields=["sched_data"])

        response = self.client.post(
            reverse("sched-data-repair", args=[self.schedule.id]),
            follow=True,
        )

        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data, "legacy")
        self.assertContains(
            response,
            "This schedule contains legacy operational data that must be repaired before schedule edits can be saved.",
        )
        self.assertContains(response, "Repair Legacy Operational Data")

    def test_successful_blank_repair_enables_save_workflow(self):
        activity = Course.objects.create(course_name="Repair Then Save", abriviation="RTS", course_len=1)
        self.schedule.sched_data = ""
        self.schedule.save(update_fields=["sched_data"])
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["empty"],
        }

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            repair_response = self.client.post(
                reverse("sched-data-repair", args=[self.schedule.id]),
                follow=True,
            )
            self.store_generated_schedule(generated_schedule)
            save_response = self.client.post(
                reverse("sched-move-save", args=[self.schedule.id]),
                self.move_post_data(activity),
                follow=True,
            )

        self.schedule.refresh_from_db()
        self.assertContains(repair_response, "Legacy operational data was repaired.")
        self.assertContains(save_response, "Move saved as a manual override.")
        self.assertEqual(len(self.schedule.sched_data["manual_moves"]), 1)

    def test_save_move_endpoint_rejects_stale_proposal(self):
        selected_activity = Course.objects.create(course_name="Originally Selected", abriviation="OS", course_len=1)
        regenerated_activity = Course.objects.create(course_name="Regenerated Activity", abriviation="RA", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [regenerated_activity.course_name],
            "tue_am1": ["empty"],
        }

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.post(
                reverse("sched-move-save", args=[self.schedule.id]),
                self.move_post_data(selected_activity),
                follow=True,
            )

        self.schedule.refresh_from_db()
        self.assertEqual(response.context["proposal_result"]["error"], "source_identity_mismatch")
        self.assertEqual(self.schedule.sched_data["source"], "test")
        self.assertContains(response, "Move was not saved because the source proposal is invalid or stale.")

    def test_save_move_endpoint_rejects_unsaveable_proposal(self):
        activity = Course.objects.create(course_name="Unsaveable Endpoint Move", abriviation="UEM", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["g_box"],
        }

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.post(
                reverse("sched-move-save", args=[self.schedule.id]),
                self.move_post_data(activity),
                follow=True,
            )

        self.schedule.refresh_from_db()
        self.assertEqual(response.context["proposal_result"]["error"], "target_unavailable")
        self.assertFalse(response.context["save_readiness"]["can_save"])
        self.assertEqual(self.schedule.sched_data["source"], "test")

    def test_occupied_target_warning_proposal_renders_and_persists(self):
        source = Course.objects.create(course_name="Rendered Overlap Source", abriviation="ROS", course_len=1)
        target = Course.objects.create(course_name="Rendered Overlap Target", abriviation="ROT", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [source.course_name],
            "mon_pm2": [target.course_name],
        }
        post_data = self.move_post_data(
            source,
            target_slot="mon_pm2",
            action_type="overlap_move",
        )

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            confirm_response = self.client.post(
                reverse("sched-move-confirm", args=[self.schedule.id]),
                post_data,
                follow=True,
            )
            save_response = self.client.post(
                reverse("sched-move-save", args=[self.schedule.id]),
                post_data,
                follow=True,
            )

        self.schedule.refresh_from_db()
        duplicate = next(
            conflict
            for conflict in confirm_response.context["conflict_summaries"]
            if conflict["type"] == "duplicate_group_slot"
        )
        self.assertTrue(confirm_response.context["proposal_result"]["applied"])
        self.assertTrue(confirm_response.context["proposal_result"]["target_was_occupied"])
        self.assertTrue(confirm_response.context["save_readiness"]["can_save"])
        self.assertEqual(confirm_response.context["save_readiness"]["blocking_conflicts"], [])
        self.assertEqual(confirm_response.context["save_readiness"]["warning_conflicts"][0]["type"], "duplicate_group_slot")
        self.assertEqual(duplicate["severity"], "warning")
        self.assertContains(confirm_response, source.course_name)
        self.assertContains(confirm_response, target.course_name)
        self.assertContains(confirm_response, 'class="schedule-overlap-card"', html=False)
        self.assertContains(confirm_response, 'class="schedule-overlap-badge"', html=False)
        self.assertContains(confirm_response, "Overlap")
        self.assertContains(confirm_response, "(proposed overlap)")
        self.assertContains(confirm_response, "Overlap Warning")
        self.assertContains(confirm_response, "This proposed move would save with warnings.")
        self.assertContains(confirm_response, 'class="alert alert-warning mb-3"', html=False)
        self.assertContains(save_response, "Move saved as a manual override.")
        self.assertEqual(self.schedule.sched_data["manual_moves"][0]["target_slot_key"], "mon_pm2")
        self.assertEqual(self.schedule.sched_data["manual_moves"][0]["action_type"], "overlap_move")

    def test_explicit_displacement_save_persists_and_replays_after_reload(self):
        source = Course.objects.create(course_name="Saved Displacement Source", abriviation="SDS", course_len=1)
        displaced = Course.objects.create(course_name="Saved Displacement Target", abriviation="SDT", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [source.course_name],
            "mon_pm2": [displaced.course_name],
        }
        post_data = self.move_post_data(
            source,
            target_slot="mon_pm2",
            action_type="displacement_move",
        )

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            confirm_response = self.client.post(
                reverse("sched-move-confirm", args=[self.schedule.id]),
                post_data,
                follow=True,
            )
            save_response = self.client.post(
                reverse("sched-move-save", args=[self.schedule.id]),
                post_data,
                follow=True,
            )
            reload_response = self.client.get(reverse("sched-detail", args=[self.schedule.id]))

        self.schedule.refresh_from_db()
        saved_move = self.schedule.sched_data["manual_moves"][0]
        target = reload_response.context["schedule_rows"][0]["cells"][1]
        holding_item = reload_response.context["holding_area_preview"][0]
        self.assertEqual(confirm_response.context["proposal_result"]["action_type"], "displacement_move")
        self.assertContains(confirm_response, "Displacement Preview")
        self.assertContains(save_response, "Move saved as a manual override.")
        self.assertEqual(saved_move["action_type"], "displacement_move")
        self.assertEqual(target["activity_id"], source.id)
        self.assertEqual(target["overlapping_blocks"], [])
        self.assertEqual(holding_item["activity_id"], displaced.id)

    def test_occupied_target_save_defaults_to_displacement_and_holding(self):
        source = Course.objects.create(course_name="Default Displacement Source", abriviation="DDS", course_len=1)
        displaced = Course.objects.create(course_name="Default Displacement Target", abriviation="DDT", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [source.course_name],
            "mon_pm2": [displaced.course_name],
        }
        post_data = self.move_post_data(source, target_slot="mon_pm2", action_type="")

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            save_response = self.client.post(
                reverse("sched-move-save", args=[self.schedule.id]),
                post_data,
                follow=True,
            )
            reload_response = self.client.get(reverse("sched-detail", args=[self.schedule.id]))

        self.schedule.refresh_from_db()
        saved_move = self.schedule.sched_data["manual_moves"][0]
        target = reload_response.context["schedule_rows"][0]["cells"][1]
        holding_item = reload_response.context["holding_area_preview"][0]
        self.assertContains(save_response, "Move saved as a manual override.")
        self.assertEqual(saved_move["action_type"], "displacement_move")
        self.assertEqual(target["activity_id"], source.id)
        self.assertEqual(target["overlapping_blocks"], [])
        self.assertEqual(holding_item["activity_id"], displaced.id)

    def test_holding_reassignment_save_appends_history_and_survives_reload(self):
        source = Course.objects.create(course_name="Workflow Holding Source", abriviation="WHS", course_len=1)
        displaced = Course.objects.create(course_name="Workflow Holding Displaced", abriviation="WHD", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [source.course_name],
            "mon_pm2": [displaced.course_name],
            "tue_am1": ["empty"],
        }
        self.schedule.sched_data = {
            "version": 1,
            "manual_moves": [{
                "source_block_id": "0:mon_pm1",
                "source_activity_id": source.id,
                "source_activity_name": source.course_name,
                "source_occurrence_id": "occurrence:0:mon_pm1",
                "source_group_index": 0,
                "source_slot_key": "mon_pm1",
                "target_group_index": 0,
                "target_slot_key": "mon_pm2",
                "move_type": "single_block",
                "action_type": "displacement_move",
                "created_at": "2026-06-14T12:00:00Z",
                "status": "active",
            }],
        }
        self.schedule.save(update_fields=["sched_data"])
        post_data = {
            "source_kind": "holding",
            "source_holding": "holding:override:0:0:mon_pm2:1",
            "source_activity_id": displaced.id,
            "source_activity_name": displaced.course_name,
            "source_occurrence_id": "occurrence:0:mon_pm2",
            "target_group": "0",
            "target_slot": "tue_am1",
            "action_type": "overlap_move",
        }

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            preview_response = self.client.get(
                reverse("sched-detail", args=[self.schedule.id]),
                {
                    "source_kind": "holding",
                    "selected_holding": "holding:override:0:0:mon_pm2:1",
                    "source_activity_id": displaced.id,
                    "source_activity_name": displaced.course_name,
                    "source_occurrence_id": "occurrence:0:mon_pm2",
                    "target_group": "0",
                    "target_slot": "tue_am1",
                    "action_type": "overlap_move",
                },
            )
            save_response = self.client.post(
                reverse("sched-move-save", args=[self.schedule.id]),
                post_data,
                follow=True,
            )
            reload_response = self.client.get(reverse("sched-detail", args=[self.schedule.id]))

        self.schedule.refresh_from_db()
        self.assertTrue(preview_response.context["proposal_result"]["applied"])
        self.assertEqual(preview_response.context["proposal_result"]["source_kind"], "holding")
        self.assertEqual(preview_response.context["holding_area_preview"], [])
        self.assertContains(save_response, "Move saved as a manual override.")
        self.assertEqual(len(self.schedule.sched_data["manual_moves"]), 2)
        saved_reassignment = self.schedule.sched_data["manual_moves"][1]
        self.assertEqual(saved_reassignment["source_kind"], "holding")
        self.assertEqual(saved_reassignment["source_holding_id"], "holding:override:0:0:mon_pm2:1")
        self.assertEqual(reload_response.context["holding_area_preview"], [])
        self.assertEqual(reload_response.context["schedule_rows"][0]["cells"][1]["activity_id"], source.id)
        self.assertEqual(reload_response.context["schedule_rows"][0]["cells"][3]["activity_id"], displaced.id)

    def test_save_move_endpoint_rejects_invalid_and_multi_block_proposals(self):
        one_block = Course.objects.create(course_name="Save One Block", abriviation="SOB", course_len=1)
        multi_block = Course.objects.create(course_name="Save Multi Block", abriviation="SMB", course_len=2)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [one_block.course_name],
            "tue_am1": [multi_block.course_name],
            "tue_am2": [multi_block.course_name],
            "wed_am1": ["empty"],
        }
        cases = [
            (
                {
                    **self.move_post_data(multi_block, source_block="invalid", target_slot="wed_am1"),
                },
                "invalid_source",
            ),
            (
                self.move_post_data(one_block, target_slot="invalid"),
                "invalid_target_slot",
            ),
        ]

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            for post_data, expected_error in cases:
                with self.subTest(expected_error):
                    response = self.client.post(
                        reverse("sched-move-save", args=[self.schedule.id]),
                        post_data,
                        follow=True,
                    )
                    self.assertEqual(response.context["proposal_result"]["error"], expected_error)

        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data["source"], "test")

    def test_save_move_endpoint_appends_multiple_moves(self):
        activity = Course.objects.create(course_name="Multiple Saved Moves", abriviation="MSM", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["empty"],
            "wed_am1": ["empty"],
        }

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            first_response = self.client.post(
                reverse("sched-move-save", args=[self.schedule.id]),
                self.move_post_data(activity, target_slot="tue_am1"),
                follow=True,
            )
            second_response = self.client.post(
                reverse("sched-move-save", args=[self.schedule.id]),
                self.move_post_data(
                    activity,
                    source_block="0:tue_am1",
                    target_slot="wed_am1",
                    source_occurrence_id="occurrence:persisted:0:0:tue_am1",
                ),
                follow=True,
            )

        self.schedule.refresh_from_db()
        self.assertContains(first_response, "Move saved as a manual override.")
        self.assertContains(second_response, "Move saved as a manual override.")
        self.assertEqual(
            [move["target_slot_key"] for move in self.schedule.sched_data["manual_moves"]],
            ["tue_am1", "wed_am1"],
        )

    def test_saved_move_survives_reload_without_changing_generated_output(self):
        activity = Course.objects.create(course_name="Saved And Replayed", abriviation="SAR", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["empty"],
        }

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            save_response = self.client.post(
                reverse("sched-move-save", args=[self.schedule.id]),
                self.move_post_data(activity),
                follow=True,
            )
            reload_response = self.client.get(reverse("sched-detail", args=[self.schedule.id]))

        self.assertContains(save_response, "Move saved as a manual override.")
        self.assertEqual(reload_response.context["schedule_rows"][0]["cells"][0]["raw_value"], "empty")
        self.assertEqual(reload_response.context["schedule_rows"][0]["cells"][3]["raw_value"], activity.course_name)
        self.assertTrue(reload_response.context["schedule_rows"][0]["cells"][3]["is_persisted_override"])
        self.assertEqual(len(reload_response.context["override_replay_result"]["applied_overrides"]), 1)
        self.assertIsNone(reload_response.context["proposal_result"])
        self.assertContains(reload_response, "(persisted)")
        self.assertContains(reload_response, "Saved overrides applied:")
        self.assertEqual(generated_schedule["mon_pm1"], [activity.course_name])
        self.assertEqual(generated_schedule["tue_am1"], ["empty"])

    def test_persisted_overlap_and_temporary_proposal_render_distinctly(self):
        source = Course.objects.create(course_name="Persisted Render Source", abriviation="PRS", course_len=1)
        occupied = Course.objects.create(course_name="Persisted Render Target", abriviation="PRT", course_len=1)
        proposal_activity = Course.objects.create(course_name="Temporary Render Proposal", abriviation="TRP", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [source.course_name],
            "mon_pm2": [occupied.course_name],
            "tue_am1": [proposal_activity.course_name],
            "tue_am2": ["empty"],
        }
        self.schedule.sched_data = {
            "version": 1,
            "manual_moves": [{
                "source_block_id": "0:mon_pm1",
                "source_activity_id": source.id,
                "source_activity_name": source.course_name,
                "source_occurrence_id": "occurrence:0:mon_pm1",
                "source_group_index": 0,
                "source_slot_key": "mon_pm1",
                "target_group_index": 0,
                "target_slot_key": "mon_pm2",
                "move_type": "single_block",
                "created_at": "2026-06-14T12:00:00Z",
                "status": "active",
            }],
        }
        self.schedule.save(update_fields=["sched_data"])

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.get(
                reverse("sched-detail", args=[self.schedule.id]),
                {
                    "selected_block": "0:tue_am1",
                    "source_activity_id": proposal_activity.id,
                    "source_activity_name": proposal_activity.course_name,
                    "source_occurrence_id": "occurrence:0:tue_am1",
                    "target_slot": "tue_am2",
                    "target_group": "0",
                },
            )

        self.assertContains(response, "(persisted overlap)")
        self.assertContains(response, 'class="schedule-overlap-card"', html=False)
        self.assertContains(response, 'class="schedule-overlap-badge"', html=False)
        self.assertContains(response, "(proposed)")
        self.assertContains(response, "duplicate_group_slot")
        self.assertTrue(response.context["proposal_result"]["applied"])

    def test_explicit_displacement_renders_holding_area_without_switching_global_default(self):
        source = Course.objects.create(course_name="Preview Holding Source", abriviation="PHS", course_len=1)
        displaced = Course.objects.create(course_name="Preview Holding Displaced", abriviation="PHD", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [source.course_name],
            "mon_pm2": [displaced.course_name],
        }
        self.schedule.sched_data = {
            "version": 1,
            "manual_moves": [{
                "source_block_id": "0:mon_pm1",
                "source_activity_id": source.id,
                "source_activity_name": source.course_name,
                "source_occurrence_id": "occurrence:0:mon_pm1",
                "source_group_index": 0,
                "source_slot_key": "mon_pm1",
                "target_group_index": 0,
                "target_slot_key": "mon_pm2",
                "move_type": "single_block",
                "action_type": "displacement_move",
                "created_at": "2026-06-14T12:00:00Z",
                "status": "active",
            }],
        }
        self.schedule.save(update_fields=["sched_data"])

        self.store_generated_schedule(generated_schedule)
        stored_before = deepcopy(self.schedule.sched_data)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.get(reverse("sched-detail", args=[self.schedule.id]))

        self.schedule.refresh_from_db()
        main_target = response.context["schedule_rows"][0]["cells"][1]
        holding_item = response.context["holding_area_preview"][0]
        self.assertContains(response, "Supporting Workspace")
        self.assertContains(response, "Displaced Activities Awaiting Reassignment")
        self.assertContains(response, "Displaced")
        self.assertContains(response, 'class="list-group-item px-0 holding-area-item"', html=False)
        self.assertContains(response, 'class="schedule-activity-card holding-activity-card"', html=False)
        self.assertContains(response, 'draggable="true"', html=False)
        self.assertContains(response, 'data-source-kind="holding"', html=False)
        self.assertContains(response, 'data-source-holding-id="holding:override:0:0:mon_pm2:1"', html=False)
        self.assertContains(response, 'aria-label="Drag Preview Holding Displaced from displaced activities to a schedule slot"', html=False)
        self.assertContains(response, '<span class="schedule-drag-handle" aria-hidden="true"></span>', html=False)
        self.assertContains(response, 'class="holding-card-meta small"', html=False)
        self.assertContains(response, 'class="holding-fallback-controls"', html=False)
        self.assertContains(response, "Fallback reassignment form")
        self.assertContains(response, 'class="row g-2 align-items-end holding-reassign-form"', html=False)
        self.assertContains(response, "PHD")
        self.assertContains(
            response,
            "These activities were pushed out by saved displacement moves",
        )
        self.assertContains(response, displaced.course_name)
        self.assertContains(response, "Proposal School 0")
        self.assertContains(response, "PM2")
        self.assertContains(response, "Saved override 1")
        self.assertEqual(response.context["override_replay_result"]["replay_mode"], "overlap")
        self.assertEqual(response.context["displacement_preview"]["replay_mode"], "overlap")
        self.assertEqual(main_target["raw_value"], source.course_name)
        self.assertEqual(main_target["overlapping_blocks"], [])
        self.assertEqual(holding_item["activity_name"], displaced.course_name)
        self.assertEqual(holding_item["activity_id"], displaced.id)
        displacement_applied = response.context["displacement_preview"]["applied_overrides"][0]
        self.assertEqual(displacement_applied["moved_activity_id"], source.id)
        self.assertEqual(displacement_applied["displaced_activity_ids"], [displaced.id])
        self.assertNotIn(
            "duplicate_group_slot",
            {conflict["type"] for conflict in response.context["conflict_summaries"]},
        )
        self.assertEqual(self.schedule.sched_data, stored_before)

    def test_holding_area_preview_is_hidden_when_displacement_holds_nothing(self):
        activity = Course.objects.create(course_name="Preview Empty Target", abriviation="PET", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["empty"],
        }
        self.schedule.sched_data = {
            "version": 1,
            "manual_moves": [{
                "source_block_id": "0:mon_pm1",
                "source_activity_id": activity.id,
                "source_activity_name": activity.course_name,
                "source_occurrence_id": "occurrence:0:mon_pm1",
                "source_group_index": 0,
                "source_slot_key": "mon_pm1",
                "target_group_index": 0,
                "target_slot_key": "tue_am1",
                "move_type": "single_block",
                "created_at": "2026-06-14T12:00:00Z",
                "status": "active",
            }],
        }
        self.schedule.save(update_fields=["sched_data"])

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.get(reverse("sched-detail", args=[self.schedule.id]))

        self.assertEqual(response.context["holding_area_preview"], [])
        self.assertNotContains(response, "Displaced Activities Awaiting Reassignment")
        self.assertNotContains(
            response,
            "These activities were pushed out by saved displacement moves",
        )
        self.assertEqual(response.context["schedule_rows"][0]["cells"][3]["raw_value"], activity.course_name)
        self.assertEqual(response.context["conflict_summaries"], [])

    def test_non_first_row_selection_defaults_target_group_and_replays_on_same_row(self):
        first_row = Course.objects.create(course_name="View First Row", abriviation="VFR", course_len=1)
        second_row = Course.objects.create(course_name="View Second Row", abriviation="VSR", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0", "Proposal School 1"],
            "mon_pm1": [first_row.course_name, second_row.course_name],
            "tue_am1": ["empty", "empty"],
        }
        post_data = {
            "source_block": "1:mon_pm1",
            "source_activity_id": second_row.id,
            "source_activity_name": second_row.course_name,
            "source_occurrence_id": "occurrence:1:mon_pm1",
            "source_group": "1",
            "source_slot": "mon_pm1",
            "target_slot": "tue_am1",
            "target_group": "1",
        }

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            selection_response = self.client.get(
                reverse("sched-detail", args=[self.schedule.id]),
                {"selected_block": "1:mon_pm1"},
            )
            save_response = self.client.post(
                reverse("sched-move-save", args=[self.schedule.id]),
                post_data,
                follow=True,
            )
            reload_response = self.client.get(reverse("sched-detail", args=[self.schedule.id]))

        self.schedule.refresh_from_db()
        self.assertContains(selection_response, '<option value="1" selected>Proposal School 1</option>', html=True)
        self.assertContains(selection_response, 'name="source_group" value="1"', html=False)
        self.assertContains(selection_response, 'name="source_slot" value="mon_pm1"', html=False)
        self.assertContains(save_response, "Move saved as a manual override.")
        saved_move = self.schedule.sched_data["manual_moves"][0]
        self.assertEqual(saved_move["source_group_index"], 1)
        self.assertEqual(saved_move["target_group_index"], 1)
        self.assertEqual(reload_response.context["schedule_rows"][0]["cells"][0]["raw_value"], first_row.course_name)
        self.assertEqual(reload_response.context["schedule_rows"][0]["cells"][3]["raw_value"], "empty")
        self.assertEqual(reload_response.context["schedule_rows"][1]["cells"][0]["raw_value"], "empty")
        self.assertEqual(reload_response.context["schedule_rows"][1]["cells"][3]["raw_value"], second_row.course_name)

    def test_non_first_row_holding_preview_reports_displaced_target_group(self):
        source = Course.objects.create(course_name="Holding Row Two Source", abriviation="HRS", course_len=1)
        displaced = Course.objects.create(course_name="Holding Row Two Displaced", abriviation="HRD", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0", "Proposal School 1"],
            "mon_pm1": ["empty", source.course_name],
            "mon_pm2": ["empty", displaced.course_name],
        }
        self.schedule.sched_data = {
            "version": 1,
            "manual_moves": [{
                "source_block_id": "1:mon_pm1",
                "source_activity_id": source.id,
                "source_activity_name": source.course_name,
                "source_occurrence_id": "occurrence:1:mon_pm1",
                "source_group_index": 1,
                "source_slot_key": "mon_pm1",
                "target_group_index": 1,
                "target_slot_key": "mon_pm2",
                "move_type": "single_block",
                "action_type": "displacement_move",
                "created_at": "2026-06-14T12:00:00Z",
                "status": "active",
            }],
        }
        self.schedule.save(update_fields=["sched_data"])

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.get(reverse("sched-detail", args=[self.schedule.id]))

        holding_item = response.context["holding_area_preview"][0]
        self.assertEqual(holding_item["activity_name"], displaced.course_name)
        self.assertEqual(holding_item["origin_group_index"], 1)
        self.assertEqual(holding_item["origin_group_label"], "Proposal School 1")
        self.assertContains(response, "Proposal School 1")
        self.assertNotEqual(holding_item["origin_group_label"], "Proposal School 0")

    def test_stale_persisted_override_warns_without_crashing_render(self):
        activity = Course.objects.create(course_name="Current Generated Activity", abriviation="CGA", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [activity.course_name],
            "tue_am1": ["empty"],
        }
        self.schedule.sched_data = {
            "version": 1,
            "manual_moves": [{
                "source_block_id": "0:mon_pm1",
                "source_activity_id": activity.id,
                "source_activity_name": "Old Activity Name",
                "source_occurrence_id": "occurrence:0:mon_pm1",
                "source_group_index": 0,
                "source_slot_key": "mon_pm1",
                "target_group_index": 0,
                "target_slot_key": "tue_am1",
                "move_type": "single_block",
                "created_at": "2026-06-14T12:00:00Z",
                "status": "active",
            }],
        }
        self.schedule.save(update_fields=["sched_data"])

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.get(reverse("sched-detail", args=[self.schedule.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["schedule_rows"][0]["cells"][0]["raw_value"], activity.course_name)
        self.assertEqual(response.context["schedule_rows"][0]["cells"][3]["raw_value"], "empty")
        self.assertContains(response, "persisted_override_replay")
        self.assertContains(response, "is stale")
        self.assertContains(response, "Replay status:")
        self.assertContains(response, "stale")

    def test_chained_overlap_rearrangement_keeps_all_activities_visible(self):
        first = Course.objects.create(course_name="Rendered Chain First", abriviation="RCF", course_len=1)
        second = Course.objects.create(course_name="Rendered Chain Second", abriviation="RCS", course_len=1)
        generated_schedule = {
            "ags": ["Proposal School 0"],
            "mon_pm1": [first.course_name],
            "mon_pm2": [second.course_name],
            "tue_am1": ["empty"],
        }
        self.schedule.sched_data = {
            "version": 1,
            "manual_moves": [
                {
                    "source_block_id": "0:mon_pm1",
                    "source_activity_id": first.id,
                    "source_activity_name": first.course_name,
                    "source_occurrence_id": "occurrence:0:mon_pm1",
                    "source_group_index": 0,
                    "source_slot_key": "mon_pm1",
                    "target_group_index": 0,
                    "target_slot_key": "mon_pm2",
                    "move_type": "single_block",
                    "created_at": "2026-06-14T12:00:00Z",
                    "status": "active",
                },
                {
                    "source_block_id": "0:mon_pm2",
                    "source_activity_id": second.id,
                    "source_activity_name": second.course_name,
                    "source_occurrence_id": "occurrence:0:mon_pm2",
                    "source_group_index": 0,
                    "source_slot_key": "mon_pm2",
                    "target_group_index": 0,
                    "target_slot_key": "tue_am1",
                    "move_type": "single_block",
                    "created_at": "2026-06-14T12:01:00Z",
                    "status": "active",
                },
            ],
        }
        self.schedule.save(update_fields=["sched_data"])

        self.store_generated_schedule(generated_schedule)
        stored_before = deepcopy(self.schedule.sched_data)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.get(reverse("sched-detail", args=[self.schedule.id]))

        self.schedule.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, first.course_name)
        self.assertContains(response, second.course_name)
        self.assertEqual(response.context["schedule_rows"][0]["cells"][1]["raw_value"], first.course_name)
        self.assertEqual(response.context["schedule_rows"][0]["cells"][3]["raw_value"], second.course_name)
        self.assertEqual(len(response.context["override_replay_result"]["applied_overrides"]), 2)
        self.assertEqual(self.schedule.sched_data, stored_before)

    def move_post_data(
        self,
        activity,
        source_block="0:mon_pm1",
        target_slot="tue_am1",
        action_type="displacement_move",
        source_occurrence_id=None,
    ):
        return {
            "source_block": source_block,
            "source_activity_id": activity.id,
            "source_activity_name": activity.course_name,
            "source_occurrence_id": source_occurrence_id or f"occurrence:{source_block}",
            "target_slot": target_slot,
            "target_group": "0",
            "action_type": action_type,
            "can_save": "manipulated-client-value",
        }

    def test_selecting_first_half_of_multi_block_occurrence_highlights_both_cells(self):
        activity = Course.objects.create(course_name="Selectable Two Block", abriviation="S2B", course_len=2)
        generated_schedule = {
            "ags": ["Selectable School 0"],
            "tue_am1": [activity.course_name],
            "tue_am2": [activity.course_name],
        }
        url = f'{reverse("sched-detail", args=[self.schedule.id])}?selected_block=0:tue_am1'

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.get(url)

        selected_block = response.context["selected_block"]
        first_block, second_block = response.context["schedule_rows"][0]["cells"][3:5]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(selected_block["block_id"], "0:tue_am1")
        self.assertEqual(selected_block["occurrence_id"], "occurrence:0:tue_am1")
        self.assertEqual(response.context["selected_occurrence_id"], selected_block["occurrence_id"])
        self.assertEqual(first_block["occurrence_id"], second_block["occurrence_id"])
        self.assertEqual(selected_block["occurrence_length"], 2)
        self.assertEqual(selected_block["occurrence_position"], 1)
        self.assertTrue(selected_block["is_multi_block"])
        self.assertContains(response, 'class="table-primary"', count=2, html=False)

    def test_selecting_second_half_of_multi_block_occurrence_highlights_both_cells_and_shows_metadata(self):
        activity = Course.objects.create(course_name="Selectable Two Block", abriviation="S2B", course_len=2)
        generated_schedule = {
            "ags": ["Selectable School 0"],
            "tue_am1": [activity.course_name],
            "tue_am2": [activity.course_name],
        }
        url = f'{reverse("sched-detail", args=[self.schedule.id])}?selected_block=0:tue_am2'

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.get(url)

        selected_block = response.context["selected_block"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(selected_block["block_id"], "0:tue_am2")
        self.assertEqual(response.context["selected_occurrence_id"], "occurrence:0:tue_am1")
        self.assertContains(response, 'class="table-primary"', count=2, html=False)
        self.assertContains(response, "Multi-block activity occurrence")
        self.assertContains(response, "Occurrence Length")
        self.assertContains(response, "2 blocks")
        self.assertContains(response, "Selected Position")
        self.assertContains(response, "Block 2 of 2")
        self.assertContains(response, "Preview Move")
        self.assertContains(response, "Proposed Starting Time Block")

    def test_schedule_detail_does_not_make_empty_or_unavailable_cells_selectable(self):
        generated_schedule = {
            "ags": ["Selectable School 0"],
            "mon_pm1": ["empty"],
            "mon_pm2": ["g_box"],
        }

        for block_id in ("0:mon_pm1", "0:mon_pm2"):
            with self.subTest(block_id=block_id):
                url = f'{reverse("sched-detail", args=[self.schedule.id])}?selected_block={block_id}'
                self.store_generated_schedule(generated_schedule)
                with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:

                    create_sched.return_value = generated_schedule
                    response = self.client.get(url)

                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.context["selected_block"])
                self.assertIsNone(response.context["selected_occurrence_id"])
                self.assertNotContains(response, "Selected Activity Block")
                self.assertNotContains(response, f'?selected_block={block_id.replace(":", "%3A")}')

    def test_schedule_detail_ignores_invalid_selected_block_id(self):
        generated_schedule = {
            "ags": ["Selectable School 0"],
            "mon_pm1": ["Selectable Activity"],
        }
        url = f'{reverse("sched-detail", args=[self.schedule.id])}?selected_block=invalid'

        self.store_generated_schedule(generated_schedule)

        with patch.object(TheSched, "create_sched", new_callable=PropertyMock) as create_sched:
            create_sched.return_value = generated_schedule
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["selected_block"])
        self.assertIsNone(response.context["selected_occurrence_id"])
        self.assertNotContains(response, "Selected Activity Block")

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
        self.schedule.generate_and_store_schedule()
        request = RequestFactory().get(reverse("sched-detail", args=[self.schedule.id]))
        response = SchedDetail.as_view()(request, pk=self.schedule.id)
        response.render()
        return response, response.content.decode()

    def export_schedule_csv(self):
        self.schedule.generate_and_store_schedule()
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

        self.assertEqual(self.schedule.generation_diagnostics, [])
        self.assertTrue(self.schedule.generation_complete)
        self.assertEqual(generated_schedule["ags"], ["Balanced Regression School 0"])

    def test_schedule_generation_refreshes_blank_sorted_activity_list(self):
        self.assertEqual(self.school.sorted_subject_lst, "")

        generated_schedule = self.schedule.create_sched

        self.assertNotIn("", [activity for values in generated_schedule.values() for activity in values])
        self.school.refresh_from_db()
        self.assertEqual(self.school.sorted_subject_lst, "")

    def test_astronomy_with_valid_locations_generates_without_lookup_key_error(self):
        astronomy = Course.objects.create(course_name="Astronomy", abriviation="AST", course_len=0)
        astronomy.primary_locs.add(Locations.objects.get(loc_name="Schedule Regression Location"))
        self.school.subject.set([self.two_block, self.one_block, astronomy])

        generated_schedule = self.schedule.create_sched

        self.assertEqual(self.schedule.generation_diagnostics, [])
        self.assertTrue(self.schedule.generation_complete)
        self.assertIn("Astronomy", [activity for values in generated_schedule.values() for activity in values])

    def test_generation_survives_global_lookup_clear_after_snapshots_are_built(self):
        original_update_sorted_subject_lst = Schools.update_sorted_subject_lst

        def clear_global_lookups_during_school_sort(school, class_len_lookup=None, class_locs_lookup=None):
            class_locs.clear()
            class_len.clear()
            master_locs.clear()
            return original_update_sorted_subject_lst(school, class_len_lookup, class_locs_lookup)

        with patch.object(
            Schools,
            "update_sorted_subject_lst",
            autospec=True,
            side_effect=clear_global_lookups_during_school_sort,
        ):
            generated_schedule = self.schedule.create_sched

        self.assertEqual(self.schedule.generation_diagnostics, [])
        self.assertTrue(self.schedule.generation_complete)
        self.assertEqual(generated_schedule["ags"], ["Balanced Regression School 0"])
        self.assertIn(
            "Regression One Block",
            [activity for values in generated_schedule.values() for activity in values],
        )

    def test_create_sched_does_not_reinitialize_lookups_inside_school_sorting(self):
        with patch("scheduler_app.models.initialize_scheduling_data", wraps=initialize_scheduling_data) as initializer:
            generated_schedule = self.schedule.create_sched

        self.assertEqual(initializer.call_count, 1)
        self.assertTrue(self.schedule.generation_complete)
        self.assertEqual(generated_schedule["ags"], ["Balanced Regression School 0"])

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

    def test_school_save_keeps_activity_with_no_available_locations(self):
        unavailable_location = Locations.objects.create(
            loc_name="Unavailable Save Location",
            loc_short="USL",
            availible=False,
        )
        activity = Course.objects.create(
            course_name="Unavailable Save Activity",
            abriviation="USA",
            course_len=1,
        )
        activity.primary_locs.add(unavailable_location)
        self.school.subject.set([self.two_block, activity, self.night])
        initialize_scheduling_data(force=True)

        self.school.save()

        self.school.refresh_from_db()
        self.assertEqual(
            list(self.school.subject.order_by("course_name")),
            [self.night, self.two_block, activity],
        )
        self.assertEqual(
            self.school.sorted_subject_lst,
            "Regression Two Block,Unavailable Save Activity,Regression Night",
        )
        self.assertNotIn("Unavailable Save Activity", class_locs)

    def make_location_valid_schedule_unassignable(self):
        location = Locations.objects.get(loc_name="Schedule Regression Location")
        extra_two_block = Course.objects.create(course_name="Competing Two Block", abriviation="C2", course_len=2)
        extra_two_block.primary_locs.add(location)
        self.school.subject.set([self.two_block, extra_two_block, self.one_block, self.night])

    def make_insufficient_night_capacity_schedule(self):
        limited_location = Locations.objects.create(
            loc_name="Limited Night Location",
            loc_short="LNL",
        )
        limited_night = Course.objects.create(
            course_name="Limited Night Activity",
            abriviation="LNA",
            course_len=0,
        )
        limited_night.primary_locs.add(limited_location)
        school = Schools.schools_list.create(
            school_name="Limited Night School",
            arrive="Thur",
            depart="Fri",
            total_students=32,
            ag_num=2,
            attending_year="2026-06-04",
        )
        school.subject.set([limited_night])
        schedule = TheSched.objects.create(sched_name="Limited Night Schedule", sched_data={})
        schedule.schools.add(school)
        return schedule

    def make_activity(self, name, abbreviation, course_len, location=None):
        activity = Course.objects.create(
            course_name=name,
            abriviation=abbreviation,
            course_len=course_len,
        )
        if location:
            activity.primary_locs.add(location)
        return activity

    def diagnostic_types(self, schedule):
        return {
            diagnostic["type"]
            for diagnostic in schedule.generation_runtime_diagnostics
        }

    def assert_runtime_diagnostics_have_required_fields(self, schedule):
        self.assertTrue(schedule.generation_runtime_diagnostics)
        for diagnostic in schedule.generation_runtime_diagnostics:
            with self.subTest(diagnostic=diagnostic):
                self.assertIn("type", diagnostic)
                self.assertIn("severity", diagnostic)
                self.assertIn("reason", diagnostic)
                self.assertTrue(diagnostic["type"])
                self.assertTrue(diagnostic["severity"])
                self.assertTrue(diagnostic["reason"])
                if diagnostic["type"] == "activity_unscheduled":
                    self.assertIn("root_cause", diagnostic)
                    self.assertIn("root_cause_reason", diagnostic)
                    self.assertTrue(diagnostic["root_cause"])
                    self.assertTrue(diagnostic["root_cause_reason"])

    def test_generation_completion_summary_classifies_localized_failure(self):
        summary = summarize_generation_completion(
            {
                "mon_pm1": ["Activity 1"],
                "mon_pm2": ["Activity 2"],
                "mon_night": ["Activity 3"],
                "tue_am1": ["Activity 4"],
            },
            [{
                "school": "Summary School",
                "group": "Summary School 0",
                "activities": ["Activity 1", "Activity 2", "Activity 3", "Activity 4", "Activity 5"],
            }],
            {
                "Activity 1": 1,
                "Activity 2": 1,
                "Activity 3": 0,
                "Activity 4": 1,
                "Activity 5": 1,
            },
        )

        self.assertEqual(summary["expected_assignments"], 5)
        self.assertEqual(summary["successful_assignments"], 4)
        self.assertEqual(summary["unscheduled_assignments"], 1)
        self.assertEqual(summary["completion_percentage"], 80.0)
        self.assertEqual(summary["outcome_severity"], "localized_failure")

    def test_generation_completion_summary_classifies_widespread_failure(self):
        summary = summarize_generation_completion(
            {
                "mon_pm1": ["Activity 1"],
                "mon_pm2": ["empty"],
                "mon_night": ["empty"],
                "tue_am1": ["empty"],
            },
            [{
                "school": "Summary School",
                "group": "Summary School 0",
                "activities": ["Activity 1", "Activity 2", "Activity 3", "Activity 4", "Activity 5"],
            }],
            {
                "Activity 1": 1,
                "Activity 2": 1,
                "Activity 3": 0,
                "Activity 4": 1,
                "Activity 5": 1,
            },
        )

        self.assertEqual(summary["expected_assignments"], 5)
        self.assertEqual(summary["successful_assignments"], 1)
        self.assertEqual(summary["unscheduled_assignments"], 4)
        self.assertEqual(summary["completion_percentage"], 20.0)
        self.assertEqual(summary["outcome_severity"], "widespread_failure")

    def test_generation_collapse_explanation_uses_proven_capacity_bottleneck(self):
        explanation = build_generation_collapse_explanation(
            {
                "outcome_severity": "widespread_failure",
                "completion_percentage": 0.0,
            },
            [{
                "type": "capacity",
                "activity": "Games Games Games",
                "demand": 7,
                "capacity": 2,
            }],
        )

        self.assertIsNotNone(explanation)
        self.assertEqual(
            explanation["heading"],
            "Generation-wide failure was caused by an unschedulable required activity.",
        )
        self.assertEqual(
            explanation["bottleneck_reason"],
            "Games Games Games requires 7 placements but only 2 are available.",
        )
        self.assertIn("requires a complete solution", explanation["scheduler_reason"])

    def test_generation_collapse_explanation_ignores_localized_failure(self):
        explanation = build_generation_collapse_explanation(
            {
                "outcome_severity": "localized_failure",
                "completion_percentage": 80.0,
            },
            [{
                "type": "capacity",
                "activity": "Astronomy",
                "demand": 7,
                "capacity": 6,
            }],
        )

        self.assertIsNone(explanation)

    def test_localized_failure_explanation_uses_unscheduled_root_cause(self):
        explanations = build_localized_failure_explanations(
            {
                "outcome_severity": "localized_failure",
                "completion_percentage": 98.6,
            },
            [
                {
                    "type": "generation_search_exhausted",
                    "severity": "error",
                    "reason": "Schedule generation exhausted available placement options.",
                },
                {
                    "type": "activity_unscheduled",
                    "severity": "error",
                    "school": "Hallz",
                    "group": "Hallz 5",
                    "activity": "Astronomy",
                    "reason": "Hallz 5 could not schedule Astronomy.",
                    "root_cause": "capacity_shortfall",
                    "root_cause_reason": "Astronomy is operating at maximum available capacity.",
                },
            ],
            [{
                "activity": "Astronomy",
                "unscheduled_count": 1,
                "eligible_locations": ["Acct", "Overlook", "Pond"],
            }],
        )

        self.assertEqual(explanations, [{
            "activity": "Astronomy",
            "group": "Hallz 5",
            "heading": "Astronomy could not be scheduled for Hallz 5.",
            "root_cause_reason": "Astronomy is operating at maximum available capacity.",
            "eligible_locations": ["Acct", "Overlook", "Pond"],
            "search_exhausted": True,
        }])

    def test_location_valid_schedule_that_cannot_fit_reports_incomplete_generation(self):
        self.make_location_valid_schedule_unassignable()

        generated_schedule = self.schedule.create_sched

        self.assertFalse(self.schedule.generation_complete)
        self.assertEqual(self.schedule.generation_diagnostics, [])
        self.assertEqual(generated_schedule["ags"], ["Balanced Regression School 0"])

    def test_incomplete_generation_reports_unscheduled_activities(self):
        self.make_location_valid_schedule_unassignable()

        with patch("scheduler_app.models.audit_schedule_feasibility", return_value={
            "diagnostics": [],
            "errors": [],
            "warnings": [],
            "info": [],
            "blocks_generation": False,
        }):
            self.schedule.create_sched

        diagnostics = self.schedule.generation_runtime_diagnostics
        unscheduled = [
            diagnostic
            for diagnostic in diagnostics
            if diagnostic["type"] == "activity_unscheduled"
        ]
        self.assertIn("generation_search_exhausted", self.diagnostic_types(self.schedule))
        self.assertTrue(unscheduled)
        self.assertTrue(any(
            diagnostic["school"] == "Balanced Regression School"
            and diagnostic["group"] == "Balanced Regression School 0"
            and diagnostic["activity"] == "Competing Two Block"
            and diagnostic["severity"] == "error"
            and diagnostic["root_cause"] == "search_exhaustion_unknown"
            and "could not place this activity" in diagnostic["reason"]
            and "No specific capacity, location, or trip-window shortfall" in diagnostic["root_cause_reason"]
            for diagnostic in unscheduled
        ))
        self.assert_runtime_diagnostics_have_required_fields(self.schedule)

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

    def test_schedule_detail_renders_localized_root_cause_before_generic_message(self):
        generated_schedule = self.schedule.create_sched
        self.schedule.generation_complete = False
        self.schedule.generation_diagnostics = []
        self.schedule.generation_runtime_diagnostics = [
            {
                "type": "generation_search_exhausted",
                "severity": "error",
                "reason": "Schedule generation exhausted available placement options before completing the schedule.",
            },
            {
                "type": "activity_unscheduled",
                "severity": "error",
                "school": "Balanced Regression School",
                "group": "Balanced Regression School 0",
                "activity": "Regression One Block",
                "reason": "Balanced Regression School 0 could not schedule Regression One Block.",
                "root_cause": "capacity_shortfall",
                "root_cause_reason": "Regression One Block is operating at maximum available capacity.",
            },
            {
                "type": "generation_outcome_summary",
                "severity": "error",
                "outcome_severity": "localized_failure",
                "expected_assignments": 69,
                "successful_assignments": 68,
                "unscheduled_assignments": 1,
                "completion_percentage": 98.6,
                "search_limit_exceeded": False,
                "search_exhausted": True,
                "reason": "Schedule generation is mostly complete: 68 of 69 expected assignments were scheduled (98.6% complete).",
            },
        ]
        self.schedule.store_generated_schedule(generated_schedule)

        request = RequestFactory().get(reverse("sched-detail", args=[self.schedule.id]))
        response = SchedDetail.as_view()(request, pk=self.schedule.id)
        response.render()
        rendered_content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Localized generation failure")
        self.assertContains(response, "Regression One Block could not be scheduled for Balanced Regression School 0.")
        self.assertContains(response, "Eligible locations:")
        self.assertContains(response, "Schedule Regression Location")
        self.assertContains(response, "Regression One Block is operating at maximum available capacity.")
        self.assertContains(
            response,
            "The scheduler exhausted available placement options while attempting to place this activity.",
        )
        self.assertIn("<details", rendered_content)
        self.assertNotIn("<details open", rendered_content)
        self.assertIn("Additional Diagnostic Details", rendered_content)
        self.assertIn("Affected Activities:", rendered_content)
        self.assertIn("Regression One Block", rendered_content)
        self.assertIn("Technical diagnostics:", rendered_content)
        self.assertNotIn("This may be due to Location capacity", rendered_content)
        self.assertLess(
            rendered_content.index("Localized generation failure"),
            rendered_content.index("Regression One Block could not be scheduled"),
        )
        self.assertLess(
            rendered_content.index("Regression One Block is operating at maximum available capacity."),
            rendered_content.index("Additional Diagnostic Details"),
        )
        self.assertLess(
            rendered_content.index("Additional Diagnostic Details"),
            rendered_content.index("Affected Activities:"),
        )
        self.assertLess(
            rendered_content.index("Affected Activities:"),
            rendered_content.index("Technical diagnostics:"),
        )

    def test_insufficient_activity_capacity_is_caught_before_recursive_search(self):
        schedule = self.make_insufficient_night_capacity_schedule()

        with patch("scheduler_app.models.GENERATION_SEARCH_MAX_ATTEMPTS", 0):
            generated_schedule = schedule.create_sched

        self.assertFalse(schedule.generation_complete)
        self.assertEqual(schedule.generation_diagnostics, [])
        self.assertEqual(generated_schedule["ags"], ["Limited Night School 0", "Limited Night School 1"])
        self.assertNotIn("classes_needed", generated_schedule)
        diagnostic = next(
            diagnostic
            for diagnostic in schedule.generation_runtime_diagnostics
            if diagnostic["type"] == "activity_capacity_insufficient"
        )
        self.assertEqual(diagnostic["type"], "activity_capacity_insufficient")
        self.assertEqual(diagnostic["school"], "Limited Night School")
        self.assertEqual(diagnostic["activity"], "Limited Night Activity")
        self.assertEqual(diagnostic["demand"], 2)
        self.assertEqual(diagnostic["capacity"], 1)
        self.assertIn("Limited Night School — Limited Night Activity needs 2 placements", diagnostic["reason"])
        self.assertIn("only 1 is available", diagnostic["reason"])

    def test_capacity_insufficiency_adds_unscheduled_root_cause_attribution(self):
        schedule = self.make_insufficient_night_capacity_schedule()

        with patch("scheduler_app.models.GENERATION_SEARCH_MAX_ATTEMPTS", 0):
            schedule.create_sched

        unscheduled = [
            diagnostic
            for diagnostic in schedule.generation_runtime_diagnostics
            if diagnostic["type"] == "activity_unscheduled"
        ]
        self.assertEqual(len(unscheduled), 2)
        for diagnostic in unscheduled:
            self.assertEqual(diagnostic["root_cause"], "capacity_shortfall")
            self.assertEqual(diagnostic["school"], "Limited Night School")
            self.assertEqual(diagnostic["activity"], "Limited Night Activity")
            self.assertIn("needs 2 placements", diagnostic["root_cause_reason"])
            self.assertIn("only 1 is available", diagnostic["root_cause_reason"])
        self.assert_runtime_diagnostics_have_required_fields(schedule)

    def test_insufficient_total_school_blocks_is_caught_before_recursive_search(self):
        broad_location = Locations.objects.create(loc_name="Various", loc_short="VAR")
        selected_activities = [
            self.make_activity(f"Total Block Day {index}", f"TBD{index}", 1, broad_location)
            for index in range(1, 6)
        ]
        selected_activities.append(self.make_activity("Total Block Night", "TBN", 0, broad_location))
        school = Schools.schools_list.create(
            school_name="Total Block School",
            arrive="Mon",
            depart="Tue",
            total_students=16,
            ag_num=1,
            attending_year="2026-06-04",
        )
        school.subject.set(selected_activities)
        schedule = TheSched.objects.create(sched_name="Total Block Schedule", sched_data={})
        schedule.schools.add(school)

        with patch("scheduler_app.models.GENERATION_SEARCH_MAX_ATTEMPTS", 0):
            generated_schedule = schedule.create_sched

        self.assertFalse(schedule.generation_complete)
        self.assertEqual(generated_schedule["ags"], ["Total Block School 0"])
        self.assertIn("school_trip_window_capacity_insufficient", self.diagnostic_types(schedule))
        self.assertNotIn("search_limit_exceeded", self.diagnostic_types(schedule))
        self.assertTrue(any(
            "requires 6 total activity blocks but only 5 usable schedule blocks exist"
            in diagnostic["reason"]
            for diagnostic in schedule.generation_runtime_diagnostics
        ))

    def test_insufficient_two_block_paired_footprints_are_caught_before_recursive_search(self):
        broad_location = Locations.objects.create(loc_name="Two Block Various", loc_short="TBV")
        first_two_block = self.make_activity("Paired Footprint One", "PF1", 2, broad_location)
        second_two_block = self.make_activity("Paired Footprint Two", "PF2", 2, broad_location)
        school = Schools.schools_list.create(
            school_name="Paired Footprint School",
            arrive="Thur",
            depart="Fri",
            total_students=16,
            ag_num=1,
            attending_year="2026-06-04",
        )
        school.subject.set([first_two_block, second_two_block])
        schedule = TheSched.objects.create(sched_name="Paired Footprint Schedule", sched_data={})
        schedule.schools.add(school)

        with patch("scheduler_app.models.GENERATION_SEARCH_MAX_ATTEMPTS", 0):
            generated_schedule = schedule.create_sched

        self.assertFalse(schedule.generation_complete)
        self.assertEqual(generated_schedule["ags"], ["Paired Footprint School 0"])
        self.assertIn("school_two_block_footprint_capacity_insufficient", self.diagnostic_types(schedule))
        self.assertNotIn("search_limit_exceeded", self.diagnostic_types(schedule))
        self.assertTrue(any(
            "requires 2 two-block paired footprints but only 1 usable paired footprints exist"
            in diagnostic["reason"]
            for diagnostic in schedule.generation_runtime_diagnostics
        ))

    def test_hard_location_bottleneck_is_diagnosed_before_search_limit(self):
        bottleneck_location = Locations.objects.create(
            loc_name="Hallz Bottleneck Location",
            loc_short="HBL",
        )
        selected_activities = [
            self.make_activity(f"Hallz Day Activity {index}", f"HDA{index}", 1, bottleneck_location)
            for index in range(1, 9)
        ]
        school = Schools.schools_list.create(
            school_name="Hallz Style School",
            arrive="Mon",
            depart="Fri",
            total_students=32,
            ag_num=2,
            attending_year="2026-06-04",
        )
        school.subject.set(selected_activities)
        schedule = TheSched.objects.create(sched_name="Hallz Style Schedule", sched_data={})
        schedule.schools.add(school)

        with patch("scheduler_app.models.GENERATION_SEARCH_MAX_ATTEMPTS", 0):
            generated_schedule = schedule.create_sched

        self.assertFalse(schedule.generation_complete)
        self.assertEqual(generated_schedule["ags"], ["Hallz Style School 0", "Hallz Style School 1"])
        self.assertIn("location_bottleneck_insufficient", self.diagnostic_types(schedule))
        self.assertNotIn("search_limit_exceeded", self.diagnostic_types(schedule))
        self.assertTrue(any(
            "Hallz Bottleneck Location may be unschedulable: 16 requested placements"
            in diagnostic["reason"]
            for diagnostic in schedule.generation_runtime_diagnostics
        ))

    def test_valid_capacity_still_proceeds_to_recursive_search(self):
        with patch("scheduler_app.models.GENERATION_SEARCH_MAX_ATTEMPTS", 0):
            generated_schedule = self.schedule.create_sched

        self.assertFalse(self.schedule.generation_complete)
        self.assertEqual(generated_schedule["ags"], ["Balanced Regression School 0"])
        self.assertIn("search_limit_exceeded", self.diagnostic_types(self.schedule))
        self.assertIn("activity_unscheduled", self.diagnostic_types(self.schedule))
        self.assertTrue(any(
            diagnostic["type"] == "activity_unscheduled"
            and diagnostic["root_cause"] == "search_exhaustion_unknown"
            for diagnostic in self.schedule.generation_runtime_diagnostics
        ))
        self.assert_runtime_diagnostics_have_required_fields(self.schedule)

    def test_schedule_detail_renders_activity_capacity_diagnostic(self):
        self.schedule = self.make_insufficient_night_capacity_schedule()

        with patch("scheduler_app.models.GENERATION_SEARCH_MAX_ATTEMPTS", 0):
            response, rendered_content = self.render_schedule_detail()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context_data["generation_complete"])
        self.assertContains(response, "Schedule generation is incomplete")
        self.assertContains(response, "Generation-wide failure")
        self.assertContains(response, "only 0 of 2 expected assignments were scheduled")
        self.assertContains(
            response,
            "Generation-wide failure was caused by an unschedulable required activity.",
        )
        self.assertContains(
            response,
            "Limited Night Activity requires 2 placements but only 1 is available.",
        )
        self.assertContains(
            response,
            "Because the scheduler requires a complete solution, this shortage prevented a schedule from being generated.",
        )
        self.assertContains(response, "Primary Bottlenecks:")
        self.assertContains(response, "Limited Night Activity Capacity Bottleneck")
        self.assertContains(response, "Total demand: 2 placements")
        self.assertContains(response, "Total available capacity: 1 placements")
        self.assertContains(response, "Shortfall: 1 placements")
        self.assertContains(response, "Affected Activities:")
        self.assertContains(response, "Limited Night Activity")
        self.assertContains(response, "2 unscheduled assignments")
        self.assertIn("<details", rendered_content)
        self.assertNotIn("<details open", rendered_content)
        self.assertIn("Additional Diagnostic Details", rendered_content)
        self.assertIn("Eligible locations:", rendered_content)
        self.assertIn("Limited Night Location", rendered_content)
        self.assertIn("Limited Night School — Limited Night Activity needs 2 placements", rendered_content)
        self.assertIn("only 1 is available", rendered_content)
        self.assertIn("Technical diagnostics:", rendered_content)
        self.assertNotIn("Activity and Location configuration passed initial checks", rendered_content)
        self.assertNotIn("This may be due to Location capacity", rendered_content)
        self.assertLess(
            rendered_content.index("Generation-wide failure"),
            rendered_content.index("Generation-wide failure was caused by an unschedulable required activity."),
        )
        self.assertLess(
            rendered_content.index("Generation-wide failure was caused by an unschedulable required activity."),
            rendered_content.index("Primary Bottlenecks:"),
        )
        self.assertLess(
            rendered_content.index("Primary Bottlenecks:"),
            rendered_content.index("Additional Diagnostic Details"),
        )
        self.assertLess(
            rendered_content.index("Additional Diagnostic Details"),
            rendered_content.index("Affected Activities:"),
        )
        self.assertLess(
            rendered_content.index("Affected Activities:"),
            rendered_content.index("Technical diagnostics:"),
        )
        self.assertIn("<table", rendered_content)

    def test_generation_search_limit_returns_incomplete_schedule(self):
        with patch("scheduler_app.models.GENERATION_SEARCH_MAX_ATTEMPTS", 1):
            generated_schedule = self.schedule.create_sched

        self.assertFalse(self.schedule.generation_complete)
        self.assertEqual(self.schedule.generation_diagnostics, [])
        self.assertEqual(generated_schedule["ags"], ["Balanced Regression School 0"])
        self.assertNotIn("classes_needed", generated_schedule)
        self.assertIn(
            {
                "type": "search_limit_exceeded",
                "severity": "warning",
                "reason": (
                    "Schedule generation stopped because the recursive assignment "
                    "search reached the safety limit of 1 attempts."
                ),
            },
            self.schedule.generation_runtime_diagnostics,
        )
        self.assertIn("activity_unscheduled", self.diagnostic_types(self.schedule))
        self.assertTrue(any(
            diagnostic["type"] == "activity_unscheduled"
            and diagnostic["school"] == "Balanced Regression School"
            and diagnostic["group"] == "Balanced Regression School 0"
            and diagnostic["root_cause"] == "search_exhaustion_unknown"
            and diagnostic["activity"] in {
                "Regression Two Block",
                "Regression One Block",
                "Regression Night",
            }
            for diagnostic in self.schedule.generation_runtime_diagnostics
        ))
        self.assert_runtime_diagnostics_have_required_fields(self.schedule)

    def test_schedule_detail_renders_when_generation_search_limit_is_exceeded(self):
        with patch("scheduler_app.models.GENERATION_SEARCH_MAX_ATTEMPTS", 1):
            response, rendered_content = self.render_schedule_detail()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context_data["generation_complete"])
        self.assertContains(response, "Schedule generation is incomplete")
        self.assertContains(response, "Generation-wide failure")
        self.assertIn("1 of 3 expected assignments were scheduled", rendered_content)
        self.assertContains(response, "Affected Activities:")
        self.assertContains(response, "Regression One Block")
        self.assertContains(response, "1 unscheduled assignment")
        self.assertIn("search reached the safety limit of 1 attempts", rendered_content)
        self.assertIn("Technical diagnostics:", rendered_content)
        self.assertIn("Incomplete Schedule Output", rendered_content)
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
        self.assertIn("Schedule generated successfully", rendered_content)
        self.assertIn("Selected Schools", rendered_content)
        self.assertIn("Balanced Regression School", rendered_content)
        self.assertIn("Balanced Regression School 0", rendered_content)
        self.assertIn("Regression Two Block", rendered_content)
        self.assertIn("Regression One Block", rendered_content)
        self.assertIn("Regression Night", rendered_content)
        self.assertIn("<table", rendered_content)
        self.assertIn("Export CSV", rendered_content)
        self.assertIn("Download stored generated schedule for spreadsheet review", rendered_content)
        self.assertIn(reverse("sched-export", args=[self.schedule.id]), rendered_content)
        self.assertNotIn("Primary Bottlenecks:", rendered_content)
        self.assertNotIn("Affected Activities:", rendered_content)
        self.assertNotIn("Eligible Regression Two Block locations:", rendered_content)

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
class PublicLandingPageTests(TestCase):
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

    def test_validation_summary_counts_monday_to_wednesday_trip_window(self):
        summary = calculate_school_slot_accounting("Mon", "Wed", [])

        self.assertEqual(summary["required_daytime"], 7)
        self.assertEqual(summary["required_night"], 2)
        self.assertEqual(summary["required_total"], 9)

    def test_validation_summary_counts_monday_to_thursday_trip_window(self):
        summary = calculate_school_slot_accounting("Mon", "Thur", [])

        self.assertEqual(summary["required_daytime"], 11)
        self.assertEqual(summary["required_night"], 3)
        self.assertEqual(summary["required_total"], 14)

    def test_validation_summary_counts_tuesday_to_friday_trip_window(self):
        summary = calculate_school_slot_accounting("Tue", "Fri", [])

        self.assertEqual(summary["required_daytime"], 11)
        self.assertEqual(summary["required_night"], 3)
        self.assertEqual(summary["required_total"], 14)

    def test_validation_summary_counts_match_trip_window_capacity(self):
        for arrive, depart in (("Mon", "Wed"), ("Mon", "Thur"), ("Tue", "Fri")):
            with self.subTest(arrive=arrive, depart=depart):
                slot_blocks = school_validation_slot_blocks(arrive, depart)
                summary = calculate_school_slot_accounting(arrive, depart, [])

                self.assertEqual(
                    summary["required_daytime"],
                    sum(1 for _slot_key, slot_kind in slot_blocks if slot_kind == "daytime"),
                )
                self.assertEqual(
                    summary["required_night"],
                    sum(1 for _slot_key, slot_kind in slot_blocks if slot_kind == "night"),
                )
                self.assertEqual(summary["required_total"], len(slot_blocks))

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
        self.assertContains(
            response,
            f'name="subject" value="{self.wm.id}" id="id_subject_0_0" checked',
            html=False,
        )
        self.assertContains(
            response,
            f'name="subject" value="{self.night_hike.id}" id="id_subject_2_0" checked',
            html=False,
        )
        self.assertNotContains(
            response,
            f'name="subject" value="{self.archery.id}" id="id_subject_1_0" checked',
            html=False,
        )

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
        self.assertNotContains(response, "sorted_subject_lst")
        self.assertNotContains(response, "Sorted Subject")
        self.assertContains(response, 'data-summary-value="required-total"', html=False)
        self.assertContains(response, "data-summary-value=\"selected-daytime\">0</span>")
        self.assertContains(response, "data-summary-value=\"selected-night\">0</span>")
        self.assertContains(response, "const scheduleSlotBlocks = [", html=False)
        self.assertContains(response, "arrive?.addEventListener('change', updateSummary);", html=False)
        self.assertContains(response, "depart?.addEventListener('change', updateSummary);", html=False)

    def test_school_update_page_includes_slot_accounting_summary(self):
        school = Schools(school_name="Summary Test School", arrive="Mon", depart="Wed", total_students=16, ag_num=1, attending_year="2026-06-04")
        school.save()
        school.subject.set([self.wm, self.night_hike])
        school.update_sorted_subject_lst()
        school.save(update_fields=["sorted_subject_lst"])

        response = self.client.get(f"/school_update/{school.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Schedule Block Summary")
        self.assertNotContains(response, "sorted_subject_lst")
        self.assertNotContains(response, "Sorted Subject")
        self.assertContains(response, 'data-summary-value="required-daytime">7</span>', html=False)
        self.assertContains(response, 'data-summary-value="required-night">2</span>', html=False)
        self.assertContains(response, 'data-summary-value="required-total">9</span>', html=False)
        self.assertContains(response, "data-summary-value=\"selected-daytime\">2</span>")
        self.assertContains(response, "data-summary-value=\"selected-night\">1</span>")
        self.assertContains(response, "data-summary-status=\"daytime\">under by 5</span>")
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
        self.assertNotContains(response, "sorted_subject_lst")
        self.assertNotContains(response, "Sorted Subject")
        self.assertContains(response, 'data-summary-value="required-daytime">7</span>', html=False)
        self.assertContains(response, 'data-summary-value="required-night">2</span>', html=False)
        self.assertContains(response, 'data-summary-value="required-total">9</span>', html=False)
        self.assertContains(response, "data-summary-value=\"selected-daytime\">1</span>")
        self.assertContains(response, "data-summary-value=\"selected-night\">1</span>")
        self.assertContains(response, "data-summary-status=\"daytime\">under by 6</span>")
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
