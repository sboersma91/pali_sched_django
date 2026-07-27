from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import checks
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import (
    Client,
    TestCase,
    override_settings,
)
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from members.models import (
    DemoCapacityCoordinator,
    DemoCapacityReservation,
    DemoOperationLease,
    DemoProvisioningAttempt,
    DemoSession,
    Organization,
)

from scheduler_app.demo_session_provisioning import (
    provision_clean_demo_session,
)


WHITENOISE_STORAGE = (
    'whitenoise.storage.CompressedManifestStaticFilesStorage'
)
HOSTED_MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
]


class RuntimeDependencyAndStaticTests(TestCase):
    def test_runtime_dependencies_are_pinned(self):
        requirements = (
            Path(settings.BASE_DIR).parent / 'requirements.txt'
        ).read_text()
        self.assertIn('gunicorn==26.0.0', requirements)
        self.assertIn('whitenoise==6.12.0', requirements)

    def test_local_settings_remain_without_production_static_middleware(self):
        self.assertNotIn(
            'whitenoise.middleware.WhiteNoiseMiddleware',
            settings.MIDDLEWARE,
        )
        self.assertEqual(settings.STATIC_URL, '/static/')

    def test_collectstatic_uses_temporary_root_and_collects_admin_assets(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with override_settings(
                STATIC_ROOT=root,
                STORAGES={
                    'default': {
                        'BACKEND': (
                            'django.core.files.storage.FileSystemStorage'
                        ),
                    },
                    'staticfiles': {'BACKEND': WHITENOISE_STORAGE},
                },
            ):
                call_command(
                    'collectstatic',
                    '--noinput',
                    verbosity=0,
                )

            self.assertTrue((root / 'admin/css/base.css').exists())
            self.assertTrue((root / 'staticfiles.json').exists())


class HealthEndpointTests(TestCase):
    def test_liveness_get_and_head_are_minimal_and_query_free(self):
        url = reverse('health-live')
        for method in ('get', 'head'):
            with self.subTest(method=method):
                with CaptureQueriesContext(connection) as queries:
                    response = getattr(self.client, method)(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(queries), 0)
                if method == 'get':
                    self.assertEqual(response.json(), {'status': 'ok'})
                    self.assertEqual(response.content, b'{"status": "ok"}')
        self.assertEqual(self.client.post(url).status_code, 405)

    def test_readiness_success_is_minimal_and_creates_no_records(self):
        counts = (
            get_user_model().objects.count(),
            Organization.objects.count(),
            DemoSession.objects.count(),
            DemoCapacityCoordinator.objects.count(),
            DemoCapacityReservation.objects.count(),
            DemoOperationLease.objects.count(),
            DemoProvisioningAttempt.objects.count(),
        )

        response = self.client.get(reverse('health-ready'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ready'})
        self.assertEqual(
            (
                get_user_model().objects.count(),
                Organization.objects.count(),
                DemoSession.objects.count(),
                DemoCapacityCoordinator.objects.count(),
                DemoCapacityReservation.objects.count(),
                DemoOperationLease.objects.count(),
                DemoProvisioningAttempt.objects.count(),
            ),
            counts,
        )
        self.assertEqual(
            self.client.head(reverse('health-ready')).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(reverse('health-ready')).status_code,
            405,
        )

    def test_readiness_failures_are_safe(self):
        secret = 'database-host.internal password=private-value'
        for category in ('pending_migrations', 'database_or_schema'):
            with self.subTest(category=category):
                with patch(
                    'scheduler_project.health.application_is_ready',
                    return_value=(False, category),
                ):
                    with self.assertLogs(
                        'scheduler_project.health',
                        level='WARNING',
                    ) as logs:
                        response = self.client.get(reverse('health-ready'))

                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {'status': 'not_ready'})
                self.assertNotIn(secret, response.content.decode())
                self.assertNotIn('password', ' '.join(logs.output))
                self.assertIn(category, ' '.join(logs.output))

    def test_pending_and_failed_migration_inspection_return_not_ready(self):
        executor = patch(
            'scheduler_project.health.MigrationExecutor'
        )
        with executor as executor_class:
            instance = executor_class.return_value
            instance.loader.graph.leaf_nodes.return_value = [('app', 'leaf')]
            instance.migration_plan.return_value = [('migration', False)]
            response = self.client.get(reverse('health-ready'))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'not_ready'})

        with patch(
            'scheduler_project.health.MigrationExecutor',
            side_effect=RuntimeError('private database detail'),
        ):
            response = self.client.get(reverse('health-ready'))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'not_ready'})
        self.assertNotContains(
            response,
            'private database detail',
            status_code=503,
        )

        with patch(
            'scheduler_project.health.connection.cursor',
            side_effect=RuntimeError('db.internal password=private'),
        ):
            response = self.client.get(reverse('health-ready'))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'not_ready'})
        self.assertNotContains(
            response,
            'db.internal',
            status_code=503,
        )

    @override_settings(DEMO_ENTRY_ENABLED=False)
    def test_health_is_isolated_from_expired_demo_middleware_and_capacity(self):
        result = provision_clean_demo_session()
        exit_time = timezone.now()
        DemoSession.objects.filter(pk=result.demo_session.pk).update(
            expires_at=exit_time,
        )
        result.demo_session.refresh_from_db()
        original_activity = result.demo_session.last_activity_at
        counts = (
            DemoCapacityReservation.objects.count(),
            DemoOperationLease.objects.count(),
            DemoProvisioningAttempt.objects.count(),
        )
        client = Client()
        client.force_login(result.user)

        live = client.get(reverse('health-live'))
        ready = client.get(reverse('health-ready'))

        self.assertEqual(live.status_code, 200)
        self.assertEqual(ready.status_code, 200)
        self.assertIn('_auth_user_id', client.session)
        result.demo_session.refresh_from_db()
        self.assertEqual(result.demo_session.status, DemoSession.Status.ACTIVE)
        self.assertEqual(result.demo_session.last_activity_at, original_activity)
        self.assertEqual(
            (
                DemoCapacityReservation.objects.count(),
                DemoOperationLease.objects.count(),
                DemoProvisioningAttempt.objects.count(),
            ),
            counts,
        )


@override_settings(
    SETTINGS_MODULE='scheduler_project.settings_hosted',
    DEBUG=False,
    DATABASES={
        'default': {'ENGINE': 'django.db.backends.postgresql'},
    },
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
    SECURE_SSL_REDIRECT=True,
    ALLOWED_HOSTS=['demo.example.com'],
    CSRF_TRUSTED_ORIGINS=['https://demo.example.com'],
    STATIC_ROOT=Path('/tmp/release-check-static'),
    STATIC_URL='/static/',
    MIDDLEWARE=HOSTED_MIDDLEWARE,
    STORAGES={
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {'BACKEND': WHITENOISE_STORAGE},
    },
    DEMO_ENTRY_ENABLED=False,
)
class HostedReleaseCheckTests(TestCase):
    def deployment_warnings(self):
        return [
            checks.Warning('accepted', id='security.W005'),
            checks.Warning('accepted', id='security.W021'),
        ]

    def test_valid_release_check_passes_and_reports_disabled_entry(self):
        output = StringIO()
        with (
            patch(
                'scheduler_app.management.commands.check_hosted_release.'
                'Command._database_ok',
                return_value=True,
            ),
            patch(
                'scheduler_app.management.commands.check_hosted_release.'
                'Command._migrations_ok',
                return_value=True,
            ),
            patch(
                'scheduler_app.management.commands.check_hosted_release.'
                'checks.run_checks',
                return_value=self.deployment_warnings(),
            ),
        ):
            call_command('check_hosted_release', stdout=output)

        text = output.getvalue()
        for category in (
            'Hosted settings: OK',
            'Database connectivity: OK',
            'Migration state: OK',
            'Static configuration: OK',
            'Runtime server dependency: OK',
            'Security checks: OK',
            'Demo entry: DISABLED',
            'Release readiness: PASS',
        ):
            self.assertIn(category, text)
        self.assertNotIn(settings.SECRET_KEY, text)

    def test_release_check_reports_enabled_entry_without_toggling_it(self):
        output = StringIO()
        with (
            patch.object(settings, 'DEMO_ENTRY_ENABLED', True),
            patch(
                'scheduler_app.management.commands.check_hosted_release.'
                'Command._database_ok',
                return_value=True,
            ),
            patch(
                'scheduler_app.management.commands.check_hosted_release.'
                'Command._migrations_ok',
                return_value=True,
            ),
            patch(
                'scheduler_app.management.commands.check_hosted_release.'
                'checks.run_checks',
                return_value=self.deployment_warnings(),
            ),
        ):
            call_command('check_hosted_release', stdout=output)
        self.assertIn('Demo entry: ENABLED', output.getvalue())
        self.assertFalse(settings.DEMO_ENTRY_ENABLED)

    def test_each_release_category_fails_closed(self):
        cases = (
            ('_database_ok', 'database'),
            ('_migrations_ok', 'migrations'),
            ('_static_configuration_ok', 'static'),
            ('_runtime_dependency_ok', 'runtime'),
            ('_demo_settings_ok', 'demo_configuration'),
        )
        for method, category in cases:
            with self.subTest(category=category):
                output = StringIO()
                patches = {
                    '_database_ok': True,
                    '_migrations_ok': True,
                    '_static_configuration_ok': True,
                    '_runtime_dependency_ok': True,
                    '_demo_settings_ok': True,
                }
                patches[method] = False
                with (
                    patch(
                        'scheduler_app.management.commands.'
                        f'check_hosted_release.Command._database_ok',
                        return_value=patches['_database_ok'],
                    ),
                    patch(
                        'scheduler_app.management.commands.'
                        f'check_hosted_release.Command._migrations_ok',
                        return_value=patches['_migrations_ok'],
                    ),
                    patch(
                        'scheduler_app.management.commands.'
                        f'check_hosted_release.Command.'
                        '_static_configuration_ok',
                        return_value=patches['_static_configuration_ok'],
                    ),
                    patch(
                        'scheduler_app.management.commands.'
                        f'check_hosted_release.Command._runtime_dependency_ok',
                        return_value=patches['_runtime_dependency_ok'],
                    ),
                    patch(
                        'scheduler_app.management.commands.'
                        f'check_hosted_release.Command._demo_settings_ok',
                        return_value=patches['_demo_settings_ok'],
                    ),
                    patch(
                        'scheduler_app.management.commands.'
                        'check_hosted_release.checks.run_checks',
                        return_value=self.deployment_warnings(),
                    ),
                ):
                    with self.assertRaises(CommandError):
                        call_command('check_hosted_release', stdout=output)
                self.assertIn('Release readiness: FAIL', output.getvalue())

    def test_unexpected_deployment_warning_fails(self):
        with (
            patch(
                'scheduler_app.management.commands.check_hosted_release.'
                'Command._database_ok',
                return_value=True,
            ),
            patch(
                'scheduler_app.management.commands.check_hosted_release.'
                'Command._migrations_ok',
                return_value=True,
            ),
            patch(
                'scheduler_app.management.commands.check_hosted_release.'
                'checks.run_checks',
                return_value=(
                    self.deployment_warnings()
                    + [checks.Warning('unexpected', id='security.W999')]
                ),
            ),
        ):
            with self.assertRaises(CommandError):
                call_command('check_hosted_release', stdout=StringIO())

    def test_local_settings_are_rejected(self):
        with (
            override_settings(
                SETTINGS_MODULE='scheduler_project.settings',
                DEBUG=True,
            ),
            patch(
                'scheduler_app.management.commands.check_hosted_release.'
                'Command._database_ok',
                return_value=True,
            ),
            patch(
                'scheduler_app.management.commands.check_hosted_release.'
                'Command._migrations_ok',
                return_value=True,
            ),
            patch(
                'scheduler_app.management.commands.check_hosted_release.'
                'checks.run_checks',
                return_value=self.deployment_warnings(),
            ),
        ):
            with self.assertRaises(CommandError):
                call_command('check_hosted_release', stdout=StringIO())
