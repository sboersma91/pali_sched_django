"""Development-only validation of working-v1 against its extraction source."""

from django.core.management.base import BaseCommand, CommandError

from members.models import Organization
from scheduler_app.default_dataset_copy import (
    _counts, _organization_graph, _relationship_counts,
)
from scheduler_app.working_demo_scenario import EXPECTED_COUNTS


class Command(BaseCommand):
    help = (
        'Validate repository-owned working-v1 reference counts against '
        'Realistic Demo Source. Hosted provisioning never calls this command.'
    )

    def handle(self, *args, **options):
        try:
            source = Organization.objects.get(name='Realistic Demo Source')
        except Organization.DoesNotExist as error:
            raise CommandError(
                'Realistic Demo Source is required only for development validation.'
            ) from error
        graph = _organization_graph(source, normalize_schedule_state=True)
        counts = _counts(graph)
        actual = {
            'locations': counts['locations'],
            'activities': counts['activities'],
            'schools': counts['schools'],
            'schedules': counts['schedules'],
            'instructors': counts['instructors'],
            'participation': counts['participation'],
            'availability': counts['availability'],
        }
        if actual != EXPECTED_COUNTS:
            raise CommandError(
                f'Source counts differ from committed working-v1: {actual!r}'
            )
        relationships = sum(_relationship_counts(graph).values())
        if relationships != 153:
            raise CommandError(
                f'Expected 153 reconstructed relationships, found {relationships}.'
            )
        self.stdout.write(self.style.SUCCESS(
            'working-v1 source validation passed: committed counts and 153 '
            'logical-key relationships match.'
        ))
