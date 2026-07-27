"""Central request-time enforcement for temporary demo authentication."""

from datetime import timedelta
import logging

from django.contrib.auth import logout
from django.shortcuts import redirect
from django.utils import timezone

from members.models import DemoSession

from .demo_session_access import (
    ACCESS_EXPIRED,
    ACCESS_INVALID,
    ACCESS_VALID,
    validate_temporary_demo_access,
)


logger = logging.getLogger(__name__)
DEMO_ACTIVITY_UPDATE_INTERVAL = timedelta(minutes=5)
EXEMPT_PATHS = {
    '/demo/',
    '/demo/start/clean/',
    '/demo/start/prepared/',
    '/demo/exit/',
    '/health/live/',
    '/health/ready/',
    '/login/',
    '/logout/',
}
EXEMPT_PREFIXES = ('/static/', '/media/')


class TemporaryDemoSessionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.path in EXEMPT_PATHS
            or request.path.startswith(EXEMPT_PREFIXES)
            or not request.user.is_authenticated
        ):
            return self.get_response(request)

        now = timezone.now()
        access = validate_temporary_demo_access(request.user, now=now)
        request.temporary_demo_access = access
        if access.category == ACCESS_EXPIRED:
            DemoSession.objects.filter(
                pk=access.demo_session.pk,
                status=DemoSession.Status.ACTIVE,
            ).update(status=DemoSession.Status.EXPIRING)
            logger.info(
                'Temporary demo session expired.',
                extra={
                    'demo_session_id': str(access.demo_session.identifier),
                    'temporary_user_id': request.user.pk,
                    'temporary_organization_id': access.organization.pk,
                    'failure_category': 'expired',
                },
            )
            logout(request)
            return redirect('/demo/?expired=1')

        if access.category == ACCESS_INVALID:
            logger.warning(
                'Temporary demo ownership validation failed.',
                extra={
                    'temporary_user_id': request.user.pk,
                    'failure_category': access.reason,
                },
            )
            logout(request)
            return redirect('/demo/?unavailable=1')

        if access.category == ACCESS_VALID:
            cutoff = now - DEMO_ACTIVITY_UPDATE_INTERVAL
            DemoSession.objects.filter(
                pk=access.demo_session.pk,
                status=DemoSession.Status.ACTIVE,
                expires_at__gt=now,
                last_activity_at__lte=cutoff,
            ).update(last_activity_at=now)

        return self.get_response(request)
