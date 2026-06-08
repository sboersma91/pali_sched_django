from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import Course, Locations, Schools, class_len, class_locs


@override_settings(ALLOWED_HOSTS=["localhost", "testserver"])
class SchoolFormWorkflowTests(TestCase):
    def setUp(self):
        class_len.update({
            "WM": 2,
            "Night Hike": 0,
            "Archery": 1,
        })
        class_locs.update({
            "WM": ["WM"],
            "Night Hike": ["Various"],
            "Archery": ["Range"],
        })
        self.wm = Course.objects.create(course_name="WM", abriviation="WM", course_len=2)
        self.night_hike = Course.objects.create(course_name="Night Hike", abriviation="NH", course_len=0)
        self.archery = Course.objects.create(course_name="Archery", abriviation="ARCH", course_len=1)
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

    def test_navbar_add_school_links_to_canonical_create_page(self):
        response = self.client.get(reverse("school-create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'<a class="dropdown-item" href="{reverse("school-create")}">Add School</a>',
            html=True,
        )

    def test_school_list_add_new_school_links_to_canonical_create_page(self):
        response = self.client.get(reverse("school-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'<a href="{reverse("school-create")}">Add New School</a>',
            html=True,
        )

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
        self.assertContains(response, "checked", count=2)

    def test_school_create_saves_subjects_after_instance_has_id(self):
        response = self.client.post(
            "/school_create",
            self.school_form_data("Create Test School", [self.wm, self.night_hike]),
        )

        self.assertEqual(response.status_code, 302)
        school = Schools.schools_list.get(school_name="Create Test School")
        self.assertEqual(list(school.subject.order_by("course_name")), [self.night_hike, self.wm])
        self.assertEqual(school.sorted_subject_lst, "WM,Night Hike")

    def test_school_update_refreshes_sorted_subjects_after_subject_changes(self):
        school = Schools(school_name="Update Test School", arrive="Mon", depart="Wed", total_students=16, ag_num=1, attending_year="2026-06-04")
        school.save()
        school.subject.set([self.wm, self.night_hike])
        school.update_sorted_subject_lst()
        school.save(update_fields=["sorted_subject_lst"])

        response = self.client.post(
            f"/school_update/{school.id}/",
            self.school_form_data("Update Test School", [self.archery, self.night_hike], arrive="Tue", depart="Thur"),
        )

        self.assertEqual(response.status_code, 302)
        school.refresh_from_db()
        self.assertEqual(school.arrive, "Tue")
        self.assertEqual(school.depart, "Thur")
        self.assertEqual(list(school.subject.order_by("course_name")), [self.archery, self.night_hike])
        self.assertEqual(school.sorted_subject_lst, "Archery,Night Hike")

    def test_add_school_function_view_uses_valid_form_save(self):
        response = self.client.post(
            "/add_school",
            self.school_form_data("Function Test School", [self.wm, self.night_hike]),
        )

        self.assertEqual(response.status_code, 302)
        school = Schools.schools_list.get(school_name="Function Test School")
        self.assertEqual(school.sorted_subject_lst, "WM,Night Hike")

    def test_add_school_page_includes_slot_accounting_summary(self):
        response = self.client.get("/add_school")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Schedule Block Summary")
        self.assertContains(response, "Selected daytime blocks:</strong> 0")
        self.assertContains(response, "Selected night blocks:</strong> 0")

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
        self.assertContains(response, "Selected daytime blocks:</strong> 2")
        self.assertContains(response, "Selected night blocks:</strong> 1")
        self.assertContains(response, "Daytime status:</strong> under by 6")
        self.assertContains(response, "Night status:</strong> under by 1")

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
        self.assertContains(response, "Selected daytime blocks:</strong> 1")
        self.assertContains(response, "Selected night blocks:</strong> 1")
        self.assertContains(response, "Daytime status:</strong> under by 7")
        self.assertContains(response, "Night status:</strong> under by 1")


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
        self.assertContains(response, "Course Name")
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
        self.assertContains(response, "Edit Course")
        self.assertContains(response, "Delete Course")
        self.assertContains(response, "Back to Courses")

    def test_course_delete_confirmation_renders_destructive_warning(self):
        response = self.client.get(reverse("course-delete", args=[self.course.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm Course Deletion")
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
        self.assertContains(response, "may remove it from Course configurations")
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
