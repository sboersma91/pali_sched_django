from copy import deepcopy

from django.test import TestCase

from members.models import Organization

from .instructor_assignment import (
    _run_instructor_assignment_core,
    run_instructor_assignment,
)
from .instructor_overrides import (
    build_occurrence_identity,
    load_and_apply_instructor_override,
    persist_manual_instructor_override,
    reset_all_manual_instructor_overrides,
    reset_manual_instructor_override,
)
from .models import (
    ActivityCertificationRequirement,
    Certification,
    Course,
    Instructor,
    InstructorCertification,
    InstructorScheduleAvailability,
    TheSched,
)
from .schedule_operations import normalize_sched_data_structure


class InstructorOverridePersistenceTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Override Org')
        self.other_organization = Organization.objects.create(name='Other Org')
        self.course = Course.objects.create(
            organization=self.organization,
            course_name='Archery',
            abriviation='ARCH',
            course_len=1,
        )
        self.schedule = self.create_schedule(
            'Override Week',
            {
                'ags': ['Group 0'],
                'mon_pm1': [self.course.course_name],
                'mon_pm2': ['empty'],
            },
        )
        self.first = self.create_instructor('Avery', 'Alpha')
        self.second = self.create_instructor('Blake', 'Beta')

    def create_instructor(self, first, last, organization=None):
        return Instructor.objects.create(
            organization=organization or self.organization,
            fname=first,
            lname=last,
        )

    def create_schedule(self, name, generated_schedule, organization=None):
        return TheSched.objects.create(
            organization=organization or self.organization,
            sched_name=name,
            sched_data={
                'version': 1,
                'generated_schedule': generated_schedule,
                'manual_moves': [],
                'generation_diagnostics': [],
                'generation_runtime_diagnostics': [],
                'generation_complete': True,
            },
        )

    def current_occurrence(self, schedule=None, index=0):
        schedule = schedule or self.schedule
        return _run_instructor_assignment_core(schedule)['occurrences'][index]

    def identity(self, schedule=None, index=0):
        schedule = schedule or self.schedule
        return build_occurrence_identity(
            schedule,
            self.current_occurrence(schedule, index),
        )

    def persist(
        self,
        *,
        schedule=None,
        identity=None,
        instructor=None,
        revision=0,
        confirm=False,
    ):
        schedule = schedule or self.schedule
        instructor = instructor or self.second
        return persist_manual_instructor_override(
            schedule=schedule,
            occurrence_identity=identity or self.identity(schedule),
            instructor_id=instructor.pk,
            expected_revision=revision,
            confirm_coverage_reduction=confirm,
        )

    def test_normalization_adds_defaults_and_preserves_all_existing_data(self):
        source = {
            'version': 7,
            'generated_schedule': {'ags': ['Keep']},
            'manual_moves': [{'keep': True}],
            'unrelated': {'also': 'keep'},
        }

        normalized = normalize_sched_data_structure(source)

        self.assertEqual(normalized['version'], 7)
        self.assertEqual(normalized['generated_schedule'], {'ags': ['Keep']})
        self.assertEqual(normalized['manual_moves'], [{'keep': True}])
        self.assertEqual(normalized['unrelated'], {'also': 'keep'})
        self.assertEqual(normalized['manual_instructor_overrides'], [])
        self.assertEqual(normalized['instructor_override_revision'], 0)
        self.assertNotIn('manual_instructor_overrides', source)

    def test_first_persistence_preserves_generation_and_activity_moves(self):
        manual_move = {'existing': 'activity move'}
        self.schedule.sched_data['manual_moves'] = [manual_move]
        self.schedule.save(update_fields=['sched_data'])
        generated_before = deepcopy(
            self.schedule.sched_data['generated_schedule']
        )

        result = self.persist()

        self.assertTrue(result['ok'])
        self.schedule.refresh_from_db()
        self.assertEqual(
            self.schedule.sched_data['generated_schedule'],
            generated_before,
        )
        self.assertEqual(
            self.schedule.sched_data['manual_moves'],
            [manual_move],
        )
        records = self.schedule.sched_data['manual_instructor_overrides']
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['status'], 'active')
        self.assertEqual(records[0]['action'], 'set')
        self.assertEqual(self.schedule.sched_data['instructor_override_revision'], 1)

    def test_persisted_identity_is_complete_json_safe_and_deterministic(self):
        result = self.persist()
        occurrence = result['override']['occurrence']

        self.assertEqual(occurrence['schedule_id'], self.schedule.pk)
        self.assertEqual(
            occurrence['organization_id'],
            self.organization.pk,
        )
        self.assertEqual(occurrence['activity_id'], self.course.pk)
        self.assertEqual(occurrence['group_index'], 0)
        self.assertEqual(
            occurrence['slot_footprint'],
            [{
                'block_id': '0:mon_pm1',
                'slot_key': 'mon_pm1',
                'position': 1,
            }],
        )
        self.assertIsInstance(result['override']['override_id'], str)
        self.assertTrue(result['override']['created_at'].endswith('Z'))

    def test_revision_conflict_rejects_without_writing(self):
        before = deepcopy(self.schedule.sched_data)

        result = self.persist(revision=4)

        self.assertEqual(result['code'], 'revision_conflict')
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data, before)

    def test_validation_uses_locked_current_schedule_state(self):
        stale_identity = self.identity()
        current_data = deepcopy(self.schedule.sched_data)
        current_data['generated_schedule']['mon_pm1'] = ['empty']
        TheSched.objects.filter(pk=self.schedule.pk).update(
            sched_data=current_data
        )

        result = self.persist(identity=stale_identity)

        self.assertEqual(result['code'], 'missing_occurrence')
        self.schedule.refresh_from_db()
        self.assertNotIn(
            'manual_instructor_overrides',
            self.schedule.sched_data,
        )

    def test_cross_organization_schedule_and_instructor_are_rejected(self):
        detached = TheSched.objects.get(pk=self.schedule.pk)
        detached.organization_id = self.other_organization.pk
        schedule_result = self.persist(
            schedule=detached,
            identity=self.identity(),
        )
        self.assertEqual(schedule_result['code'], 'organization_mismatch')

        foreign = self.create_instructor(
            'Foreign',
            'Instructor',
            organization=self.other_organization,
        )
        instructor_result = self.persist(instructor=foreign)
        self.assertEqual(instructor_result['code'], 'organization_mismatch')

    def test_missing_instructor_and_occurrence_are_rejected(self):
        missing_instructor = persist_manual_instructor_override(
            schedule=self.schedule,
            occurrence_identity=self.identity(),
            instructor_id=999999,
            expected_revision=0,
        )
        self.assertEqual(missing_instructor['code'], 'missing_instructor')

        identity = self.identity()
        identity.update({
            'occurrence_id': 'missing',
            'activity_id': 999999,
        })
        missing_occurrence = self.persist(identity=identity)
        self.assertEqual(missing_occurrence['code'], 'missing_occurrence')

    def test_each_composite_identity_change_is_diagnosed_as_stale(self):
        mutations = (
            ('activity_id', 999),
            ('group_index', 9),
            ('occurrence_id', 'changed'),
            ('slot_footprint', [{
                'block_id': '0:mon_pm2',
                'slot_key': 'mon_pm2',
                'position': 1,
            }]),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                identity = self.identity()
                identity[field] = value
                if field == 'activity_id':
                    # Preserve occurrence lookup through its current ID.
                    pass
                result = self.persist(identity=identity)
                self.assertEqual(
                    result['code'],
                    'stale_occurrence_identity',
                )

    def test_valid_persisted_override_is_applied_read_only(self):
        self.persist()
        stored = deepcopy(self.schedule.sched_data)

        with self.assertNumQueries(15):
            result = run_instructor_assignment(self.schedule)

        self.assertEqual(
            result['assignments'][0]['assigned_instructor'],
            self.second,
        )
        self.assertEqual(
            result['instructor_override_diagnostics'][0]['code'],
            'active_and_applied',
        )
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data, stored)

    def test_stale_override_is_skipped_preserved_and_automatic_plan_continues(self):
        self.persist()
        stored = deepcopy(self.schedule.sched_data)
        stored['manual_instructor_overrides'][0]['occurrence']['activity_id'] = 999
        self.schedule.sched_data = stored
        self.schedule.save(update_fields=['sched_data'])

        result = run_instructor_assignment(self.schedule)

        self.assertEqual(
            result['instructor_override_diagnostics'][0]['code'],
            'stale_occurrence_identity',
        )
        self.assertEqual(
            result['assignments'][0]['assigned_instructor'],
            self.first,
        )
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data, stored)

    def test_stale_override_is_skipped_while_other_valid_override_applies(self):
        other = Course.objects.create(
            organization=self.organization,
            course_name='Climbing',
            abriviation='CLMB',
            course_len=1,
        )
        data = deepcopy(self.schedule.sched_data)
        data['generated_schedule']['mon_pm2'] = [other.course_name]
        self.schedule.sched_data = data
        self.schedule.save(update_fields=['sched_data'])
        first_identity = self.identity(index=0)
        second_identity = self.identity(index=1)
        self.persist(identity=first_identity)
        self.persist(identity=second_identity, revision=1)
        stored = deepcopy(self.schedule.sched_data)
        stored['manual_instructor_overrides'][0]['occurrence'][
            'activity_id'
        ] = 999
        self.schedule.sched_data = stored
        self.schedule.save(update_fields=['sched_data'])

        result = load_and_apply_instructor_override(self.schedule)

        self.assertEqual(
            {
                diagnostic['code']
                for diagnostic in result['instructor_override_diagnostics']
            },
            {'stale_occurrence_identity', 'active_and_applied'},
        )
        self.assertEqual(len(result['applied_instructor_overrides']), 1)
        self.assertEqual(
            result['applied_instructor_overrides'][0]['occurrence'],
            second_identity,
        )
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data, stored)

    def test_hard_invalid_persisted_override_is_skipped(self):
        self.persist()
        InstructorScheduleAvailability.objects.create(
            organization=self.organization,
            schedule=self.schedule,
            instructor=self.second,
            slot_key='mon_pm1',
            state=InstructorScheduleAvailability.UNAVAILABLE,
        )

        result = run_instructor_assignment(self.schedule)

        diagnostic = result['instructor_override_diagnostics'][0]
        self.assertEqual(diagnostic['code'], 'hard_constraint_rejection')
        self.assertEqual(
            diagnostic['rejection_code'],
            'explicitly_unavailable',
        )
        self.assertEqual(
            result['assignments'][0]['assigned_instructor'],
            self.first,
        )

    def test_malformed_and_ambiguous_active_records_are_skipped(self):
        normalized = normalize_sched_data_structure(self.schedule.sched_data)
        normalized['manual_instructor_overrides'] = ['bad']
        self.schedule.sched_data = normalized
        self.schedule.save(update_fields=['sched_data'])
        malformed = load_and_apply_instructor_override(self.schedule)
        self.assertEqual(
            malformed['instructor_override_diagnostics'][0]['code'],
            'malformed_record',
        )

        record = self.persisted_record_fixture('one')
        normalized['manual_instructor_overrides'] = [
            record,
            {**record, 'override_id': 'two'},
        ]
        self.schedule.sched_data = normalized
        self.schedule.save(update_fields=['sched_data'])
        multiple = load_and_apply_instructor_override(self.schedule)
        self.assertEqual(
            multiple['instructor_override_diagnostics'][0]['code'],
            'ambiguous_active_override',
        )
        self.assertIsNone(multiple['applied_instructor_override'])

    def persisted_record_fixture(self, override_id):
        return {
            'override_id': override_id,
            'action': 'set',
            'status': 'active',
            'schedule_id': self.schedule.pk,
            'organization_id': self.organization.pk,
            'occurrence': self.identity(),
            'instructor_id': self.second.pk,
            'created_at': '2026-07-24T00:00:00Z',
            'coverage_before': 1,
            'coverage_after': 1,
            'coverage_delta': 0,
            'confirmed_coverage_reduction': False,
        }

    def test_same_occurrence_replacement_preserves_append_only_history(self):
        first = self.persist()

        second = self.persist(instructor=self.first, revision=1)

        self.assertTrue(second['ok'])
        self.schedule.refresh_from_db()
        records = self.schedule.sched_data['manual_instructor_overrides']
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]['status'], 'superseded')
        self.assertEqual(
            records[0]['superseded_by'],
            records[1]['override_id'],
        )
        self.assertEqual(records[1]['status'], 'active')
        self.assertNotEqual(
            first['override']['override_id'],
            second['override']['override_id'],
        )
        self.assertEqual(self.schedule.sched_data['instructor_override_revision'], 2)

    def test_second_active_occurrence_is_added_and_both_are_applied(self):
        second_course = Course.objects.create(
            organization=self.organization,
            course_name='Climbing',
            abriviation='CLMB',
            course_len=1,
        )
        data = deepcopy(self.schedule.sched_data)
        data['generated_schedule']['mon_pm2'] = [second_course.course_name]
        self.schedule.sched_data = data
        self.schedule.save(update_fields=['sched_data'])
        self.persist(identity=self.identity(index=0))

        result = self.persist(
            identity=self.identity(index=1),
            revision=1,
        )

        self.assertTrue(result['ok'])
        self.schedule.refresh_from_db()
        self.assertEqual(
            len(self.schedule.sched_data['manual_instructor_overrides']),
            2,
        )
        self.assertEqual(
            {
                record['occurrence']['occurrence_id']
                for record in self.schedule.sched_data[
                    'manual_instructor_overrides'
                ]
                if record['status'] == 'active'
            },
            {
                self.identity(index=0)['occurrence_id'],
                self.identity(index=1)['occurrence_id'],
            },
        )
        with self.assertNumQueries(15):
            replayed = load_and_apply_instructor_override(self.schedule)
        self.assertEqual(len(replayed['applied_instructor_overrides']), 2)

    def test_replacing_one_occurrence_preserves_other_active_override(self):
        second_course = Course.objects.create(
            organization=self.organization,
            course_name='Climbing',
            abriviation='CLMB',
            course_len=1,
        )
        data = deepcopy(self.schedule.sched_data)
        data['generated_schedule']['mon_pm2'] = [second_course.course_name]
        self.schedule.sched_data = data
        self.schedule.save(update_fields=['sched_data'])
        self.persist(identity=self.identity(index=0))
        self.persist(identity=self.identity(index=1), revision=1)

        replacement = self.persist(
            identity=self.identity(index=0),
            instructor=self.first,
            revision=2,
        )

        self.assertTrue(replacement['ok'])
        self.schedule.refresh_from_db()
        records = self.schedule.sched_data['manual_instructor_overrides']
        self.assertEqual(len(records), 3)
        self.assertEqual(
            len([record for record in records if record['status'] == 'active']),
            2,
        )
        self.assertEqual(records[0]['status'], 'superseded')
        self.assertEqual(
            self.schedule.sched_data['instructor_override_revision'],
            3,
        )

    def test_reset_one_appends_event_and_preserves_other_active_override(self):
        other = Course.objects.create(
            organization=self.organization,
            course_name='Climbing',
            abriviation='CLMB',
            course_len=1,
        )
        data = deepcopy(self.schedule.sched_data)
        data['generated_schedule']['mon_pm2'] = [other.course_name]
        data['manual_moves'] = [{'existing': 'activity move'}]
        self.schedule.sched_data = data
        self.schedule.save(update_fields=['sched_data'])
        first_identity = self.identity(index=0)
        second_identity = self.identity(index=1)
        self.persist(identity=first_identity)
        self.persist(identity=second_identity, revision=1)
        before_generated = deepcopy(
            self.schedule.sched_data['generated_schedule']
        )

        result = reset_manual_instructor_override(
            schedule=self.schedule,
            occurrence_identity=first_identity,
            expected_revision=2,
        )

        self.assertTrue(result['ok'])
        self.assertEqual(result['active_override_count_before'], 2)
        self.assertEqual(result['active_override_count_after'], 1)
        self.schedule.refresh_from_db()
        records = self.schedule.sched_data['manual_instructor_overrides']
        self.assertEqual([record['action'] for record in records], [
            'set',
            'set',
            'reset',
        ])
        self.assertEqual(records[0]['status'], 'active')
        self.assertEqual(records[2]['target_override_id'], records[0]['override_id'])
        self.assertEqual(
            self.schedule.sched_data['instructor_override_revision'],
            3,
        )
        self.assertEqual(
            self.schedule.sched_data['generated_schedule'],
            before_generated,
        )
        self.assertEqual(
            self.schedule.sched_data['manual_moves'],
            [{'existing': 'activity move'}],
        )
        replayed = load_and_apply_instructor_override(self.schedule)
        self.assertEqual(len(replayed['applied_instructor_overrides']), 1)
        self.assertEqual(
            replayed['applied_instructor_overrides'][0]['occurrence'],
            second_identity,
        )

    def test_reset_one_can_target_stale_intent_and_conflict_writes_nothing(self):
        identity = self.identity()
        self.persist(identity=identity)
        data = deepcopy(self.schedule.sched_data)
        data['generated_schedule']['mon_pm1'] = ['empty']
        self.schedule.sched_data = data
        self.schedule.save(update_fields=['sched_data'])
        before = deepcopy(data)

        conflict = reset_manual_instructor_override(
            schedule=self.schedule,
            occurrence_identity=identity,
            expected_revision=0,
        )
        self.assertEqual(conflict['code'], 'revision_conflict')
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data, before)

        reset = reset_manual_instructor_override(
            schedule=self.schedule,
            occurrence_identity=identity,
            expected_revision=1,
        )
        self.assertTrue(reset['ok'])
        self.assertEqual(reset['code'], 'reset')

    def test_reset_one_can_remove_missing_instructor_intent(self):
        identity = self.identity()
        self.persist(identity=identity)
        self.second.delete()

        result = reset_manual_instructor_override(
            schedule=self.schedule,
            occurrence_identity=identity,
            expected_revision=1,
        )

        self.assertTrue(result['ok'])
        self.assertEqual(result['active_override_count_after'], 0)

    def test_reset_one_requires_exact_active_composite_identity(self):
        identity = self.identity()
        self.persist(identity=identity)
        wrong = deepcopy(identity)
        wrong['activity_id'] = 999

        result = reset_manual_instructor_override(
            schedule=self.schedule,
            occurrence_identity=wrong,
            expected_revision=1,
        )

        self.assertEqual(result['code'], 'no_matching_active_override')
        self.schedule.refresh_from_db()
        self.assertEqual(
            len(self.schedule.sched_data['manual_instructor_overrides']),
            1,
        )

    def test_reset_all_appends_one_event_preserves_schedule_and_is_idempotent(self):
        self.schedule.sched_data['manual_moves'] = [{'keep': True}]
        self.schedule.save(update_fields=['sched_data'])
        self.persist()
        generated = deepcopy(self.schedule.sched_data['generated_schedule'])

        result = reset_all_manual_instructor_overrides(
            schedule=self.schedule,
            expected_revision=1,
        )

        self.assertTrue(result['ok'])
        self.assertEqual(result['code'], 'reset_all')
        self.assertEqual(result['active_override_count_after'], 0)
        self.assertEqual(
            result['planner_result']['active_instructor_override_intents'],
            (),
        )
        self.schedule.refresh_from_db()
        self.assertEqual(
            [record['action'] for record in self.schedule.sched_data[
                'manual_instructor_overrides'
            ]],
            ['set', 'reset_all'],
        )
        self.assertEqual(self.schedule.sched_data['generated_schedule'], generated)
        self.assertEqual(
            self.schedule.sched_data['manual_moves'],
            [{'keep': True}],
        )
        no_op = reset_all_manual_instructor_overrides(
            schedule=self.schedule,
            expected_revision=2,
        )
        self.assertEqual(no_op['code'], 'no_active_overrides')
        self.assertEqual(no_op['new_revision'], 2)
        self.schedule.refresh_from_db()
        self.assertEqual(
            len(self.schedule.sched_data['manual_instructor_overrides']),
            2,
        )

    def test_new_set_after_reset_all_becomes_active_without_reactivation(self):
        identity = self.identity()
        first = self.persist(identity=identity)
        reset_all_manual_instructor_overrides(
            schedule=self.schedule,
            expected_revision=1,
        )

        later = self.persist(
            identity=identity,
            instructor=self.first,
            revision=2,
        )

        self.assertTrue(later['ok'])
        replayed = load_and_apply_instructor_override(self.schedule)
        self.assertEqual(len(replayed['applied_instructor_overrides']), 1)
        self.assertNotEqual(
            replayed['applied_instructor_overrides'][0]['override_id'],
            first['override']['override_id'],
        )

    def test_reset_all_clears_malformed_prior_lifecycle_diagnostics(self):
        normalized = normalize_sched_data_structure(self.schedule.sched_data)
        normalized['manual_instructor_overrides'] = ['malformed']
        normalized['instructor_override_revision'] = 1
        self.schedule.sched_data = normalized
        self.schedule.save(update_fields=['sched_data'])

        result = reset_all_manual_instructor_overrides(
            schedule=self.schedule,
            expected_revision=1,
        )

        self.assertTrue(result['ok'])
        self.assertEqual(result['code'], 'reset_all')
        self.assertEqual(
            result['planner_result']['instructor_override_diagnostics'][0][
                'code'
            ],
            'no_active_override',
        )

    def coverage_reduction_schedule(self):
        fixed = Course.objects.create(
            organization=self.organization,
            course_name='Fixed Multi',
            abriviation='FIXD',
            course_len=2,
        )
        common = Course.objects.create(
            organization=self.organization,
            course_name='Common',
            abriviation='COMM',
            course_len=1,
        )
        scarce = Course.objects.create(
            organization=self.organization,
            course_name='Scarce',
            abriviation='SCRC',
            course_len=1,
        )
        schedule = self.create_schedule(
            'Reduction Week',
            {
                'ags': ['Group 0', 'Group 1'],
                'mon_pm1': [fixed.course_name, common.course_name],
                'mon_pm2': [fixed.course_name, scarce.course_name],
            },
        )
        certification = Certification.objects.create(
            organization=self.organization,
            name='Scarce Skill',
        )
        InstructorCertification.objects.create(
            instructor=self.second,
            certification=certification,
        )
        ActivityCertificationRequirement.objects.create(
            course=scarce,
            certification=certification,
        )
        return schedule

    def test_coverage_reduction_requires_confirmation_then_persists(self):
        schedule = self.coverage_reduction_schedule()
        identity = self.identity(schedule, index=0)
        before = deepcopy(schedule.sched_data)

        unconfirmed = self.persist(
            schedule=schedule,
            identity=identity,
            instructor=self.second,
        )

        self.assertEqual(
            unconfirmed['code'],
            'coverage_confirmation_required',
        )
        schedule.refresh_from_db()
        self.assertEqual(schedule.sched_data, before)

        confirmed = self.persist(
            schedule=schedule,
            identity=identity,
            instructor=self.second,
            confirm=True,
        )
        self.assertTrue(confirmed['ok'])
        self.assertLess(confirmed['override']['coverage_delta'], 0)
        self.assertTrue(
            confirmed['override']['confirmed_coverage_reduction']
        )

    def test_coverage_neutral_override_needs_no_confirmation(self):
        result = self.persist()

        self.assertTrue(result['ok'])
        self.assertGreaterEqual(result['override']['coverage_delta'], 0)
        self.assertFalse(
            result['override']['confirmed_coverage_reduction']
        )

    def test_equivalent_inputs_have_deterministic_semantics(self):
        first_identity = self.identity()
        second_identity = deepcopy(first_identity)

        first = self.persist(identity=first_identity)
        self.schedule.sched_data = {
            key: value
            for key, value in self.schedule.sched_data.items()
            if key not in {
                'manual_instructor_overrides',
                'instructor_override_revision',
            }
        }
        self.schedule.save(update_fields=['sched_data'])
        second = self.persist(identity=second_identity)

        for result in (first, second):
            override = result['override']
            self.assertEqual(override['occurrence'], first_identity)
            self.assertEqual(override['coverage_before'], 1)
            self.assertEqual(override['coverage_after'], 1)
            self.assertEqual(override['coverage_delta'], 0)

    def test_regeneration_clears_history_and_resets_revision(self):
        self.persist()

        self.schedule.store_generated_schedule({
            'ags': ['Group 0'],
            'mon_pm1': [self.course.course_name],
        })

        self.schedule.refresh_from_db()
        self.assertEqual(
            self.schedule.sched_data['manual_instructor_overrides'],
            [],
        )
        self.assertEqual(
            self.schedule.sched_data['instructor_override_revision'],
            0,
        )
        self.assertEqual(self.schedule.sched_data['manual_moves'], [])

    def test_activity_move_can_make_override_stale_without_deleting_it(self):
        self.persist()
        stored = deepcopy(self.schedule.sched_data)
        stored['manual_moves'] = [{
            'source_block_id': '0:mon_pm1',
            'source_activity_id': self.course.pk,
            'source_activity_name': self.course.course_name,
            'source_occurrence_id': 'occurrence:0:mon_pm1',
            'source_group_index': 0,
            'source_slot_key': 'mon_pm1',
            'target_group_index': 0,
            'target_slot_key': 'mon_pm2',
            'move_type': 'single_block',
            'action_type': 'displacement_move',
            'occurrence_length': 1,
            'source_block_ids': ['0:mon_pm1'],
            'target_block_ids': ['0:mon_pm2'],
            'created_at': '2026-07-24T00:00:00Z',
            'status': 'active',
        }]
        self.schedule.sched_data = stored
        self.schedule.save(update_fields=['sched_data'])

        result = run_instructor_assignment(self.schedule)

        self.assertEqual(
            result['instructor_override_diagnostics'][0]['code'],
            'stale_occurrence_identity',
        )
        self.schedule.refresh_from_db()
        self.assertEqual(
            len(self.schedule.sched_data['manual_instructor_overrides']),
            1,
        )

    def test_no_override_behavior_is_automatic_read_only_and_bounded(self):
        stored = deepcopy(self.schedule.sched_data)

        with self.assertNumQueries(7):
            result = run_instructor_assignment(self.schedule)

        self.assertEqual(
            result['instructor_override_diagnostics'][0]['code'],
            'no_active_override',
        )
        self.assertEqual(
            result['assignments'][0]['assigned_instructor'],
            self.first,
        )
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.sched_data, stored)

    def test_multi_slot_identity_revalidates_complete_footprint(self):
        multi = Course.objects.create(
            organization=self.organization,
            course_name='Multi',
            abriviation='MULT',
            course_len=2,
        )
        schedule = self.create_schedule(
            'Multi Week',
            {
                'ags': ['Group 0'],
                'tue_am1': [multi.course_name],
                'tue_am2': [multi.course_name],
            },
        )
        identity = self.identity(schedule)

        persisted = self.persist(
            schedule=schedule,
            identity=identity,
        )
        self.assertTrue(persisted['ok'])
        self.assertEqual(
            len(persisted['override']['occurrence']['slot_footprint']),
            2,
        )

        stored = deepcopy(schedule.sched_data)
        stored['manual_instructor_overrides'][0]['occurrence'][
            'slot_footprint'
        ].pop()
        schedule.sched_data = stored
        schedule.save(update_fields=['sched_data'])
        loaded = run_instructor_assignment(schedule)
        self.assertEqual(
            loaded['instructor_override_diagnostics'][0]['code'],
            'stale_occurrence_identity',
        )

    def test_unsupported_instructor_count_is_hard_rejected(self):
        identity = self.identity()
        self.course.required_instructor_count = 2
        self.course.save(update_fields=['required_instructor_count'])

        result = self.persist(identity=identity)

        self.assertEqual(
            result['code'],
            'hard_constraint_rejection',
        )
        self.assertEqual(
            result['rejection_code'],
            'unsupported_instructor_count',
        )
