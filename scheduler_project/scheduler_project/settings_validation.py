"""Small fail-closed parsers for the explicit hosted settings module."""

from ipaddress import IPv6Address
import re
from urllib.parse import parse_qs, unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured


TRUE_VALUES = {'1', 'true', 'yes', 'on'}
FALSE_VALUES = {'0', 'false', 'no', 'off'}
HOSTNAME_PATTERN = re.compile(
    r'^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*$'
)
IPV4_PATTERN = re.compile(r'^(?:\d{1,3}\.){3}\d{1,3}$')


def environment_boolean(environment, name, *, default=None):
    raw = environment.get(name)
    if raw is None:
        if default is None:
            raise ImproperlyConfigured(f'{name} must be set to true or false.')
        return default
    value = raw.strip().lower()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    raise ImproperlyConfigured(f'{name} must be set to true or false.')


def environment_integer(environment, name, *, default, minimum=1):
    raw = environment.get(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except (AttributeError, TypeError, ValueError) as error:
        raise ImproperlyConfigured(f'{name} must be an integer.') from error
    if value < minimum:
        raise ImproperlyConfigured(f'{name} must be at least {minimum}.')
    return value


def required_value(environment, name):
    value = environment.get(name, '').strip()
    if not value:
        raise ImproperlyConfigured(f'{name} is required for hosted settings.')
    return value


def comma_list(environment, name):
    raw = required_value(environment, name)
    values = [item.strip() for item in raw.split(',')]
    if any(not item for item in values):
        raise ImproperlyConfigured(f'{name} contains an empty list item.')
    return values


def validate_secret(secret, development_secret):
    if (
        secret == development_secret
        or secret.startswith('django-insecure')
        or len(secret) < 50
    ):
        raise ImproperlyConfigured(
            'DJANGO_SECRET_KEY must be a strong hosted-only value of at least '
            '50 characters.'
        )
    return secret


def _valid_ipv4(value):
    if not IPV4_PATTERN.fullmatch(value):
        return False
    return all(0 <= int(part) <= 255 for part in value.split('.'))


def validate_allowed_hosts(values):
    for value in values:
        if (
            value == '*'
            or '://' in value
            or '/' in value
            or '?' in value
            or '#' in value
            or '@' in value
            or any(character.isspace() for character in value)
        ):
            raise ImproperlyConfigured(
                'DJANGO_ALLOWED_HOSTS must contain exact hosts without schemes or paths.'
            )
        if value.startswith('[') and value.endswith(']'):
            try:
                IPv6Address(value[1:-1])
            except ValueError as error:
                raise ImproperlyConfigured('DJANGO_ALLOWED_HOSTS contains an invalid IPv6 host.')
            else:
                continue
        if ':' in value or not (
            HOSTNAME_PATTERN.fullmatch(value) or _valid_ipv4(value)
        ):
            raise ImproperlyConfigured('DJANGO_ALLOWED_HOSTS contains an invalid host.')
    return values


def validate_csrf_origins(values, allowed_hosts):
    allowed_names = {
        value[1:-1] if value.startswith('[') and value.endswith(']') else value
        for value in allowed_hosts
    }
    for value in values:
        parsed = urlsplit(value)
        if (
            parsed.scheme != 'https'
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or '*' in value
            or parsed.hostname not in allowed_names
        ):
            raise ImproperlyConfigured(
                'DJANGO_CSRF_TRUSTED_ORIGINS must contain exact HTTPS origins '
                'matching DJANGO_ALLOWED_HOSTS.'
            )
    return values


def parse_postgresql_database_url(value):
    """Return Django PostgreSQL settings without echoing URL credentials."""
    try:
        parsed = urlsplit(value)
        port = parsed.port or 5432
        query = parse_qs(parsed.query, keep_blank_values=True)
    except (AttributeError, TypeError, ValueError) as error:
        raise ImproperlyConfigured(
            'DATABASE_URL must be a valid PostgreSQL connection URL.'
        ) from error
    database_name = unquote(parsed.path[1:]) if parsed.path.startswith('/') else ''
    if (
        parsed.scheme not in {'postgres', 'postgresql'}
        or not parsed.hostname
        or parsed.username is None
        or parsed.password is None
        or not database_name
        or parsed.fragment
        or not 1 <= port <= 65535
    ):
        raise ImproperlyConfigured(
            'DATABASE_URL must be a complete PostgreSQL connection URL.'
        )
    sslmodes = query.get('sslmode', ())
    if len(sslmodes) > 1 or (sslmodes and sslmodes[0] != 'require'):
        raise ImproperlyConfigured(
            'DATABASE_URL must use PostgreSQL with SSL mode require.'
        )
    return {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': database_name,
        'HOST': parsed.hostname,
        'USER': unquote(parsed.username),
        'PASSWORD': unquote(parsed.password),
        'PORT': port,
        'CONN_MAX_AGE': 60,
        'OPTIONS': {'sslmode': 'require'},
    }


def validate_demo_capacity_settings(values):
    for name, value in values.items():
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ImproperlyConfigured(f'{name} must be a positive integer.')
    total = values['DEMO_MAX_ACTIVE_SESSIONS']
    if values['DEMO_MAX_ACTIVE_PREPARED_SESSIONS'] > total:
        raise ImproperlyConfigured(
            'DEMO_MAX_ACTIVE_PREPARED_SESSIONS cannot exceed '
            'DEMO_MAX_ACTIVE_SESSIONS.'
        )
    if values['DEMO_MAX_ACTIVE_CLEAN_SESSIONS'] > total:
        raise ImproperlyConfigured(
            'DEMO_MAX_ACTIVE_CLEAN_SESSIONS cannot exceed '
            'DEMO_MAX_ACTIVE_SESSIONS.'
        )
    return values


def validate_demo_maintenance_settings(values, capacity_values):
    maximums = {
        'DEMO_MAINTENANCE_CLEANUP_LIMIT': 100,
        'DEMO_MAINTENANCE_ATTEMPT_LIMIT': 5000,
        'DEMO_MAINTENANCE_AUXILIARY_LIMIT': 1000,
    }
    for name, value in values.items():
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ImproperlyConfigured(f'{name} must be a positive integer.')
        if name in maximums and value > maximums[name]:
            raise ImproperlyConfigured(
                f'{name} cannot exceed {maximums[name]}.'
            )
    longest_window = max(
        capacity_values['DEMO_GLOBAL_START_WINDOW_SECONDS'],
        capacity_values['DEMO_CLIENT_START_WINDOW_SECONDS'],
        capacity_values['DEMO_PREPARED_RESET_WINDOW_SECONDS'],
    )
    if values['DEMO_ATTEMPT_RETENTION_DAYS'] * 86400 <= longest_window:
        raise ImproperlyConfigured(
            'DEMO_ATTEMPT_RETENTION_DAYS must exceed every demo throttle window.'
        )
    if values['DEMO_MAINTENANCE_LEASE_SECONDS'] < 60:
        raise ImproperlyConfigured(
            'DEMO_MAINTENANCE_LEASE_SECONDS must be at least 60.'
        )
    return values
