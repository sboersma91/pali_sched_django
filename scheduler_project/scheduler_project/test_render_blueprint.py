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
            ruby, '-rjson', '-ryaml', '-e',
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
        cls.web = cls.services[0]
        cls.environment = {
            item['key']: item for item in cls.web['envVars']
        }

    def test_blueprint_declares_exactly_one_free_web_service(self):
        self.assertEqual(len(self.services), 1)
        self.assertEqual(self.web['type'], 'web')
        self.assertEqual(self.web['plan'], 'free')
        self.assertNotIn('databases', self.blueprint)
        self.assertNotIn('type: cron', self.manifest_text)
        for paid in ('starter', 'basic-256mb', 'standard'):
            self.assertNotIn(paid, self.manifest_text)

    def test_build_start_and_health_contract(self):
        self.assertEqual(self.web['runtime'], 'python')
        self.assertEqual(self.web['autoDeployTrigger'], 'off')
        self.assertEqual(self.web['healthCheckPath'], '/health/ready/')
        self.assertIn('pip install -r requirements.txt', self.web['buildCommand'])
        self.assertIn(
            'scheduler_project/manage.py collectstatic --noinput',
            self.web['buildCommand'],
        )
        self.assertNotIn('preDeployCommand', self.web)

        start = ' '.join(self.web['startCommand'].split())
        migrate = 'python scheduler_project/manage.py migrate --noinput'
        check = 'python scheduler_project/manage.py check_hosted_release'
        gunicorn = 'exec gunicorn'
        self.assertLess(start.index(migrate), start.index(check))
        self.assertLess(start.index(check), start.index(gunicorn))
        self.assertIn('&&', start)
        self.assertIn('--chdir scheduler_project', start)
        self.assertIn('scheduler_project.wsgi:application', start)
        self.assertIn('--bind 0.0.0.0:$PORT', start)
        self.assertNotIn('run_demo_maintenance', start)

    def test_database_url_is_manual_and_no_discrete_database_values_remain(self):
        self.assertEqual(
            self.environment['DATABASE_URL'],
            {'key': 'DATABASE_URL', 'sync': False},
        )
        for forbidden in (
            'DJANGO_DATABASE_ENGINE', 'POSTGRES_DB', 'POSTGRES_HOST',
            'POSTGRES_PORT', 'POSTGRES_USER', 'POSTGRES_PASSWORD',
            'POSTGRES_SSLMODE', 'fromDatabase',
        ):
            self.assertNotIn(forbidden, self.manifest_text)

    def test_security_demo_and_manual_secret_values_are_preserved(self):
        expected = {
            'DJANGO_SETTINGS_MODULE': 'scheduler_project.settings_hosted',
            'DJANGO_TRUST_PROXY_SSL_HEADER': 'true',
            'DEMO_ENTRY_ENABLED': 'false',
            'DEMO_SCAFFOLDING_ENABLED': 'true',
            'DEMO_MAX_ACTIVE_SESSIONS': '10',
            'DEMO_MAX_ACTIVE_PREPARED_SESSIONS': '4',
            'DEMO_MAX_ACTIVE_CLEAN_SESSIONS': '6',
        }
        for key, value in expected.items():
            self.assertEqual(self.environment[key]['value'], value)
        for key in (
            'DATABASE_URL', 'DJANGO_SECRET_KEY', 'DJANGO_ALLOWED_HOSTS',
            'DJANGO_CSRF_TRUSTED_ORIGINS', 'DEMO_ORGANIZATION_IDENTIFIER',
        ):
            self.assertIs(self.environment[key]['sync'], False)
            self.assertNotIn('value', self.environment[key])

    def test_blueprint_contains_no_credentials_or_access_automation(self):
        active = '\n'.join(
            line for line in self.manifest_text.splitlines()
            if not line.lstrip().startswith('#')
        ).lower()
        for forbidden in (
            'postgresql://', 'postgres://', 'password=', 'cloudflare',
            'api_token', 'domains:', 'rendersubdomainpolicy:', 'certificate',
        ):
            self.assertNotIn(forbidden, active)
