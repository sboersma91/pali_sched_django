import uuid

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from scheduler_app.demo_session_cleanup import (
    DEFAULT_CLEANUP_LIMIT,
    DemoCleanupError,
    cleanup_expired_demo_sessions,
    plan_demo_session_cleanup,
)


class Command(BaseCommand):
    help = 'Dry-run or confirm bounded cleanup of expired temporary demo sessions.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Perform the planned deletions. Without this flag, no writes occur.',
        )
        parser.add_argument(
            '--before',
            help='Timezone-aware ISO-8601 expiration cutoff; defaults to server time.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=DEFAULT_CLEANUP_LIMIT,
            help='Maximum sessions to inspect in this bounded run.',
        )
        parser.add_argument(
            '--session-id',
            help='Target one exact opaque DemoSession UUID.',
        )

    def handle(self, *args, **options):
        cutoff = timezone.now()
        if options['before']:
            cutoff = parse_datetime(options['before'])
            if cutoff is None:
                raise CommandError('--before must be a valid ISO-8601 timestamp.')
            if timezone.is_naive(cutoff):
                raise CommandError('--before must include a timezone offset.')

        session_id = None
        if options['session_id']:
            try:
                session_id = uuid.UUID(options['session_id'])
            except ValueError as error:
                raise CommandError('--session-id must be a valid UUID.') from error

        try:
            plan = plan_demo_session_cleanup(
                cutoff=cutoff,
                limit=options['limit'],
                session_id=session_id,
            )
        except DemoCleanupError as error:
            raise CommandError(str(error)) from error

        self.stdout.write('Temporary demo cleanup plan')
        self.stdout.write(f'Cutoff: {plan.cutoff.isoformat()}')
        self.stdout.write(f'Limit: {plan.limit}')
        for item in plan.items:
            self.stdout.write(
                f'- {item.category.upper()} {item.session_id} '
                f'mode={item.mode} status={item.status} '
                f'expires={item.expires_at.isoformat()} '
                f'user={item.username} organization={item.organization_name} '
                f'reason="{item.reason}" counts={item.record_counts}'
            )
        self.stdout.write(f'More sessions remain: {"yes" if plan.more_remaining else "no"}')

        if not options['confirm']:
            self.stdout.write(self.style.WARNING(
                'DRY RUN: no records were changed. Re-run with --confirm to delete.'
            ))
            return

        result = cleanup_expired_demo_sessions(plan=plan)
        for category in ('deleted', 'skipped', 'blocked', 'failed'):
            outcomes = getattr(result, category)
            self.stdout.write(f'{category.upper()} ({len(outcomes)})')
            for outcome in outcomes:
                self.stdout.write(
                    f'- {outcome.session_id}: {outcome.reason or category}'
                )
        if result.failed:
            raise CommandError(
                'Cleanup completed with one or more isolated session failures.'
            )
