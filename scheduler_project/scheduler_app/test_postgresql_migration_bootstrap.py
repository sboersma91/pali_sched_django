from datetime import datetime, timezone
from unittest import skipUnless

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


@skipUnless(
    connection.vendor == 'postgresql',
    'PostgreSQL-specific migration regression test',
)
class PostgreSQLMigrationBootstrapTests(TransactionTestCase):
    """Exercise the historical migration path that SQLite cannot reproduce."""

    migrate_from = [('scheduler_app', '0001_initial')]
    migrate_through_conversion = [
        ('scheduler_app', '0002_auto_20220111_2002'),
    ]

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_fresh_history_converts_trip_timestamps_and_reaches_current_schema(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        Schools = old_apps.get_model('scheduler_app', 'Schools')
        school_id = Schools.objects.create(
            school_name='Historical School',
            arrive=datetime(2022, 1, 10, tzinfo=timezone.utc),
            depart=datetime(2022, 1, 14, tzinfo=timezone.utc),
            total_students=20,
        ).pk

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_through_conversion)
        converted_apps = executor.loader.project_state(
            self.migrate_through_conversion
        ).apps
        ConvertedSchools = converted_apps.get_model(
            'scheduler_app',
            'Schools',
        )
        converted = ConvertedSchools.objects.get(pk=school_id)
        self.assertIs(converted.arrive, True)
        self.assertIs(converted.depart, True)
        self.assertEqual(
            ConvertedSchools._meta.get_field('arrive').get_internal_type(),
            'BooleanField',
        )

        executor = MigrationExecutor(connection)
        leaf_nodes = executor.loader.graph.leaf_nodes()
        executor.migrate(leaf_nodes)
        current_apps = executor.loader.project_state(leaf_nodes).apps
        CurrentSchools = current_apps.get_model('scheduler_app', 'Schools')
        arrive_field = CurrentSchools._meta.get_field('arrive')
        self.assertEqual(arrive_field.get_internal_type(), 'CharField')
        self.assertEqual(arrive_field.max_length, 50)
