from django.core.management.base import BaseCommand, CommandError

from scheduler_app.demo_scaffolding import (
    DemoSafetyError,
    inspect_demo_environment,
    reset_demo_environment,
)


class Command(BaseCommand):
    help = 'Restore the configured canonical demo environment to its validated starting state.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--organization',
            required=True,
            help='Exact configured demo organization identifier.',
        )
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Required explicit confirmation for canonical demo reset.',
        )

    def handle(self, *args, **options):
        if not options['confirm']:
            raise CommandError('--confirm is required; no reset changes were made.')

        try:
            plan = inspect_demo_environment(options['organization'])
        except DemoSafetyError as error:
            raise CommandError(str(error)) from error

        self.stdout.write('Demo environment reset plan')
        self.stdout.write(f'Organization: {plan.organization_identifier}')
        for category, items in plan.categories():
            self.stdout.write(f'\n{category.upper()} ({len(items)})')
            for item in items:
                self.stdout.write(
                    f'- {item.record_type}: {item.identity} — {item.reason}'
                )

        try:
            result = reset_demo_environment(
                options['organization'],
                inspection=plan,
            )
        except DemoSafetyError as error:
            raise CommandError(str(error)) from error

        self.stdout.write('\nRESET RESULT')
        for category, items in result.applied.categories():
            self.stdout.write(f'\n{category.upper()} ({len(items)})')
            for item in items:
                self.stdout.write(
                    f'- {item.record_type}: {item.identity} — {item.reason}'
                )
        if result.already_canonical:
            self.stdout.write(self.style.SUCCESS(
                '\nDemo environment already matches the canonical starting state.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                '\nDemo environment restored to the canonical starting state.'
            ))
