from django.core.management.base import BaseCommand, CommandError

from scheduler_app.demo_scaffolding import (
    DemoSafetyError,
    apply_demo_reference_data,
    inspect_demo_environment,
)


class Command(BaseCommand):
    help = 'Inspect the configured demo organization and print a read-only future-change plan.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--organization',
            required=True,
            help='Exact configured demo organization identifier.',
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Apply stable reference-data changes after printing the plan.',
        )
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Required explicit confirmation for --apply.',
        )

    def handle(self, *args, **options):
        if options['apply'] and not options['confirm']:
            raise CommandError('--apply requires --confirm; no changes were made.')
        if options['confirm'] and not options['apply']:
            raise CommandError('--confirm is only valid with --apply; no changes were made.')

        try:
            result = inspect_demo_environment(options['organization'])
        except DemoSafetyError as error:
            raise CommandError(str(error)) from error

        self.stdout.write('Demo environment inspection plan')
        self.stdout.write(f'Organization: {result.organization_identifier}')
        for category, items in result.categories():
            self.stdout.write(f'\n{category.upper()} ({len(items)})')
            for item in items:
                self.stdout.write(f'- {item.record_type}: {item.identity} — {item.reason}')

        if not options['apply']:
            self.stdout.write(self.style.SUCCESS('\nSafe target inspected; no changes were made.'))
            return

        try:
            applied = apply_demo_reference_data(
                options['organization'],
                inspection=result,
            )
        except DemoSafetyError as error:
            raise CommandError(str(error)) from error

        self.stdout.write('\nAPPLY RESULT')
        for category, items in applied.categories():
            self.stdout.write(f'\n{category.upper()} ({len(items)})')
            for item in items:
                self.stdout.write(f'- {item.record_type}: {item.identity} — {item.reason}')
        self.stdout.write(self.style.SUCCESS(
            '\nStable demo reference data applied atomically.'
        ))
