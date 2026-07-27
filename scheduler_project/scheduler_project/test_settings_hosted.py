import json
import os
from pathlib import Path
import subprocess
import sys

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from . import settings as local_settings
from .settings_validation import (
    environment_boolean,
    validate_allowed_hosts,
    validate_csrf_origins,
    validate_secret,
)


HOSTED_VARIABLES = {
    'DJANGO_SECRET_KEY',
    'DJANGO_ALLOWED_HOSTS',
    'DJANGO_CSRF_TRUSTED_ORIGINS',
    'DJANGO_TRUST_PROXY_SSL_HEADER',
    'DJANGO_SECURE_HSTS_SECONDS',
    'DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE',
    'DJANGO_DATABASE_ENGINE',
    'DEMO_ENTRY_ENABLED',
    'POSTGRES_DB',
    'POSTGRES_HOST',
    'POSTGRES_USER',
    'POSTGRES_PASSWORD',
    'POSTGRES_PORT',
    'POSTGRES_SSLMODE',
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
    'DEMO_ATTEMPT_RETENTION_DAYS',
    'DEMO_MAINTENANCE_LEASE_SECONDS',
    'DEMO_MAINTENANCE_CLEANUP_LIMIT',
    'DEMO_MAINTENANCE_ATTEMPT_LIMIT',
    'DEMO_MAINTENANCE_AUXILIARY_LIMIT',
}
VALID_HOSTED_ENVIRONMENT = {
    'DJANGO_SECRET_KEY': 'hosted-secret-' + ('x' * 60),
    'DJANGO_ALLOWED_HOSTS': 'demo.example.com',
    'DJANGO_CSRF_TRUSTED_ORIGINS': 'https://demo.example.com',
    'POSTGRES_DB': 'flowline',
    'POSTGRES_HOST': 'db.internal',
    'POSTGRES_USER': 'flowline',
    'POSTGRES_PASSWORD': 'database-password-value',
}


def hosted_import(environment_updates=None, *, remove=()):
    environment = os.environ.copy()
    for name in HOSTED_VARIABLES:
        environment.pop(name, None)
    environment.update(VALID_HOSTED_ENVIRONMENT)
    environment.update(environment_updates or {})
    for name in remove:
        environment.pop(name, None)
    script = """
import json
from scheduler_project import settings as local
from scheduler_project import settings_hosted as hosted
print(json.dumps({
    'debug': hosted.DEBUG,
    'secret': hosted.SECRET_KEY,
    'hosts': hosted.ALLOWED_HOSTS,
    'origins': hosted.CSRF_TRUSTED_ORIGINS,
    'engine': hosted.DATABASES['default']['ENGINE'],
    'database': hosted.DATABASES['default']['NAME'],
    'secure_session': hosted.SESSION_COOKIE_SECURE,
    'httponly': hosted.SESSION_COOKIE_HTTPONLY,
    'samesite': hosted.SESSION_COOKIE_SAMESITE,
    'secure_csrf': hosted.CSRF_COOKIE_SECURE,
    'ssl_redirect': hosted.SECURE_SSL_REDIRECT,
    'hsts': hosted.SECURE_HSTS_SECONDS,
    'frame': hosted.X_FRAME_OPTIONS,
    'referrer': hosted.SECURE_REFERRER_POLICY,
    'nosniff': hosted.SECURE_CONTENT_TYPE_NOSNIFF,
    'request_size': hosted.DATA_UPLOAD_MAX_MEMORY_SIZE,
    'static_root': str(hosted.STATIC_ROOT),
    'proxy': hosted.SECURE_PROXY_SSL_HEADER,
    'entry': hosted.DEMO_ENTRY_ENABLED,
    'apps_shared': hosted.INSTALLED_APPS is local.INSTALLED_APPS,
    'middleware_shared': hosted.MIDDLEWARE is local.MIDDLEWARE,
    'templates_shared': hosted.TEMPLATES is local.TEMPLATES,
    'active_limit': hosted.DEMO_MAX_ACTIVE_SESSIONS,
    'prepared_limit': hosted.DEMO_MAX_ACTIVE_PREPARED_SESSIONS,
    'clean_limit': hosted.DEMO_MAX_ACTIVE_CLEAN_SESSIONS,
    'global_start_limit': hosted.DEMO_GLOBAL_START_LIMIT,
    'client_start_limit': hosted.DEMO_CLIENT_START_LIMIT,
    'prepared_operations': hosted.DEMO_MAX_CONCURRENT_PREPARED_OPERATIONS,
    'reset_limit': hosted.DEMO_PREPARED_RESET_LIMIT,
    'attempt_retention': hosted.DEMO_ATTEMPT_RETENTION_DAYS,
    'maintenance_lease': hosted.DEMO_MAINTENANCE_LEASE_SECONDS,
    'maintenance_cleanup': hosted.DEMO_MAINTENANCE_CLEANUP_LIMIT,
    'maintenance_attempt': hosted.DEMO_MAINTENANCE_ATTEMPT_LIMIT,
    'maintenance_auxiliary': hosted.DEMO_MAINTENANCE_AUXILIARY_LIMIT,
    'whitenoise_index': hosted.MIDDLEWARE.index(
        'whitenoise.middleware.WhiteNoiseMiddleware'
    ),
    'security_index': hosted.MIDDLEWARE.index(
        'django.middleware.security.SecurityMiddleware'
    ),
    'static_url': hosted.STATIC_URL,
    'static_backend': hosted.STORAGES['staticfiles']['BACKEND'],
}))
"""
    return subprocess.run(
        [sys.executable, '-c', script],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


class LocalSettingsTests(SimpleTestCase):
    def test_local_settings_preserve_development_defaults(self):
        self.assertTrue(local_settings.DEBUG)
        self.assertEqual(
            local_settings.DATABASES['default']['ENGINE'],
            'django.db.backends.sqlite3',
        )
        self.assertTrue(local_settings.DEMO_ENTRY_ENABLED)


class HostedSettingsImportTests(SimpleTestCase):
    def test_complete_environment_loads_secure_postgresql_settings(self):
        result = hosted_import()

        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads(result.stdout)
        self.assertFalse(values['debug'])
        self.assertEqual(values['engine'], 'django.db.backends.postgresql')
        self.assertEqual(values['database'], 'flowline')
        self.assertEqual(values['hosts'], ['demo.example.com'])
        self.assertEqual(values['origins'], ['https://demo.example.com'])
        self.assertTrue(values['secure_session'])
        self.assertTrue(values['httponly'])
        self.assertEqual(values['samesite'], 'Lax')
        self.assertTrue(values['secure_csrf'])
        self.assertTrue(values['ssl_redirect'])
        self.assertEqual(values['hsts'], 3600)
        self.assertEqual(values['frame'], 'DENY')
        self.assertEqual(values['referrer'], 'same-origin')
        self.assertTrue(values['nosniff'])
        self.assertEqual(values['request_size'], 1024 * 1024)
        self.assertTrue(values['static_root'].endswith('staticfiles'))
        self.assertIsNone(values['proxy'])
        self.assertFalse(values['entry'])
        self.assertFalse(values['apps_shared'])
        self.assertFalse(values['middleware_shared'])
        self.assertFalse(values['templates_shared'])
        self.assertEqual(values['active_limit'], 10)
        self.assertEqual(values['prepared_limit'], 4)
        self.assertEqual(values['clean_limit'], 6)
        self.assertEqual(values['global_start_limit'], 12)
        self.assertEqual(values['client_start_limit'], 3)
        self.assertEqual(values['prepared_operations'], 1)
        self.assertEqual(values['reset_limit'], 6)
        self.assertEqual(values['attempt_retention'], 7)
        self.assertEqual(values['maintenance_lease'], 900)
        self.assertEqual(values['maintenance_cleanup'], 25)
        self.assertEqual(values['maintenance_attempt'], 500)
        self.assertEqual(values['maintenance_auxiliary'], 100)
        self.assertEqual(
            values['whitenoise_index'],
            values['security_index'] + 1,
        )
        self.assertEqual(values['static_url'], '/static/')
        self.assertEqual(
            values['static_backend'],
            'whitenoise.storage.CompressedManifestStaticFilesStorage',
        )

    def test_capacity_overrides_load_and_invalid_values_fail(self):
        overridden = hosted_import({
            'DEMO_MAX_ACTIVE_SESSIONS': '20',
            'DEMO_MAX_ACTIVE_PREPARED_SESSIONS': '8',
            'DEMO_MAX_ACTIVE_CLEAN_SESSIONS': '12',
        })
        self.assertEqual(overridden.returncode, 0, overridden.stderr)
        values = json.loads(overridden.stdout)
        self.assertEqual(values['active_limit'], 20)
        self.assertEqual(values['prepared_limit'], 8)
        self.assertEqual(values['clean_limit'], 12)

        for name, value in (
            ('DEMO_GLOBAL_START_LIMIT', '0'),
            ('DEMO_CLIENT_START_LIMIT', '-1'),
            ('DEMO_MAX_ACTIVE_SESSIONS', 'many'),
            ('DEMO_MAX_ACTIVE_PREPARED_SESSIONS', '11'),
        ):
            with self.subTest(name=name):
                result = hosted_import({name: value})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(name, result.stderr)

    def test_proxy_and_entry_require_narrow_boolean_values(self):
        enabled = hosted_import({
            'DJANGO_TRUST_PROXY_SSL_HEADER': 'true',
            'DEMO_ENTRY_ENABLED': 'yes',
        })
        self.assertEqual(enabled.returncode, 0, enabled.stderr)
        values = json.loads(enabled.stdout)
        self.assertEqual(
            values['proxy'],
            ['HTTP_X_FORWARDED_PROTO', 'https'],
        )
        self.assertTrue(values['entry'])

        for name in ('DJANGO_TRUST_PROXY_SSL_HEADER', 'DEMO_ENTRY_ENABLED'):
            with self.subTest(name=name):
                invalid = hosted_import({name: 'sometimes'})
                self.assertNotEqual(invalid.returncode, 0)
                self.assertIn(name, invalid.stderr)

    def test_maintenance_overrides_and_relationships_are_validated(self):
        overridden = hosted_import({
            'DEMO_ATTEMPT_RETENTION_DAYS': '14',
            'DEMO_MAINTENANCE_LEASE_SECONDS': '1200',
            'DEMO_MAINTENANCE_CLEANUP_LIMIT': '50',
            'DEMO_MAINTENANCE_ATTEMPT_LIMIT': '1000',
            'DEMO_MAINTENANCE_AUXILIARY_LIMIT': '200',
        })
        self.assertEqual(overridden.returncode, 0, overridden.stderr)
        values = json.loads(overridden.stdout)
        self.assertEqual(values['attempt_retention'], 14)
        self.assertEqual(values['maintenance_lease'], 1200)
        self.assertEqual(values['maintenance_cleanup'], 50)
        self.assertEqual(values['maintenance_attempt'], 1000)
        self.assertEqual(values['maintenance_auxiliary'], 200)

        for name, value in (
            ('DEMO_ATTEMPT_RETENTION_DAYS', '0'),
            ('DEMO_ATTEMPT_RETENTION_DAYS', 'many'),
            ('DEMO_MAINTENANCE_LEASE_SECONDS', '59'),
            ('DEMO_MAINTENANCE_CLEANUP_LIMIT', '101'),
            ('DEMO_MAINTENANCE_ATTEMPT_LIMIT', '5001'),
            ('DEMO_MAINTENANCE_AUXILIARY_LIMIT', '1001'),
        ):
            with self.subTest(name=name):
                invalid = hosted_import({name: value})
                self.assertNotEqual(invalid.returncode, 0)
                self.assertIn(name, invalid.stderr)

        short_retention = hosted_import({
            'DEMO_ATTEMPT_RETENTION_DAYS': '1',
            'DEMO_GLOBAL_START_WINDOW_SECONDS': '86400',
        })
        self.assertNotEqual(short_retention.returncode, 0)
        self.assertIn('DEMO_ATTEMPT_RETENTION_DAYS', short_retention.stderr)

    def test_missing_database_fields_fail_without_leaking_password(self):
        secret_password = VALID_HOSTED_ENVIRONMENT['POSTGRES_PASSWORD']
        for name in (
            'POSTGRES_DB',
            'POSTGRES_HOST',
            'POSTGRES_USER',
            'POSTGRES_PASSWORD',
        ):
            with self.subTest(name=name):
                result = hosted_import(remove=(name,))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(name, result.stderr)
                self.assertNotIn(secret_password, result.stderr)

    def test_sqlite_and_unsupported_database_engines_fail(self):
        for engine in ('django.db.backends.sqlite3', 'mysql'):
            with self.subTest(engine=engine):
                result = hosted_import({'DJANGO_DATABASE_ENGINE': engine})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('DJANGO_DATABASE_ENGINE', result.stderr)

    def test_invalid_request_size_and_hsts_fail(self):
        for name, value in (
            ('DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE', '0'),
            ('DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE', 'large'),
            ('DJANGO_SECURE_HSTS_SECONDS', '0'),
        ):
            with self.subTest(name=name, value=value):
                result = hosted_import({name: value})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(name, result.stderr)


class HostedValidationHelperTests(SimpleTestCase):
    def test_secret_validation_rejects_missing_development_prefixed_and_short(self):
        development = local_settings.SECRET_KEY
        for value in (
            '',
            development,
            'django-insecure-' + ('x' * 60),
            'too-short',
        ):
            with self.subTest(value_length=len(value)):
                with self.assertRaises(ImproperlyConfigured) as raised:
                    validate_secret(value, development)
                if value:
                    self.assertNotIn(value, str(raised.exception))
        self.assertEqual(
            validate_secret('hosted-' + ('z' * 60), development),
            'hosted-' + ('z' * 60),
        )

        for remove in ('DJANGO_SECRET_KEY',):
            result = hosted_import(remove=(remove,))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('DJANGO_SECRET_KEY', result.stderr)

    def test_exact_hosts_and_multiple_hosts(self):
        self.assertEqual(
            validate_allowed_hosts(['demo.example.com', '203.0.113.10']),
            ['demo.example.com', '203.0.113.10'],
        )
        for value in (
            '*',
            'https://demo.example.com',
            'demo.example.com/path',
            'bad host',
            'demo.example.com:443',
        ):
            with self.subTest(value=value):
                with self.assertRaises(ImproperlyConfigured):
                    validate_allowed_hosts([value])

        multiple = hosted_import({
            'DJANGO_ALLOWED_HOSTS': 'demo.example.com,admin.example.com',
            'DJANGO_CSRF_TRUSTED_ORIGINS': (
                'https://demo.example.com,https://admin.example.com'
            ),
        })
        self.assertEqual(multiple.returncode, 0, multiple.stderr)
        self.assertEqual(
            json.loads(multiple.stdout)['hosts'],
            ['demo.example.com', 'admin.example.com'],
        )

    def test_csrf_origins_are_exact_https_and_match_allowed_hosts(self):
        allowed = ['demo.example.com']
        self.assertEqual(
            validate_csrf_origins(['https://demo.example.com'], allowed),
            ['https://demo.example.com'],
        )
        for value in (
            'http://demo.example.com',
            'https://*.example.com',
            'https://demo.example.com/path',
            'https://user@demo.example.com',
            'https://foreign.example.com',
        ):
            with self.subTest(value=value):
                with self.assertRaises(ImproperlyConfigured):
                    validate_csrf_origins([value], allowed)

    def test_boolean_parser_accepts_only_documented_values(self):
        for value in ('1', 'true', 'yes', 'on'):
            self.assertTrue(environment_boolean({'FLAG': value}, 'FLAG'))
        for value in ('0', 'false', 'no', 'off'):
            self.assertFalse(environment_boolean({'FLAG': value}, 'FLAG'))
        with self.assertRaises(ImproperlyConfigured):
            environment_boolean({'FLAG': ''}, 'FLAG')
