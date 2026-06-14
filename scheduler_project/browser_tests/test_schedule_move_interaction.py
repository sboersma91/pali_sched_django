from importlib.util import find_spec
from unittest import SkipTest, skipUnless

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse

PLAYWRIGHT_AVAILABLE = find_spec("playwright") is not None
if PLAYWRIGHT_AVAILABLE:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

from scheduler_app.models import TheSched
from scheduler_app.schedule_blocks import SCHEDULE_BLOCK_KEYS
from scheduler_app.schedule_cells import generated_schedule_cell


@skipUnless(PLAYWRIGHT_AVAILABLE, "Install requirements-dev.txt to run Playwright browser tests.")
class ScheduleMoveInteractionTests(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch()
        except PlaywrightError as error:
            cls.playwright.stop()
            raise SkipTest(
                "Playwright Chromium is unavailable. Run `python -m playwright install chromium`."
            ) from error

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "browser"):
            cls.browser.close()
        if hasattr(cls, "playwright"):
            cls.playwright.stop()
        super().tearDownClass()

    def setUp(self):
        self.schedule = TheSched.objects.create(
            sched_name="Browser Move Schedule",
            sched_data={
                "mode": "persisted",
                "schedule": self.two_block_payload(),
                "generation_complete": True,
            },
        )
        self.page = self.browser.new_page()
        self.page.goto(self.live_server_url + reverse("sched-detail", args=[self.schedule.id]))

    def tearDown(self):
        self.page.close()

    @staticmethod
    def two_block_payload():
        payload = {key: ["g_box"] for key in SCHEDULE_BLOCK_KEYS}
        payload["ags"] = ["Browser Group 0"]
        payload["mon_pm1"][0] = generated_schedule_cell(
            activity_name="Ropes",
            activity_id=13,
            location_name="Course",
            location_id=5,
            assignment_id="assignment-1",
            assignment_part=1,
            assignment_span=2,
        )
        payload["mon_pm2"][0] = generated_schedule_cell(
            activity_name="Ropes",
            activity_id=13,
            location_name="Course",
            location_id=5,
            assignment_id="assignment-1",
            assignment_part=2,
            assignment_span=2,
        )
        payload["tue_am1"][0] = "empty"
        payload["tue_am2"][0] = "empty"
        return payload

    def cell(self, block_key):
        return self.page.locator(f'[data-schedule-cell][data-block-key="{block_key}"]')

    def select_assignment(self):
        self.cell("mon_pm2").locator("div").first.click()

    def pointer_position(self, block_key, assignment_content=False):
        locator = self.cell(block_key).locator("div").first if assignment_content else self.cell(block_key)
        box = locator.bounding_box()
        return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    def test_selects_linked_assignment_and_only_approved_destinations(self):
        self.select_assignment()

        self.assertEqual(self.page.locator(".schedule-cell-selected").count(), 2)
        self.assertEqual(self.page.locator(".schedule-cell-valid-destination").count(), 2)
        self.assertTrue(self.cell("tue_am1").get_attribute("aria-label").startswith("Move Ropes to"))
        self.assertEqual(self.cell("tue_pm1").get_attribute("role"), None)
        self.assertFalse(self.page.locator("[data-schedule-move-selection-status]").is_hidden())

        self.cell("tue_am1").hover()
        self.assertIn("schedule_detail", self.page.url)

    def test_escape_outside_click_and_reclick_clear_selection(self):
        for cancel_action in (
            lambda: self.page.keyboard.press("Escape"),
            lambda: self.page.locator("h1").click(),
            lambda: self.cell("mon_pm1").locator("div").first.click(),
            lambda: self.page.locator("[data-schedule-move-cancel]").click(),
        ):
            self.select_assignment()
            cancel_action()
            self.assertEqual(self.page.locator(".schedule-cell-selected").count(), 0)
            self.assertEqual(self.page.locator(".schedule-cell-valid-destination").count(), 0)

    def test_clicking_valid_destination_submits_existing_move_form(self):
        self.assertEqual(self.page.locator("[data-schedule-move-form]").count(), 1)

        self.select_assignment()
        self.cell("tue_am1").click()

        self.page.get_by_text("Schedule move saved.").wait_for()
        self.assertEqual(self.cell("tue_am1").get_attribute("data-assignment-id"), "assignment-1")
        self.assertEqual(self.cell("tue_am2").get_attribute("data-assignment-id"), "assignment-1")

    def test_focused_destination_submits_with_enter(self):
        self.select_assignment()
        self.cell("tue_am1").focus()

        self.page.keyboard.press("Enter")

        self.page.get_by_text("Schedule move saved.").wait_for()
        self.assertEqual(self.cell("tue_am1").get_attribute("data-assignment-id"), "assignment-1")

    def test_focused_destination_submits_with_space(self):
        self.select_assignment()
        self.cell("tue_am1").focus()

        self.page.keyboard.press("Space")

        self.page.get_by_text("Schedule move saved.").wait_for()
        self.assertEqual(self.cell("tue_am1").get_attribute("data-assignment-id"), "assignment-1")

    def test_pointer_drag_activates_only_after_threshold(self):
        source_x, source_y = self.pointer_position("mon_pm2", assignment_content=True)
        self.page.mouse.move(source_x, source_y)
        self.page.mouse.down()
        self.page.mouse.move(source_x + 4, source_y + 4)

        self.assertEqual(self.page.locator(".schedule-drag-active").count(), 0)
        self.assertEqual(self.page.locator(".schedule-cell-selected").count(), 0)

        self.page.mouse.move(source_x + 12, source_y + 12)
        self.assertEqual(self.page.locator(".schedule-drag-active").count(), 1)
        self.assertEqual(self.page.locator(".schedule-cell-selected").count(), 2)
        self.assertEqual(self.page.locator(".schedule-cell-valid-destination").count(), 2)
        self.page.mouse.up()

    def test_pointer_drag_to_valid_destination_submits_existing_move(self):
        source_x, source_y = self.pointer_position("mon_pm2", assignment_content=True)
        destination_x, destination_y = self.pointer_position("tue_am1")

        self.page.mouse.move(source_x, source_y)
        self.page.mouse.down()
        self.page.mouse.move(destination_x, destination_y, steps=5)
        self.page.mouse.up()

        self.page.get_by_text("Schedule move saved.").wait_for()
        self.assertEqual(self.cell("tue_am1").get_attribute("data-assignment-id"), "assignment-1")
        self.assertEqual(self.cell("tue_am2").get_attribute("data-assignment-id"), "assignment-1")

    def test_pointer_drag_to_invalid_destination_cancels(self):
        source_x, source_y = self.pointer_position("mon_pm2", assignment_content=True)
        invalid_x, invalid_y = self.pointer_position("tue_pm1")

        self.page.mouse.move(source_x, source_y)
        self.page.mouse.down()
        self.page.mouse.move(invalid_x, invalid_y, steps=5)
        self.assertEqual(self.page.locator(".schedule-drag-active").count(), 1)
        self.page.mouse.up()

        self.assertEqual(self.page.locator(".schedule-drag-active").count(), 0)
        self.assertEqual(self.page.locator(".schedule-cell-selected").count(), 0)
        self.assertEqual(self.page.locator(".schedule-cell-valid-destination").count(), 0)
        self.assertIn("schedule_detail", self.page.url)
