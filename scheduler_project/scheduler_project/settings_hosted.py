"""Fail-closed settings for a deliberately selected private hosted deployment."""

from copy import deepcopy
import os

from django.core.exceptions import ImproperlyConfigured

from .settings import *  # noqa: F403
from .settings import SECRET_KEY as DEVELOPMENT_SECRET_KEY
from .settings_validation import (
    comma_list,
    environment_boolean,
    environment_integer,
    required_value,
    validate_allowed_hosts,
    validate_csrf_origins,
    validate_demo_capacity_settings,
    validate_demo_maintenance_settings,
    parse_postgresql_database_url,
    validate_secret,
)


DEBUG = False
SECRET_KEY = validate_secret(
    required_value(os.environ, 'DJANGO_SECRET_KEY'),
    DEVELOPMENT_SECRET_KEY,
)
ALLOWED_HOSTS = validate_allowed_hosts(
    comma_list(os.environ, 'DJANGO_ALLOWED_HOSTS')
)
CSRF_TRUSTED_ORIGINS = validate_csrf_origins(
    comma_list(os.environ, 'DJANGO_CSRF_TRUSTED_ORIGINS'),
    ALLOWED_HOSTS,
)

DATABASES = {
    'default': parse_postgresql_database_url(
        required_value(os.environ, 'DATABASE_URL')
    ),
}

SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
X_FRAME_OPTIONS = 'DENY'
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_HSTS_SECONDS = environment_integer(
    os.environ,
    'DJANGO_SECURE_HSTS_SECONDS',
    default=3600,
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
DATA_UPLOAD_MAX_MEMORY_SIZE = environment_integer(
    os.environ,
    'DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE',
    default=1024 * 1024,
)
STATIC_ROOT = BASE_DIR / 'staticfiles'  # noqa: F405
STATIC_URL = '/static/'
MIDDLEWARE = list(MIDDLEWARE)  # noqa: F405
MIDDLEWARE.insert(
    MIDDLEWARE.index('django.middleware.security.SecurityMiddleware') + 1,
    'whitenoise.middleware.WhiteNoiseMiddleware',
)
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': (
            'whitenoise.storage.CompressedManifestStaticFilesStorage'
        ),
    },
}

if environment_boolean(
    os.environ,
    'DJANGO_TRUST_PROXY_SSL_HEADER',
    default=False,
):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
else:
    SECURE_PROXY_SSL_HEADER = None

DEMO_ENTRY_ENABLED = environment_boolean(
    os.environ,
    'DEMO_ENTRY_ENABLED',
    default=False,
)

_demo_capacity_defaults = {
    'DEMO_MAX_ACTIVE_SESSIONS': 10,
    'DEMO_MAX_ACTIVE_PREPARED_SESSIONS': 4,
    'DEMO_MAX_ACTIVE_CLEAN_SESSIONS': 6,
    'DEMO_GLOBAL_START_LIMIT': 12,
    'DEMO_GLOBAL_START_WINDOW_SECONDS': 3600,
    'DEMO_CLIENT_START_LIMIT': 3,
    'DEMO_CLIENT_START_WINDOW_SECONDS': 900,
    'DEMO_MAX_CONCURRENT_PREPARED_OPERATIONS': 1,
    'DEMO_PREPARED_RESET_LIMIT': 6,
    'DEMO_PREPARED_RESET_WINDOW_SECONDS': 3600,
    'DEMO_CAPACITY_RESERVATION_SECONDS': 600,
    'DEMO_PREPARED_OPERATION_LEASE_SECONDS': 600,
}
_demo_capacity_values = validate_demo_capacity_settings({
    name: environment_integer(
        os.environ,
        name,
        default=default,
    )
    for name, default in _demo_capacity_defaults.items()
})
globals().update(_demo_capacity_values)

_demo_maintenance_defaults = {
    'DEMO_ATTEMPT_RETENTION_DAYS': 7,
    'DEMO_MAINTENANCE_LEASE_SECONDS': 900,
    'DEMO_MAINTENANCE_CLEANUP_LIMIT': 25,
    'DEMO_MAINTENANCE_ATTEMPT_LIMIT': 500,
    'DEMO_MAINTENANCE_AUXILIARY_LIMIT': 100,
}
_demo_maintenance_values = validate_demo_maintenance_settings(
    {
        name: environment_integer(
            os.environ,
            name,
            default=default,
        )
        for name, default in _demo_maintenance_defaults.items()
    },
    _demo_capacity_values,
)
globals().update(_demo_maintenance_values)

# Do not share mutable local-setting containers by reference.
INSTALLED_APPS = list(INSTALLED_APPS)  # noqa: F405
MIDDLEWARE = list(MIDDLEWARE)  # noqa: F405
TEMPLATES = deepcopy(TEMPLATES)  # noqa: F405
AUTH_PASSWORD_VALIDATORS = deepcopy(AUTH_PASSWORD_VALIDATORS)  # noqa: F405
