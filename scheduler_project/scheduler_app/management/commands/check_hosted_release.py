import importlib.util
from pathlib import Path

from django.conf import settings
from django.core import checks
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from scheduler_project.settings_validation import (
    validate_demo_capacity_settings,
    validate_demo_maintenance_settings,
)


ACCEPTED_DEPLOYMENT_WARNINGS = {'security.W005', 'security.W021'}
WHITENOISE_MIDDLEWARE = 'whitenoise.middleware.WhiteNoiseMiddleware'
WHITENOISE_STORAGE = (
    'whitenoise.storage.CompressedManifestStaticFilesStorage'
)


class Command(BaseCommand):
    help = 'Run read-only checks for a private hosted release.'

    def handle(self, *args, **options):
        failures = []

        hosted_ok = (
            settings.SETTINGS_MODULE == 'scheduler_project.settings_hosted'
            and settings.DEBUG is False
            and settings.DATABASES['default']['ENGINE']
            == 'django.db.backends.postgresql'
            and settings.SESSION_COOKIE_SECURE
            and settings.CSRF_COOKIE_SECURE
            and settings.SECURE_SSL_REDIRECT
            and bool(settings.ALLOWED_HOSTS)
            and '*' not in settings.ALLOWED_HOSTS
            and bool(settings.CSRF_TRUSTED_ORIGINS)
        )
        self._report('Hosted settings', hosted_ok)
        if not hosted_ok:
            failures.append('configuration')

        static_ok = self._static_configuration_ok()
        self._report('Static configuration', static_ok)
        if not static_ok:
            failures.append('static')

        runtime_ok = self._runtime_dependency_ok()
        self._report('Runtime server dependency', runtime_ok)
        if not runtime_ok:
            failures.append('runtime')

        settings_ok = self._demo_settings_ok()
        self._report('Demo limit configuration', settings_ok)
        if not settings_ok:
            failures.append('demo_configuration')

        database_ok = self._database_ok()
        self._report('Database connectivity', database_ok)
        if not database_ok:
            failures.append('database')

        migrations_ok = database_ok and self._migrations_ok()
        self._report('Migration state', migrations_ok)
        if not migrations_ok:
            failures.append('migrations')

        security_ok = self._system_and_deployment_checks_ok()
        self._report('Security checks', security_ok)
        if not security_ok:
            failures.append('security')

        self.stdout.write(
            'Demo entry: '
            f'{"ENABLED" if settings.DEMO_ENTRY_ENABLED else "DISABLED"}'
        )

        if failures:
            self.stdout.write('Release readiness: FAIL')
            raise CommandError(
                'Hosted release checks failed: '
                + ', '.join(sorted(set(failures)))
            )
        self.stdout.write('Release readiness: PASS')

    def _report(self, category, passed):
        self.stdout.write(f'{category}: {"OK" if passed else "FAIL"}')

    def _static_configuration_ok(self):
        middleware = list(getattr(settings, 'MIDDLEWARE', ()))
        try:
            security_index = middleware.index(
                'django.middleware.security.SecurityMiddleware'
            )
            whitenoise_index = middleware.index(WHITENOISE_MIDDLEWARE)
        except ValueError:
            return False
        storages = getattr(settings, 'STORAGES', {})
        static_backend = storages.get('staticfiles', {}).get('BACKEND')
        return (
            whitenoise_index == security_index + 1
            and static_backend == WHITENOISE_STORAGE
            and bool(getattr(settings, 'STATIC_URL', ''))
            and isinstance(getattr(settings, 'STATIC_ROOT', None), Path)
            and importlib.util.find_spec('whitenoise') is not None
        )

    def _runtime_dependency_ok(self):
        requirements = settings.BASE_DIR.parent / 'requirements.txt'
        try:
            declared = 'gunicorn==' in requirements.read_text()
        except OSError:
            declared = False
        return (
            declared
            and importlib.util.find_spec('gunicorn') is not None
        )

    def _demo_settings_ok(self):
        capacity_names = (
            'DEMO_MAX_ACTIVE_SESSIONS',
            'DEMO_MAX_ACTIVE_PREPARED_SESSIONS',
            'DEMO_MAX_ACTIVE_CLEAN_SESSIONS',
            'DEMO_GLOBAL_START_LIMIT',
            'DEMO_GLOBAL_START_WINDOW_SECONDS',
            'DEMO_CLIENT_START_LIMIT',
            'DEMO_CLIENT_START_WINDOW_SECONDS',
            'DEMO_MAX_CONCURRENT_PREPARED_OPERATIONS',
            'DEMO_PREPARED_RESET_LIMIT',
            'DEMO_PREPARED_RESET_WINDOW_SECONDS',
            'DEMO_CAPACITY_RESERVATION_SECONDS',
            'DEMO_PREPARED_OPERATION_LEASE_SECONDS',
        )
        maintenance_names = (
            'DEMO_ATTEMPT_RETENTION_DAYS',
            'DEMO_MAINTENANCE_LEASE_SECONDS',
            'DEMO_MAINTENANCE_CLEANUP_LIMIT',
            'DEMO_MAINTENANCE_ATTEMPT_LIMIT',
            'DEMO_MAINTENANCE_AUXILIARY_LIMIT',
        )
        try:
            capacity = validate_demo_capacity_settings({
                name: getattr(settings, name) for name in capacity_names
            })
            validate_demo_maintenance_settings(
                {
                    name: getattr(settings, name)
                    for name in maintenance_names
                },
                capacity,
            )
        except Exception:
            return False
        return True

    def _database_ok(self):
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
                cursor.fetchone()
        except Exception:
            return False
        return True

    def _migrations_ok(self):
        try:
            executor = MigrationExecutor(connection)
            return not executor.migration_plan(
                executor.loader.graph.leaf_nodes()
            )
        except Exception:
            return False

    def _system_and_deployment_checks_ok(self):
        try:
            messages = checks.run_checks(include_deployment_checks=True)
        except Exception:
            return False
        unexpected = [
            message
            for message in messages
            if message.id not in ACCEPTED_DEPLOYMENT_WARNINGS
        ]
        present_accepted = {
            message.id
            for message in messages
            if message.id in ACCEPTED_DEPLOYMENT_WARNINGS
        }
        return not unexpected and present_accepted == ACCEPTED_DEPLOYMENT_WARNINGS
