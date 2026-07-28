from datetime import date
from importlib import import_module
from unittest.mock import patch

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class TimestampOgMigrationTests(TransactionTestCase):
    migrate_from = [("scheduler_app", "0022_thesched_sched_data")]
    migrate_to = [("scheduler_app", "0023_thesched_timestamp_og")]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        TheSched = old_apps.get_model("scheduler_app", "TheSched")
        self.schedule_id = TheSched.objects.create(
            sched_name="Historical Schedule",
            sched_data={"version": 1},
        ).pk

        migration = import_module(
            "scheduler_app.migrations.0023_thesched_timestamp_og"
        )
        self.migration_date = date(2026, 7, 27)
        with patch.object(
            migration.timezone,
            "localdate",
            return_value=self.migration_date,
        ):
            executor = MigrationExecutor(connection)
            executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_schedule_receives_migration_date_and_final_field_state(self):
        TheSched = self.apps.get_model("scheduler_app", "TheSched")

        schedule = TheSched.objects.get(pk=self.schedule_id)
        field = TheSched._meta.get_field("timestamp_og")

        self.assertEqual(schedule.timestamp_og, self.migration_date)
        self.assertEqual(field.get_internal_type(), "DateField")
        self.assertTrue(field.auto_now_add)
        self.assertFalse(field.null)
