from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from scheduler_app.demo_maintenance import (
    DemoMaintenanceAlreadyRunning,
    plan_demo_maintenance,
    run_demo_maintenance,
)


class Command(BaseCommand):
    help = 'Plan or run one bounded temporary-demo maintenance cycle.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Perform bounded maintenance writes; default is a read-only dry run.',
        )
        parser.add_argument(
            '--before',
            help='Timezone-aware ISO-8601 ownership cleanup cutoff.',
        )
        parser.add_argument(
            '--cleanup-limit',
            type=int,
            default=settings.DEMO_MAINTENANCE_CLEANUP_LIMIT,
        )
        parser.add_argument(
            '--attempt-limit',
            type=int,
            default=settings.DEMO_MAINTENANCE_ATTEMPT_LIMIT,
        )
        parser.add_argument(
            '--auxiliary-limit',
            type=int,
            default=settings.DEMO_MAINTENANCE_AUXILIARY_LIMIT,
        )

    def handle(self, *args, **options):
        now = timezone.now()
        cutoff = now
        if options['before']:
            cutoff = parse_datetime(options['before'])
            if cutoff is None:
                raise CommandError(
                    '--before must be a valid ISO-8601 timestamp.',
                    returncode=2,
                )
            if timezone.is_naive(cutoff):
                raise CommandError(
                    '--before must include a timezone offset.',
                    returncode=2,
                )
            if cutoff > now:
                raise CommandError(
                    '--before cannot be in the future.',
                    returncode=2,
                )

        try:
            plan = plan_demo_maintenance(
                now=now,
                cleanup_cutoff=cutoff,
                cleanup_limit=options['cleanup_limit'],
                attempt_limit=options['attempt_limit'],
                auxiliary_limit=options['auxiliary_limit'],
            )
        except (ValueError, TypeError) as error:
            raise CommandError(str(error), returncode=2) from error

        self.stdout.write('Temporary demo maintenance')
        self.stdout.write(
            f'Mode: {"confirmed" if options["confirm"] else "dry run"}'
        )
        self.stdout.write(f'Cleanup cutoff: {plan.cleanup_cutoff.isoformat()}')
        self.stdout.write(f'Retention cutoff: {plan.retention_cutoff.isoformat()}')
        self.stdout.write(
            f'Limits: cleanup={plan.cleanup_limit} '
            f'attempt={plan.attempt_limit} auxiliary={plan.auxiliary_limit}'
        )
        self.stdout.write(
            'Maintenance lease active: '
            f'{"yes" if plan.active_maintenance_lease else "no"}'
        )
        self.stdout.write(
            f'Demo ownership: eligible={plan.cleanup_eligible} '
            f'selected={plan.cleanup_selected} backlog={plan.cleanup_backlog} '
            f'blocked={sum(item.category == "blocked" for item in plan.cleanup.items)}'
        )
        self._write_plan('Expired reservations', plan.reservations)
        self._write_plan('Expired operation leases', plan.operation_leases)
        self._write_plan('Old provisioning attempts', plan.provisioning_attempts)
        self._write_plan('Old reset attempts', plan.reset_attempts)
        self.stdout.write(
            f'Expired Django sessions: {plan.framework_sessions.eligible}'
        )

        if not options['confirm']:
            self.stdout.write(self.style.WARNING(
                'DRY RUN: no records were changed. Re-run with --confirm.'
            ))
            return

        try:
            result = run_demo_maintenance(plan=plan, confirmed=True)
        except DemoMaintenanceAlreadyRunning as error:
            raise CommandError(
                f'{error} Retry after the active lease expires.',
                returncode=2,
            ) from error

        self.stdout.write(f'Maintenance lease: {result.lease_status}')
        if result.cleanup is not None:
            self.stdout.write(
                'Demo ownership result: '
                f'deleted={len(result.cleanup.deleted)} '
                f'skipped={len(result.cleanup.skipped)} '
                f'blocked={len(result.cleanup.blocked)} '
                f'failed={len(result.cleanup.failed)}'
            )
        for label, category in (
            ('Expired reservations', result.reservations),
            ('Expired operation leases', result.operation_leases),
            ('Old provisioning attempts', result.provisioning_attempts),
            ('Old reset attempts', result.reset_attempts),
        ):
            if category is not None:
                self.stdout.write(
                    f'{label}: deleted={category.deleted} '
                    f'backlog={category.plan.backlog} '
                    f'error={category.error_category or "none"}'
                )
        if result.framework_sessions is not None:
            self.stdout.write(
                'Django sessions: '
                f'cleared={result.framework_sessions.deleted} '
                f'error={result.framework_sessions.error_category or "none"}'
            )
        self.stdout.write(
            f'Final status: {result.completion_category}; '
            f'errors={len(result.errors)}'
        )
        if result.errors:
            raise CommandError(
                'Demo maintenance completed with one or more category failures.'
            )

    def _write_plan(self, label, plan):
        self.stdout.write(
            f'{label}: eligible={plan.eligible} '
            f'selected={plan.selected} backlog={plan.backlog}'
        )
