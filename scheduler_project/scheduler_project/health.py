"""Minimal public process liveness and application readiness checks."""

import logging

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.http import JsonResponse
from django.views.decorators.http import require_safe

logger = logging.getLogger(__name__)


@require_safe
def health_live(request):
    return JsonResponse({'status': 'ok'})


def application_is_ready():
    from members.models import DemoCapacityCoordinator

    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
        executor = MigrationExecutor(connection)
        if executor.migration_plan(executor.loader.graph.leaf_nodes()):
            return False, 'pending_migrations'
        DemoCapacityCoordinator.objects.filter(
            identifier='demo-capacity'
        ).exists()
    except Exception:
        return False, 'database_or_schema'
    return True, 'ready'


@require_safe
def health_ready(request):
    ready, category = application_is_ready()
    if not ready:
        logger.warning(
            'Application readiness failed category=%s',
            category,
        )
        return JsonResponse({'status': 'not_ready'}, status=503)
    return JsonResponse({'status': 'ready'})
