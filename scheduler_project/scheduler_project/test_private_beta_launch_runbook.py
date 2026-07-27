import re
from pathlib import Path

from django.core.management import get_commands
from django.test import SimpleTestCase
from django.urls import reverse

from scheduler_project.test_render_blueprint import load_blueprint


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK_PATH = REPOSITORY_ROOT / 'docs/private-beta-launch-runbook.md'
DEMO_RUNBOOK_PATH = REPOSITORY_ROOT / 'docs/demo-environment-runbook.md'


class PrivateBetaLaunchRunbookTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.text = RUNBOOK_PATH.read_text()
        cls.blueprint = load_blueprint()
        cls.web = next(
            service for service in cls.blueprint['services']
            if service['type'] == 'web'
        )

    def test_runbook_exists_is_linked_and_contains_every_phase(self):
        self.assertTrue(RUNBOOK_PATH.is_file())
        self.assertIn(
            '(private-beta-launch-runbook.md)',
            DEMO_RUNBOOK_PATH.read_text(),
        )
        for number in range(20):
            self.assertIsNotNone(
                re.search(
                    rf'^## Phase {number} \S',
                    self.text,
                    re.MULTILINE,
                ),
                msg=f'Phase {number} is missing.',
            )
        for control in (
            '**Objective:**',
            '**Expected result:**',
            '**Stop:**',
            '**Recovery:**',
            '**Mutation:**',
        ):
            self.assertGreaterEqual(self.text.count(control), 20)

    def test_every_referenced_management_command_exists(self):
        referenced = {
            'migrate',
            'check_hosted_release',
            'createsuperuser',
            'classify_canonical_demo',
            'inspect_demo_environment',
            'reset_demo_environment',
            'run_demo_maintenance',
        }
        commands = get_commands()
        self.assertTrue(referenced.issubset(commands))
        for command in referenced:
            self.assertIn(
                f'scheduler_project/manage.py {command}',
                self.text,
            )

    def test_every_referenced_application_route_exists(self):
        expected = {
            '/health/live/': reverse('health-live'),
            '/health/ready/': reverse('health-ready'),
            '/demo/': reverse('demo-landing'),
            '/demo/exit/': reverse('demo-exit'),
            '/login/': reverse('login'),
            '/logout/': reverse('logout'),
            '/admin/': reverse('admin:index'),
        }
        for documented, actual in expected.items():
            with self.subTest(route=documented):
                self.assertEqual(actual, documented)
                self.assertIn(documented, self.text)

    def test_manifest_commands_and_order_match_runbook(self):
        for command in (
            self.web['buildCommand'],
            self.web['startCommand'],
        ):
            normalized = ' '.join(command.split())
            normalized_runbook = ' '.join(
                self.text.replace('\\\n', ' ').split()
            )
            self.assertIn(normalized, normalized_runbook)

        start = self.web['startCommand']
        self.assertLess(
            start.index('migrate --noinput'),
            start.index('check_hosted_release'),
        )

    def test_free_web_manual_maintenance_and_entry_gate_are_documented(self):
        self.assertEqual(len(self.blueprint['services']), 1)
        environment = {
            item['key']: item for item in self.web['envVars']
            if 'key' in item
        }
        self.assertEqual(environment['DEMO_ENTRY_ENABLED']['value'], 'false')
        self.assertIn('Render Free web service', self.text)
        self.assertIn('External free PostgreSQL', self.text)
        self.assertIn('Automatic maintenance is not running', self.text)
        self.assertIn('cold-start', self.text)

        enablement = self.text.split(
            '## Phase 14',
            1,
        )[1].split('## Phase 15', 1)[0]
        prerequisites = (
            'Cloudflare',
            'Direct Render origin',
            'Readiness',
            'maintenance',
            'restore test',
            'Privacy notice',
            'Emergency operator',
        )
        first_true = enablement.index('DEMO_ENTRY_ENABLED=true')
        for prerequisite in prerequisites:
            self.assertLess(enablement.index(prerequisite), first_true)

    def test_runbook_forbids_origin_bypass_and_sqlite_transfer(self):
        phase_six = self.text.split(
            '## Phase 6',
            1,
        )[1].split('## Phase 7', 1)[0]
        self.assertIn('default deny', phase_six.lower())
        self.assertIn('before Django', phase_six)
        self.assertIn('Do not bypass `/health/*`', phase_six)

        phase_seven = self.text.split(
            '## Phase 7',
            1,
        )[1].split('## Phase 8', 1)[0]
        self.assertIn('disable', phase_seven.lower())
        self.assertIn('onrender.com', phase_seven)
        self.assertIn('cannot reach Django', phase_seven)

        self.assertIn(
            'no local SQLite export or development history was imported',
            self.text,
        )
        self.assertNotIn('dumpdata', self.text)
        self.assertNotIn('loaddata', self.text)

    def test_runbook_uses_placeholders_and_contains_no_credential_material(self):
        for placeholder in (
            '<beta-hostname>',
            '<canonical-organization-name>',
            '<external-database-url>',
            '<approved-tester-email>',
        ):
            self.assertIn(placeholder, self.text)

        forbidden_patterns = (
            r'https?://[A-Za-z0-9.-]+\.onrender\.com',
            r'postgres(?:ql)?://[^<\s]+',
            r'-----BEGIN [A-Z ]+PRIVATE KEY-----',
            r'(?i)\bapi[_ -]?token\s*[:=]\s*\S+',
            r'(?i)\bpassword\s*[:=]\s*\S+',
            r'\b[A-Fa-f0-9]{32}\b',
            r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}',
        )
        for pattern in forbidden_patterns:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, self.text))

    def test_privacy_and_restore_are_explicit_launch_blockers(self):
        self.assertIn(
            'This is a temporary demo workspace. Do not enter real names',
            self.text,
        )
        self.assertIn(
            'The required notice is implemented',
            self.text,
        )
        self.assertNotIn('privacy notice absent', self.text.lower())
        self.assertIn(
            'Restore that point into a **separate** PostgreSQL database',
            self.text,
        )
        final_gate = self.text.split('## Phase 19', 1)[1]
        self.assertIn('Required privacy notice is visible', final_gate)
        self.assertIn('Separate-database restore test passes', final_gate)
