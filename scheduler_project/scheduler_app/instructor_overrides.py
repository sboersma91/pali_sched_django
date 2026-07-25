"""Persistence and read-only replay for manual instructor assignment intent."""

from copy import deepcopy
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .instructor_assignment import _run_instructor_assignment_core
from .models import Instructor
from .schedule_blocks import SCHEDULE_SLOT_KEYS
from .schedule_operations import (
    MalformedSchedDataError,
    normalize_sched_data_structure,
)


ACTIVE_STATUS = 'active'
SET_ACTION = 'set'
RESET_ACTION = 'reset'
RESET_ALL_ACTION = 'reset_all'
APPLIED_STATUS = 'applied'


def _result(code, *, ok=False, **details):
    return {
        'ok': ok,
        'code': code,
        **details,
    }


def _ordered_footprint(footprint):
    slot_order = {
        slot_key: index for index, slot_key in enumerate(SCHEDULE_SLOT_KEYS)
    }
    return [
        {
            'block_id': slot.get('block_id'),
            'slot_key': slot.get('slot_key'),
            'position': slot.get('position'),
        }
        for slot in sorted(
            footprint or (),
            key=lambda slot: (
                slot_order.get(slot.get('slot_key'), len(slot_order)),
                slot.get('position') or 0,
                slot.get('block_id') or '',
            ),
        )
    ]


def build_occurrence_identity(schedule, occurrence):
    """Return the JSON-safe composite identity for one current occurrence."""
    return {
        'schedule_id': schedule.pk,
        'organization_id': schedule.organization_id,
        'occurrence_id': occurrence.get('occurrence_id'),
        'group_index': occurrence.get('group_index'),
        'activity_id': occurrence.get('activity_id'),
        'slot_footprint': _ordered_footprint(
            occurrence.get('slot_footprint')
        ),
    }


def _identity_mismatches(expected, current):
    fields = (
        'schedule_id',
        'organization_id',
        'occurrence_id',
        'group_index',
        'activity_id',
        'slot_footprint',
    )
    return tuple(
        field for field in fields if expected.get(field) != current.get(field)
    )


def _resolve_occurrence(schedule, occurrences, identity):
    if not isinstance(identity, dict):
        return None, _result('malformed_record')
    occurrence_id = identity.get('occurrence_id')
    occurrence = next(
        (
            candidate for candidate in occurrences
            if candidate.get('occurrence_id') == occurrence_id
        ),
        None,
    )
    if occurrence is None:
        possible_matches = [
            candidate for candidate in occurrences
            if identity.get('activity_id') == candidate.get('activity_id')
            and identity.get('group_index') == candidate.get('group_index')
        ]
        occurrence = possible_matches[0] if len(possible_matches) == 1 else None
        if occurrence is None:
            return None, _result('missing_occurrence')
    current_identity = build_occurrence_identity(schedule, occurrence)
    mismatches = _identity_mismatches(identity, current_identity)
    if mismatches:
        return None, _result(
            'stale_occurrence_identity',
            mismatched_fields=mismatches,
            current_identity=current_identity,
        )
    return occurrence, None


def _active_records(records):
    """Resolve active set intents by replaying lifecycle events in order."""
    diagnostics = []
    active_by_key = {}
    active_by_id = {}
    ambiguous_keys = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            diagnostics.append(_result('malformed_record', record_index=index))
            continue
        action = record.get('action')
        status = record.get('status')
        if action == SET_ACTION and status == 'superseded':
            continue
        if action == SET_ACTION:
            if (
                status != ACTIVE_STATUS
                or not record.get('override_id')
                or not isinstance(record.get('occurrence'), dict)
                or not isinstance(record.get('instructor_id'), int)
                or isinstance(record.get('instructor_id'), bool)
            ):
                diagnostics.append(_result(
                    'malformed_record',
                    record_index=index,
                ))
                continue
            key = _active_occurrence_key(record)
            if key in ambiguous_keys:
                diagnostics.append(_result(
                    'ambiguous_active_override',
                    occurrence_key=key,
                    active_override_ids=(record['override_id'],),
                ))
                continue
            if key in active_by_key:
                diagnostics.append(_result(
                    'ambiguous_active_override',
                    occurrence_key=key,
                    active_override_ids=(
                        active_by_key[key][1]['override_id'],
                        record['override_id'],
                    ),
                ))
                active_by_id.pop(
                    active_by_key[key][1]['override_id'],
                    None,
                )
                active_by_key.pop(key, None)
                ambiguous_keys.add(key)
                continue
            item = (index, record)
            active_by_key[key] = item
            active_by_id[record['override_id']] = item
            continue
        if action == RESET_ACTION:
            if (
                status != APPLIED_STATUS
                or not record.get('override_id')
                or not isinstance(record.get('target_override_id'), str)
                or not isinstance(record.get('occurrence'), dict)
            ):
                diagnostics.append(_result(
                    'malformed_reset_record',
                    record_index=index,
                ))
                continue
            target = active_by_id.get(record['target_override_id'])
            if (
                target is None
                or target[1].get('occurrence') != record['occurrence']
            ):
                diagnostics.append(_result(
                    'malformed_reset_record',
                    record_index=index,
                ))
                continue
            active_by_id.pop(record['target_override_id'], None)
            active_by_key.pop(_active_occurrence_key(target[1]), None)
            continue
        if action == RESET_ALL_ACTION:
            if (
                status != APPLIED_STATUS
                or not record.get('override_id')
                or not isinstance(record.get('target_revision'), int)
                or isinstance(record.get('target_revision'), bool)
            ):
                diagnostics.append(_result(
                    'malformed_reset_all_record',
                    record_index=index,
                ))
                continue
            active_by_key.clear()
            active_by_id.clear()
            ambiguous_keys.clear()
            diagnostics.clear()
            continue
        diagnostics.append(_result('malformed_record', record_index=index))
    return sorted(active_by_id.values(), key=lambda item: item[0]), diagnostics


def _active_occurrence_key(record):
    occurrence = record.get('occurrence') or {}
    return (
        occurrence.get('schedule_id'),
        occurrence.get('organization_id'),
        occurrence.get('occurrence_id'),
    )


def _resolve_active_overrides(schedule, occurrences, records):
    """Resolve history-active records in bulk without mutating stored history."""
    unambiguous, diagnostics = _active_records(records)
    instructors = Instructor.objects.in_bulk({
        record['instructor_id'] for _index, record in unambiguous
    })
    resolved = []
    for index, record in sorted(unambiguous, key=lambda item: item[0]):
        if (
            record.get('schedule_id') != schedule.pk
            or record['occurrence'].get('schedule_id') != schedule.pk
        ):
            diagnostics.append(_result(
                'stale_occurrence_identity',
                override_id=record['override_id'],
            ))
            continue
        if (
            record.get('organization_id') != schedule.organization_id
            or record['occurrence'].get('organization_id')
            != schedule.organization_id
        ):
            diagnostics.append(_result(
                'organization_mismatch',
                override_id=record['override_id'],
            ))
            continue
        occurrence, error = _resolve_occurrence(
            schedule,
            occurrences,
            record['occurrence'],
        )
        if error:
            diagnostics.append({
                **error,
                'override_id': record['override_id'],
            })
            continue
        instructor = instructors.get(record['instructor_id'])
        if instructor is None:
            diagnostics.append(_result(
                'missing_instructor',
                override_id=record['override_id'],
            ))
            continue
        if instructor.organization_id != schedule.organization_id:
            diagnostics.append(_result(
                'organization_mismatch',
                override_id=record['override_id'],
            ))
            continue
        resolved.append({
            'record_index': index,
            'record': record,
            'occurrence': occurrence,
            'instructor': instructor,
        })
    return resolved, diagnostics, any(
        diagnostic['code'] == 'ambiguous_active_override'
        for diagnostic in diagnostics
    )


def _automatic_result_with_diagnostics(schedule, diagnostics):
    result = _run_instructor_assignment_core(schedule)
    result['instructor_override_diagnostics'] = tuple(diagnostics)
    result['applied_instructor_override'] = None
    result['applied_instructor_overrides'] = ()
    return result


def _plan_with_override_history(schedule, normalized):
    automatic = _run_instructor_assignment_core(schedule)
    has_resettable_intent = _history_has_resettable_intent(
        normalized['manual_instructor_overrides']
    )
    history_active, _history_diagnostics = _active_records(
        normalized['manual_instructor_overrides']
    )
    resolved, diagnostics, ambiguous = _resolve_active_overrides(
        schedule,
        automatic['occurrences'],
        normalized['manual_instructor_overrides'],
    )
    reset_targets = tuple(deepcopy(record) for _index, record in history_active)
    if not resolved:
        if diagnostics:
            automatic['instructor_override_diagnostics'] = tuple(diagnostics)
            automatic['applied_instructor_override'] = None
            automatic['applied_instructor_overrides'] = ()
            automatic['active_instructor_override_intents'] = reset_targets
            automatic['has_resettable_instructor_override_intent'] = (
                has_resettable_intent
            )
            return automatic
        automatic['instructor_override_diagnostics'] = (
            _result('no_active_override', ok=True),
        )
        automatic['applied_instructor_override'] = None
        automatic['applied_instructor_overrides'] = ()
        automatic['active_instructor_override_intents'] = ()
        automatic['has_resettable_instructor_override_intent'] = False
        return automatic

    planned = _run_instructor_assignment_core(
        schedule,
        fixed_assignments=tuple({
            'occurrence': item['occurrence'],
            'instructor': item['instructor'],
        } for item in resolved),
    )
    accepted = []
    rejected = []
    for item, fixed_diagnostic in zip(
        resolved,
        planned['fixed_assignment_diagnostics'],
    ):
        if fixed_diagnostic['accepted']:
            accepted.append(item)
        else:
            rejected.append(_result(
                'hard_constraint_rejection',
                override_id=item['record']['override_id'],
                rejection_code=fixed_diagnostic['rejection_code'],
                affected_slot_keys=fixed_diagnostic['affected_slot_keys'],
            ))
    if len(accepted) != len(resolved):
        diagnostics.extend(rejected)
        if accepted:
            planned = _run_instructor_assignment_core(
                schedule,
                fixed_assignments=tuple({
                    'occurrence': item['occurrence'],
                    'instructor': item['instructor'],
                } for item in accepted),
            )
        else:
            planned = automatic
    diagnostics.extend(
        _result(
            'active_and_applied',
            ok=True,
            override_id=item['record']['override_id'],
        )
        for item in accepted
    )
    if not accepted:
        automatic['instructor_override_diagnostics'] = tuple(diagnostics)
        automatic['applied_instructor_override'] = None
        automatic['applied_instructor_overrides'] = ()
        automatic['active_instructor_override_intents'] = reset_targets
        automatic['has_resettable_instructor_override_intent'] = (
            has_resettable_intent
        )
        return automatic

    applied_records = tuple(deepcopy(item['record']) for item in accepted)
    planned['instructor_override_diagnostics'] = tuple(diagnostics)
    planned['applied_instructor_overrides'] = applied_records
    planned['applied_instructor_override'] = (
        applied_records[0] if len(applied_records) == 1 else None
    )
    planned['active_instructor_override_intents'] = reset_targets
    planned['has_resettable_instructor_override_intent'] = has_resettable_intent
    return planned


def load_and_apply_instructor_override(schedule):
    """Read, revalidate, and apply all compatible active override intents."""
    try:
        normalized = normalize_sched_data_structure(schedule.sched_data)
    except MalformedSchedDataError:
        return _automatic_result_with_diagnostics(
            schedule,
            (_result('malformed_record'),),
        )
    return _plan_with_override_history(schedule, normalized)


def persist_manual_instructor_override(
    *,
    schedule,
    occurrence_identity,
    instructor_id,
    expected_revision,
    confirm_coverage_reduction=False,
):
    """Validate under the schedule row lock and append one intent event."""
    if schedule.pk is None:
        return _result('missing_schedule')

    with transaction.atomic():
        locked = type(schedule).objects.select_for_update().get(pk=schedule.pk)
        if schedule.organization_id != locked.organization_id:
            return _result('organization_mismatch')
        try:
            normalized = normalize_sched_data_structure(locked.sched_data)
        except MalformedSchedDataError:
            return _result('malformed_record')

        current_revision = normalized['instructor_override_revision']
        if expected_revision != current_revision:
            return _result(
                'revision_conflict',
                expected_revision=expected_revision,
                current_revision=current_revision,
            )

        try:
            automatic = _run_instructor_assignment_core(locked)
        except ValidationError as error:
            if 'required instructor count must be 1' in str(error):
                return _result(
                    'hard_constraint_rejection',
                    rejection_code='unsupported_instructor_count',
                )
            raise
        occurrence, occurrence_error = _resolve_occurrence(
            locked,
            automatic['occurrences'],
            occurrence_identity,
        )
        if occurrence_error:
            return occurrence_error

        resolved, history_diagnostics, ambiguous = _resolve_active_overrides(
            locked,
            automatic['occurrences'],
            normalized['manual_instructor_overrides'],
        )
        if ambiguous:
            return _result(
                'ambiguous_active_override',
                diagnostics=tuple(history_diagnostics),
            )
        if any(
            diagnostic.get('code', '').startswith('malformed')
            for diagnostic in history_diagnostics
        ):
            return _result(
                'malformed_record',
                diagnostics=tuple(history_diagnostics),
            )

        instructor = Instructor.objects.filter(pk=instructor_id).first()
        if instructor is None:
            return _result('missing_instructor')
        if instructor.organization_id != locked.organization_id:
            return _result('organization_mismatch')

        current_fixed = tuple({
            'occurrence': item['occurrence'],
            'instructor': item['instructor'],
        } for item in resolved)
        current_plan = (
            _run_instructor_assignment_core(
                locked,
                fixed_assignments=current_fixed,
            )
            if current_fixed else automatic
        )
        if current_fixed and not all(
            diagnostic['accepted']
            for diagnostic in current_plan['fixed_assignment_diagnostics']
        ):
            resolved = [
                item for item, diagnostic in zip(
                    resolved,
                    current_plan['fixed_assignment_diagnostics'],
                )
                if diagnostic['accepted']
            ]
            current_fixed = tuple({
                'occurrence': item['occurrence'],
                'instructor': item['instructor'],
            } for item in resolved)
            current_plan = (
                _run_instructor_assignment_core(
                    locked,
                    fixed_assignments=current_fixed,
                )
                if current_fixed else automatic
            )

        target_id = occurrence.get('occurrence_id')
        history_active, _malformed = _active_records(
            normalized['manual_instructor_overrides']
        )
        history_target = next(
            (
                (index, record)
                for index, record in history_active
                if (record.get('occurrence') or {}).get('occurrence_id')
                == target_id
            ),
            None,
        )
        retained = [
            item for item in resolved
            if item['occurrence'].get('occurrence_id') != target_id
        ]
        proposed_fixed = tuple(
            [{
                'occurrence': item['occurrence'],
                'instructor': item['instructor'],
            } for item in retained]
            + [{'occurrence': occurrence, 'instructor': instructor}]
        )
        planned = _run_instructor_assignment_core(
            locked,
            fixed_assignments=proposed_fixed,
        )
        rejected = next(
            (
                diagnostic
                for diagnostic in planned['fixed_assignment_diagnostics']
                if not diagnostic['accepted']
            ),
            None,
        )
        if rejected:
            return _result(
                'hard_constraint_rejection',
                rejection_code=rejected['rejection_code'],
                affected_slot_keys=rejected['affected_slot_keys'],
            )
        coverage_before = current_plan['coverage']['assigned_occurrence_count']
        coverage_after = planned['coverage']['assigned_occurrence_count']
        planner_summary = {
            'occurrence': occurrence,
            'instructor': instructor,
            'accepted': True,
            'rejection_code': None,
            'affected_slot_keys': (),
            'coverage_before': coverage_before,
            'coverage_after': coverage_after,
            'coverage_delta': coverage_after - coverage_before,
            'requires_confirmation': coverage_after < coverage_before,
        }
        if (
            planner_summary['requires_confirmation']
            and not confirm_coverage_reduction
        ):
            return _result(
                'coverage_confirmation_required',
                planner_summary=deepcopy(planner_summary),
                planner_result=planned,
                current_revision=current_revision,
            )

        override_id = str(uuid4())
        created_at = timezone.now().isoformat().replace('+00:00', 'Z')
        if history_target:
            active_index, prior_record = history_target
            superseded = deepcopy(prior_record)
            superseded['status'] = 'superseded'
            superseded['superseded_at'] = created_at
            superseded['superseded_by'] = override_id
            normalized['manual_instructor_overrides'][active_index] = superseded

        record = {
            'override_id': override_id,
            'action': SET_ACTION,
            'status': ACTIVE_STATUS,
            'schedule_id': locked.pk,
            'organization_id': locked.organization_id,
            'occurrence': build_occurrence_identity(locked, occurrence),
            'instructor_id': instructor.pk,
            'created_at': created_at,
            'coverage_before': coverage_before,
            'coverage_after': coverage_after,
            'coverage_delta': coverage_after - coverage_before,
            'confirmed_coverage_reduction': bool(
                planner_summary['requires_confirmation']
                and confirm_coverage_reduction
            ),
        }
        normalized['manual_instructor_overrides'].append(record)
        new_revision = current_revision + 1
        normalized['instructor_override_revision'] = new_revision
        locked.sched_data = normalized
        locked.save(update_fields=['sched_data'])

    schedule.sched_data = deepcopy(normalized)
    return _result(
        'persisted',
        ok=True,
        override=deepcopy(record),
        planner_result=planned,
        new_revision=new_revision,
    )


def _revision_conflict(expected_revision, current_revision):
    return _result(
        'revision_conflict',
        expected_revision=expected_revision,
        current_revision=current_revision,
    )


def _history_has_resettable_intent(records):
    for record in reversed(records):
        if not isinstance(record, dict):
            return True
        if (
            record.get('action') == RESET_ALL_ACTION
            and record.get('status') == APPLIED_STATUS
        ):
            return False
        if (
            record.get('action') == SET_ACTION
            and record.get('status') == ACTIVE_STATUS
        ):
            return True
    return False


def reset_manual_instructor_override(
    *,
    schedule,
    occurrence_identity,
    expected_revision,
):
    """Append a reset event for one exactly identified active set intent."""
    if schedule.pk is None:
        return _result('missing_schedule')
    if not isinstance(occurrence_identity, dict):
        return _result('malformed_record')

    with transaction.atomic():
        locked = type(schedule).objects.select_for_update().get(pk=schedule.pk)
        if schedule.organization_id != locked.organization_id:
            return _result('organization_mismatch')
        try:
            normalized = normalize_sched_data_structure(locked.sched_data)
        except MalformedSchedDataError:
            return _result('malformed_record')
        current_revision = normalized['instructor_override_revision']
        if expected_revision != current_revision:
            return _revision_conflict(expected_revision, current_revision)

        active, diagnostics = _active_records(
            normalized['manual_instructor_overrides']
        )
        if any(
            diagnostic['code'] in {
                'ambiguous_active_override',
                'malformed_record',
                'malformed_reset_record',
                'malformed_reset_all_record',
            }
            for diagnostic in diagnostics
        ):
            return _result(
                'ambiguous_override_history',
                diagnostics=tuple(diagnostics),
            )
        target = next(
            (
                record for _index, record in active
                if record['occurrence'] == occurrence_identity
            ),
            None,
        )
        if target is None:
            return _result('no_matching_active_override')
        if (
            occurrence_identity.get('schedule_id') != locked.pk
            or occurrence_identity.get('organization_id')
            != locked.organization_id
        ):
            return _result('no_matching_active_override')

        active_before = len(active)
        event = {
            'override_id': str(uuid4()),
            'action': RESET_ACTION,
            'status': APPLIED_STATUS,
            'schedule_id': locked.pk,
            'organization_id': locked.organization_id,
            'target_override_id': target['override_id'],
            'occurrence': deepcopy(target['occurrence']),
            'created_at': timezone.now().isoformat().replace('+00:00', 'Z'),
        }
        normalized['manual_instructor_overrides'].append(event)
        new_revision = current_revision + 1
        normalized['instructor_override_revision'] = new_revision
        locked.sched_data = normalized
        planner_result = _plan_with_override_history(locked, normalized)
        locked.save(update_fields=['sched_data'])

    schedule.sched_data = deepcopy(normalized)
    return _result(
        'reset',
        ok=True,
        reset_action=RESET_ACTION,
        old_revision=current_revision,
        new_revision=new_revision,
        affected_occurrence=deepcopy(target['occurrence']),
        target_override_id=target['override_id'],
        active_override_count_before=active_before,
        active_override_count_after=active_before - 1,
        planner_result=planner_result,
    )


def reset_all_manual_instructor_overrides(
    *,
    schedule,
    expected_revision,
):
    """Append one reset-all event and return a fully automatic plan."""
    if schedule.pk is None:
        return _result('missing_schedule')

    with transaction.atomic():
        locked = type(schedule).objects.select_for_update().get(pk=schedule.pk)
        if schedule.organization_id != locked.organization_id:
            return _result('organization_mismatch')
        try:
            normalized = normalize_sched_data_structure(locked.sched_data)
        except MalformedSchedDataError:
            return _result('malformed_record')
        current_revision = normalized['instructor_override_revision']
        if expected_revision != current_revision:
            return _revision_conflict(expected_revision, current_revision)

        active, _diagnostics = _active_records(
            normalized['manual_instructor_overrides']
        )
        if not _history_has_resettable_intent(
            normalized['manual_instructor_overrides']
        ):
            planner_result = _plan_with_override_history(locked, normalized)
            return _result(
                'no_active_overrides',
                ok=True,
                reset_action=RESET_ALL_ACTION,
                old_revision=current_revision,
                new_revision=current_revision,
                active_override_count_before=0,
                active_override_count_after=0,
                planner_result=planner_result,
            )

        event = {
            'override_id': str(uuid4()),
            'action': RESET_ALL_ACTION,
            'status': APPLIED_STATUS,
            'schedule_id': locked.pk,
            'organization_id': locked.organization_id,
            'target_revision': current_revision,
            'created_at': timezone.now().isoformat().replace('+00:00', 'Z'),
        }
        normalized['manual_instructor_overrides'].append(event)
        new_revision = current_revision + 1
        normalized['instructor_override_revision'] = new_revision
        locked.sched_data = normalized
        planner_result = _plan_with_override_history(locked, normalized)
        locked.save(update_fields=['sched_data'])

    schedule.sched_data = deepcopy(normalized)
    return _result(
        'reset_all',
        ok=True,
        reset_action=RESET_ALL_ACTION,
        old_revision=current_revision,
        new_revision=new_revision,
        active_override_count_before=len(active),
        active_override_count_after=0,
        planner_result=planner_result,
    )
