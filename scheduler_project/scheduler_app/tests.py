from django.test import Client, TestCase, override_settings

from .models import Course, Schools, class_len, class_locs


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
