from django.core.management.base import BaseCommand, CommandError

from scheduler_app.default_dataset_copy import (
    DefaultDatasetCopyError,
    DefaultDatasetCopyPlan,
    copy_default_dataset_to_organization,
)


class Command(BaseCommand):
    help = (
        'Dry-run or confirm a guarded deep copy of Default Organization '
        'scheduling data into one named permanent organization.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--target-organization',
            required=True,
            help='Exact permanent target organization name.',
        )
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Create and populate the target atomically.',
        )

    def _print_plan(self, plan):
        self.stdout.write('Default dataset copy plan')
        self.stdout.write(f'Source organization: {plan.source_name}')
        self.stdout.write(f'Target organization: {plan.target_name}')
        self.stdout.write(
            'Target action: '
            + (
                'create permanent customer organization'
                if plan.target_will_be_created
                else 'use existing empty permanent organization'
            )
        )
        self.stdout.write('\nINCLUDED RECORDS')
        for category, count in plan.counts.items():
            self.stdout.write(f'- {category}: {count}')
        self.stdout.write('\nRELATIONSHIPS')
        for category, count in plan.relationships.items():
            self.stdout.write(f'- {category}: {count}')
        self.stdout.write('\nSCHEDULE STATE POLICY')
        for state in plan.schedule_states:
            self.stdout.write(
                f'- {state["schedule"]}: {state["source_state"]}; '
                f'{state["target_state"]}'
            )
        self.stdout.write('\nEXCLUDED')
        for category in plan.excluded_categories:
            self.stdout.write(f'- {category}')
        self.stdout.write('\nWARNINGS')
        if plan.warnings:
            for warning in plan.warnings:
                self.stdout.write(f'- {warning}')
        else:
            self.stdout.write('- none')
        self.stdout.write('\nBLOCKERS')
        if plan.blockers:
            for blocker in plan.blockers:
                self.stdout.write(f'- {blocker}')
        else:
            self.stdout.write('- none')
        self.stdout.write(
            f'\nExpected mutation count: {plan.expected_mutations}'
        )

    def handle(self, *args, **options):
        try:
            result = copy_default_dataset_to_organization(
                options['target_organization'],
                confirmed=options['confirm'],
            )
        except DefaultDatasetCopyError as error:
            if error.plan:
                self._print_plan(error.plan)
            raise CommandError(str(error)) from error

        plan = result if isinstance(result, DefaultDatasetCopyPlan) else result.plan
        self._print_plan(plan)
        if isinstance(result, DefaultDatasetCopyPlan):
            self.stdout.write(self.style.SUCCESS(
                '\nDry run complete; no database changes were made. '
                'Repeat with --confirm only after operator approval.'
            ))
            return

        if result.copied:
            self.stdout.write(self.style.SUCCESS(
                '\nDefault dataset copied atomically.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                '\nTarget already matches the complete normalized copy; '
                'no changes were made.'
            ))
        self.stdout.write(f'Target organization: {result.target.name}')
        self.stdout.write(f'Target organization ID: {result.target.pk}')
        self.stdout.write(
            'Target membership: '
            + ('present' if result.target_has_membership else 'none')
        )
        if not result.target_has_membership:
            self.stdout.write(
                'Add an operator membership manually through the approved '
                'admin boundary if normal UI inspection is required.'
            )
