from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.db import migrations
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ProjectState
from django.test import SimpleTestCase


AUDITED_APPS = {"members", "scheduler_app"}
FORBIDDEN_SQLITE_EXPRESSIONS = (
    'date("now")',
    "date('now')",
    'datetime("now")',
    "datetime('now')",
    "strftime(",
)


def nested_operations(operations):
    for operation in operations:
        yield operation
        for attribute in ("database_operations", "state_operations"):
            yield from nested_operations(getattr(operation, attribute, ()))


class MigrationPortabilityAuditTests(SimpleTestCase):
    def setUp(self):
        self.loader = MigrationLoader(None, ignore_no_migrations=True)

    def test_project_migrations_have_no_raw_sql_or_sqlite_only_expressions(self):
        raw_sql_operations = []
        for (app_label, migration_name), migration in self.loader.disk_migrations.items():
            if app_label not in AUDITED_APPS:
                continue
            for operation in nested_operations(migration.operations):
                if isinstance(operation, migrations.RunSQL):
                    raw_sql_operations.append(f"{app_label}.{migration_name}")

        self.assertEqual(raw_sql_operations, [])

        project_root = Path(settings.BASE_DIR)
        for app_label in AUDITED_APPS:
            for migration_path in (project_root / app_label / "migrations").glob("*.py"):
                source = migration_path.read_text().lower()
                for expression in FORBIDDEN_SQLITE_EXPRESSIONS:
                    self.assertNotIn(
                        expression,
                        source,
                        f"{migration_path.name} contains SQLite-only {expression}",
                    )

    def test_graph_is_conflict_free_and_leaf_state_matches_current_models(self):
        self.assertEqual(self.loader.detect_conflicts(), {})

        migration_state = self.loader.project_state(
            self.loader.graph.leaf_nodes()
        )
        current_state = ProjectState.from_apps(apps)
        changes = MigrationAutodetector(
            migration_state,
            current_state,
        ).changes(graph=self.loader.graph)

        self.assertEqual(changes, {})
