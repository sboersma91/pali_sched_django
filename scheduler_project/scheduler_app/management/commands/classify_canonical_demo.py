from django.core.management.base import BaseCommand, CommandError

from scheduler_app.canonical_classification import (
    CanonicalClassificationError,
    classify_canonical_demo_organization,
)


class Command(BaseCommand):
    help = 'Explicitly classify the configured demo organization as canonical_demo.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--organization',
            required=True,
            help='Exact configured demo organization identifier.',
        )
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Required explicit confirmation for canonical classification.',
        )

    def handle(self, *args, **options):
        if not options['confirm']:
            raise CommandError(
                '--confirm is required; no classification changes were made.'
            )

        def report_transition(current, proposed):
            self.stdout.write(f'Current purpose: {current}')
            self.stdout.write(f'Proposed purpose: {proposed}')

        try:
            result = classify_canonical_demo_organization(
                options['organization'],
                before_write=report_transition,
            )
        except CanonicalClassificationError as error:
            raise CommandError(str(error)) from error

        self.stdout.write(f'Stored purpose: {result.final_purpose}')
        if result.already_canonical:
            self.stdout.write(self.style.SUCCESS(
                'Organization was already classified as canonical_demo; '
                'no change was made.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                'Organization classified as canonical_demo.'
            ))
