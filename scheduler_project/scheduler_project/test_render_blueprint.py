import json
from pathlib import Path
import shutil
import subprocess

from django.test import SimpleTestCase


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BLUEPRINT_PATH = REPOSITORY_ROOT / 'render.yaml'


def load_blueprint():
    """Parse the Blueprint with Ruby's standard Psych YAML parser."""
    ruby = shutil.which('ruby')
    if ruby is None:
        raise RuntimeError('Ruby is required to parse render.yaml in this test.')
    result = subprocess.run(
        [
            ruby,
            '-rjson',
            '-ryaml',
            '-e',
            'print JSON.generate(YAML.safe_load(File.read(ARGV.fetch(0))))',
            str(BLUEPRINT_PATH),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class RenderBlueprintTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manifest_text = BLUEPRINT_PATH.read_text()
        cls.blueprint = load_blueprint()
        cls.services = cls.blueprint['services']
        cls.web = next(
            service for service in cls.services
            if service['type'] == 'web'
        )
        cls.cron = next(
            service for service in cls.services
            if service['type'] == 'cron'
        )

    @staticmethod
    def environment(service):
        return {
            item['key']: item
            for item in service['envVars']
            if 'key' in item
        }

    def test_manifest_has_exactly_the_approved_resources(self):
        self.assertTrue(BLUEPRINT_PATH.is_file())
        self.assertEqual(
            [service['type'] for service in self.services],
            ['web', 'cron'],
        )
        self.assertEqual(len(self.blueprint['databases']), 1)
        self.assertTrue(
            all(
                service['type'] not in {'worker', 'keyvalue', 'redis'}
                for service in self.services
            )
        )

    def test_web_runtime_commands_and_health_contract(self):
        self.assertEqual(self.web['runtime'], 'python')
        self.assertNotEqual(self.web['plan'], 'free')
        self.assertEqual(self.web['autoDeployTrigger'], 'off')
        self.assertEqual(self.web['healthCheckPath'], '/health/ready/')

        build = self.web['buildCommand']
        self.assertIn('pip install -r requirements.txt', build)
        self.assertIn(
            'scheduler_project/manage.py collectstatic --noinput',
            build,
        )
        self.assertNotIn('migrate', build)

        pre_deploy = self.web['preDeployCommand']
        migration = 'scheduler_project/manage.py migrate --noinput'
        release_check = (
            'scheduler_project/manage.py check_hosted_release'
        )
        self.assertLess(
            pre_deploy.index(migration),
            pre_deploy.index(release_check),
        )

        start = self.web['startCommand']
        self.assertIn('gunicorn', start)
        self.assertIn('--chdir scheduler_project', start)
        self.assertIn('scheduler_project.wsgi:application', start)
        self.assertIn('--bind 0.0.0.0:$PORT', start)
        self.assertIn('--workers 2', start)
        self.assertIn('--worker-class sync', start)
        self.assertNotIn('migrate', start)
        self.assertNotIn('run_demo_maintenance', start)

    def test_database_is_paid_private_and_region_aligned(self):
        database = self.blueprint['databases'][0]
        self.assertNotEqual(database['plan'], 'free')
        self.assertEqual(database['region'], self.web['region'])
        self.assertEqual(database['ipAllowList'], [])
        self.assertNotIn('sqlite', self.manifest_text.lower())

    def test_web_and_cron_bind_supported_discrete_database_values(self):
        for service in (self.web, self.cron):
            with self.subTest(service=service['name']):
                environment = self.environment(service)
                expected_properties = {
                    'POSTGRES_DB': 'database',
                    'POSTGRES_USER': 'user',
                    'POSTGRES_PASSWORD': 'password',
                }
                for key, property_name in expected_properties.items():
                    self.assertEqual(
                        environment[key]['fromDatabase'],
                        {
                            'name': 'pali-sched-beta-db',
                            'property': property_name,
                        },
                    )
                self.assertEqual(
                    environment['POSTGRES_HOST'],
                    {'key': 'POSTGRES_HOST', 'sync': False},
                )
                self.assertEqual(
                    environment['POSTGRES_PORT']['value'],
                    '5432',
                )

    def test_cron_contract_is_bounded(self):
        self.assertNotEqual(self.cron['plan'], 'free')
        self.assertEqual(self.cron['region'], self.web['region'])
        self.assertEqual(self.cron['schedule'], '*/15 * * * *')
        self.assertEqual(
            ' '.join(self.cron['startCommand'].split()),
            (
                'python scheduler_project/manage.py '
                'run_demo_maintenance --confirm'
            ),
        )
        for forbidden in (
            'migrate',
            'reset_demo_environment',
            'gunicorn',
            'clearsessions',
        ):
            self.assertNotIn(forbidden, self.cron['startCommand'])

    def test_security_capacity_and_maintenance_values_are_explicit(self):
        expected = {
            'DJANGO_SETTINGS_MODULE': 'scheduler_project.settings_hosted',
            'DJANGO_DATABASE_ENGINE': 'django.db.backends.postgresql',
            'POSTGRES_SSLMODE': 'require',
            'DJANGO_TRUST_PROXY_SSL_HEADER': 'true',
            'DEMO_ENTRY_ENABLED': 'false',
            'DEMO_SCAFFOLDING_ENABLED': 'true',
            'DEMO_MAX_ACTIVE_SESSIONS': '10',
            'DEMO_MAX_ACTIVE_PREPARED_SESSIONS': '4',
            'DEMO_MAX_ACTIVE_CLEAN_SESSIONS': '6',
            'DEMO_GLOBAL_START_LIMIT': '12',
            'DEMO_GLOBAL_START_WINDOW_SECONDS': '3600',
            'DEMO_CLIENT_START_LIMIT': '3',
            'DEMO_CLIENT_START_WINDOW_SECONDS': '900',
            'DEMO_MAX_CONCURRENT_PREPARED_OPERATIONS': '1',
            'DEMO_PREPARED_RESET_LIMIT': '6',
            'DEMO_PREPARED_RESET_WINDOW_SECONDS': '3600',
            'DEMO_CAPACITY_RESERVATION_SECONDS': '600',
            'DEMO_PREPARED_OPERATION_LEASE_SECONDS': '600',
            'DEMO_ATTEMPT_RETENTION_DAYS': '7',
            'DEMO_MAINTENANCE_LEASE_SECONDS': '900',
            'DEMO_MAINTENANCE_CLEANUP_LIMIT': '25',
            'DEMO_MAINTENANCE_ATTEMPT_LIMIT': '500',
            'DEMO_MAINTENANCE_AUXILIARY_LIMIT': '100',
        }
        for service in (self.web, self.cron):
            environment = self.environment(service)
            for key, value in expected.items():
                with self.subTest(service=service['name'], key=key):
                    self.assertEqual(environment[key]['value'], value)

    def test_operator_values_and_secrets_are_unsynced(self):
        unsynced = {
            'DJANGO_SECRET_KEY',
            'DJANGO_ALLOWED_HOSTS',
            'DJANGO_CSRF_TRUSTED_ORIGINS',
            'DEMO_ORGANIZATION_IDENTIFIER',
            'POSTGRES_HOST',
        }
        for service in (self.web, self.cron):
            environment = self.environment(service)
            for key in unsynced:
                with self.subTest(service=service['name'], key=key):
                    self.assertIs(environment[key]['sync'], False)
                    self.assertNotIn('value', environment[key])

        forbidden = (
            'DATABASE_URL',
            'cloudflare',
            'api_token',
            'staff_password',
            'DEBUG=True',
            'http://beta.',
        )
        active_manifest = '\n'.join(
            line for line in self.manifest_text.splitlines()
            if not line.lstrip().startswith('#')
        )
        for value in forbidden:
            self.assertNotIn(value, active_manifest)
        self.assertNotIn('value: "*"', active_manifest)

    def test_blueprint_contains_no_provider_side_access_automation(self):
        active_manifest = '\n'.join(
            line for line in self.manifest_text.splitlines()
            if not line.lstrip().startswith('#')
        ).lower()
        for forbidden in (
            'domains:',
            'rendersubdomainpolicy:',
            'certificate',
            'dns',
            'cloudflare',
            'accountid',
            'resourceid',
        ):
            self.assertNotIn(forbidden, active_manifest)
