from copy import deepcopy

from django.db import transaction
from django.utils import timezone

from .models import Course


SCHEDULE_DAYS = [
    {'name': 'Monday', 'slots': [
        {'label': 'PM1', 'key': 'mon_pm1'}, {'label': 'PM2', 'key': 'mon_pm2'}, {'label': 'Night', 'key': 'mon_night'},
    ]},
    {'name': 'Tuesday', 'slots': [
        {'label': 'AM1', 'key': 'tue_am1'}, {'label': 'AM2', 'key': 'tue_am2'},
        {'label': 'PM1', 'key': 'tue_pm1'}, {'label': 'PM2', 'key': 'tue_pm2'}, {'label': 'Night', 'key': 'tue_night'},
    ]},
    {'name': 'Wednesday', 'slots': [
        {'label': 'AM1', 'key': 'wed_am1'}, {'label': 'AM2', 'key': 'wed_am2'},
        {'label': 'PM1', 'key': 'wed_pm1'}, {'label': 'PM2', 'key': 'wed_pm2'}, {'label': 'Night', 'key': 'wed_night'},
    ]},
    {'name': 'Thursday', 'slots': [
        {'label': 'AM1', 'key': 'thur_am1'}, {'label': 'AM2', 'key': 'thur_am2'},
        {'label': 'PM1', 'key': 'thur_pm1'}, {'label': 'PM2', 'key': 'thur_pm2'}, {'label': 'Night', 'key': 'thur_night'},
    ]},
    {'name': 'Friday', 'slots': [
        {'label': 'AM1', 'key': 'fri_am1'}, {'label': 'AM2', 'key': 'fri_am2'},
    ]},
]

SCHEDULE_DISPLAY_VALUES = {'g_box': '/////', 'empty': '****'}
MOVE_CONFLICT_SEVERITY = {
    'duplicate_group_slot': 'warning',
    'broken_multi_block': 'error',
    'invalid_time_slot': 'warning',
}
SOURCE_IDENTITY_CHANGED_MESSAGE = (
    'This proposal cannot be saved because the generated schedule changed since selection.'
)
MANUAL_MOVE_REQUIRED_FIELDS = (
    'source_block_id',
    'source_activity_id',
    'source_activity_name',
    'source_occurrence_id',
    'source_group_index',
    'source_slot_key',
    'target_group_index',
    'target_slot_key',
)


def build_schedule_blocks(schedule):
    activity_names = {
        value
        for day in SCHEDULE_DAYS
        for slot in day['slots']
        for value in schedule.get(slot['key'], [])
        if value not in SCHEDULE_DISPLAY_VALUES and value
    }
    activities = {
        course_name: {'id': activity_id, 'length': course_len}
        for course_name, activity_id, course_len in Course.objects.filter(
            course_name__in=activity_names
        ).values_list('course_name', 'id', 'course_len')
    }

    schedule_rows = []
    for group_index, group_label in enumerate(schedule.get('ags', [])):
        cells = []
        for day in SCHEDULE_DAYS:
            for slot in day['slots']:
                slot_values = schedule.get(slot['key'], [])
                raw_value = slot_values[group_index] if group_index < len(slot_values) else ''
                is_empty = raw_value == 'empty'
                is_unavailable = raw_value == 'g_box'
                is_activity = bool(raw_value) and not is_empty and not is_unavailable
                activity = activities.get(raw_value, {})
                block_id = f'{group_index}:{slot["key"]}'
                cells.append({
                    'block_id': block_id,
                    'group_index': group_index,
                    'group_label': group_label,
                    'slot_key': slot['key'],
                    'slot_label': slot['label'],
                    'raw_value': raw_value,
                    'display_value': SCHEDULE_DISPLAY_VALUES.get(raw_value, raw_value),
                    'activity_id': activity.get('id'),
                    'activity_length': activity.get('length'),
                    'is_activity': is_activity,
                    'is_empty': is_empty,
                    'is_unavailable': is_unavailable,
                    'occurrence_id': f'occurrence:{block_id}' if is_activity else None,
                    'occurrence_length': 1 if is_activity else None,
                    'occurrence_position': 1 if is_activity else None,
                    'is_multi_block': False,
                    'is_proposed_source': False,
                    'is_proposed_target': False,
                    'proposed_from_block_id': None,
                    'proposed_to_block_id': None,
                    'has_overlap': False,
                    'overlapping_blocks': [],
                    'conflicts': [],
                })

        cells_by_slot = {cell['slot_key']: cell for cell in cells}
        for cell in cells:
            if not cell['slot_key'].endswith('1'):
                continue
            if activities.get(cell['raw_value'], {}).get('length') != 2:
                continue

            paired_cell = cells_by_slot.get(f'{cell["slot_key"][:-1]}2')
            if not paired_cell or paired_cell['raw_value'] != cell['raw_value']:
                continue

            occurrence_id = f'occurrence:{cell["block_id"]}'
            for position, occurrence_cell in enumerate((cell, paired_cell), start=1):
                occurrence_cell.update({
                    'occurrence_id': occurrence_id,
                    'occurrence_length': 2,
                    'occurrence_position': position,
                    'is_multi_block': True,
                })

        schedule_rows.append({
            'ag': group_label,
            'group_index': group_index,
            'cells': cells,
        })

    return schedule_rows


def iter_schedule_blocks(blocks):
    for row in blocks:
        for block in row['cells']:
            yield block
            yield from block.get('overlapping_blocks', [])


def verify_move_proposal_source(blocks, proposal):
    all_blocks = list(iter_schedule_blocks(blocks))
    blocks_by_id = {block['block_id']: block for block in all_blocks}
    source_block_id = proposal.get('source_block_id')
    source = blocks_by_id.get(source_block_id)
    result = {
        'verified': False,
        'error': None,
        'message': '',
        'source_block_id': source_block_id,
        'source': source,
    }

    def reject(error, message):
        result.update({'error': error, 'message': message})
        return result

    if not source:
        return reject('invalid_source', 'The selected source block ID is invalid.')
    if not source['is_activity'] or source['is_empty'] or source['is_unavailable']:
        return reject('stale_source', SOURCE_IDENTITY_CHANGED_MESSAGE)
    if source['is_multi_block'] or source.get('occurrence_length') != 1:
        return reject('multi_block_not_supported', 'Multi-block activity moves are not supported yet.')

    expected_activity_id = proposal.get('source_activity_id')
    expected_activity_name = proposal.get('source_activity_name')
    expected_occurrence_id = proposal.get('source_occurrence_id')
    if (
        expected_activity_id is None
        or not expected_activity_name
        or source.get('activity_id') != expected_activity_id
        or source.get('raw_value') != expected_activity_name
        or source.get('display_value') != expected_activity_name
        or (
            expected_occurrence_id is not None
            and source.get('occurrence_id') != expected_occurrence_id
        )
    ):
        return reject('source_identity_mismatch', SOURCE_IDENTITY_CHANGED_MESSAGE)

    result['verified'] = True
    return result


def apply_move_proposal(blocks, proposal):
    valid_group_indices = {row['group_index'] for row in blocks}
    valid_slot_keys = {
        slot['key']
        for day in SCHEDULE_DAYS
        for slot in day['slots']
    }
    blocks_by_group_slot = {
        (block['group_index'], block['slot_key']): block
        for row in blocks
        for block in row['cells']
    }
    source_block_id = proposal.get('source_block_id')
    target_slot_key = proposal.get('target_slot_key')
    target_group_index = proposal.get('target_group_index')
    source_verification = verify_move_proposal_source(blocks, proposal)
    result = {
        'applied': False,
        'error': None,
        'message': '',
        'source_block_id': source_block_id,
        'source_activity_id': proposal.get('source_activity_id'),
        'source_activity_name': proposal.get('source_activity_name'),
        'source_occurrence_id': proposal.get('source_occurrence_id'),
        'source_group_index': None,
        'source_slot_key': None,
        'target_block_id': None,
        'target_slot_key': target_slot_key,
        'target_group_index': target_group_index,
        'move_type': 'single_block',
        'source_identity_verified': source_verification['verified'],
    }

    def reject(error, message):
        result.update({'error': error, 'message': message})
        return result

    if not source_verification['verified']:
        return reject(source_verification['error'], source_verification['message'])
    source = source_verification['source']
    result.update({
        'source_activity_id': source['activity_id'],
        'source_activity_name': source['raw_value'],
        'source_occurrence_id': source['occurrence_id'],
        'source_group_index': source['group_index'],
        'source_slot_key': source['slot_key'],
    })
    if not isinstance(target_group_index, int):
        return reject('invalid_target_group', 'The target activity group is invalid.')
    if target_group_index not in valid_group_indices:
        return reject('invalid_target_group', 'The target activity group is invalid or stale.')
    if target_slot_key not in valid_slot_keys:
        return reject('invalid_target_slot', 'The target time slot is invalid.')

    target = blocks_by_group_slot.get((target_group_index, target_slot_key))
    if not target:
        return reject('invalid_target_slot', 'The target activity group or time slot is invalid.')
    result['target_block_id'] = target['block_id']
    if target['block_id'] == source['block_id']:
        return reject('same_source_and_target', 'The target is the current activity block.')
    if target['is_unavailable']:
        return reject('target_unavailable', 'The target block is unavailable for this activity group.')
    if not target['is_empty'] and not target['is_activity']:
        return reject('target_not_available', 'The target block is not available for a move proposal.')

    activity_values = {
        key: source[key]
        for key in (
            'raw_value',
            'display_value',
            'activity_id',
            'activity_length',
        )
    }
    source.update({
        'raw_value': 'empty',
        'display_value': SCHEDULE_DISPLAY_VALUES['empty'],
        'activity_id': None,
        'activity_length': None,
        'is_activity': False,
        'is_empty': True,
        'is_unavailable': False,
        'occurrence_id': None,
        'occurrence_length': None,
        'occurrence_position': None,
        'is_multi_block': False,
        'is_proposed_source': True,
        'is_proposed_target': False,
        'proposed_from_block_id': None,
        'proposed_to_block_id': target['block_id'],
    })
    if target['is_activity']:
        proposed_target = {
            **deepcopy(source),
            **activity_values,
            'block_id': f'overlap:{target["block_id"]}:{source["block_id"]}',
            'group_index': target['group_index'],
            'group_label': target['group_label'],
            'slot_key': target['slot_key'],
            'slot_label': target['slot_label'],
            'is_activity': True,
            'is_empty': False,
            'is_unavailable': False,
            'occurrence_id': f'occurrence:overlap:{target["block_id"]}:{source["block_id"]}',
            'occurrence_length': 1,
            'occurrence_position': 1,
            'is_multi_block': False,
            'is_proposed_source': False,
            'is_proposed_target': True,
            'proposed_from_block_id': source['block_id'],
            'proposed_to_block_id': None,
            'overlapping_blocks': [],
            'conflicts': [],
        }
        target['overlapping_blocks'].append(proposed_target)
        target['has_overlap'] = True
        source['proposed_to_block_id'] = proposed_target['block_id']
        result.update({
            'target_block_id': proposed_target['block_id'],
            'target_was_occupied': True,
            'target_activity': target['display_value'],
        })
    else:
        target.update({
            **activity_values,
            'is_activity': True,
            'is_empty': False,
            'is_unavailable': False,
            'occurrence_id': f'occurrence:{target["block_id"]}',
            'occurrence_length': 1,
            'occurrence_position': 1,
            'is_multi_block': False,
            'is_proposed_source': False,
            'is_proposed_target': True,
            'proposed_from_block_id': source['block_id'],
            'proposed_to_block_id': None,
        })
        proposed_target = target
        result['target_was_occupied'] = False
    result.update({
        'applied': True,
        'message': 'The move proposal was applied in memory only.',
        'activity': proposed_target['display_value'],
        'source_group_label': source['group_label'],
        'source_slot_label': source['slot_label'],
        'target_group_label': target['group_label'],
        'target_slot_label': target['slot_label'],
    })
    return result


def evaluate_move_proposal_for_save(proposal_result):
    blocking_conflicts = []
    warning_conflicts = []
    informational_conflicts = []

    if not proposal_result or not proposal_result.get('applied'):
        conflict = {
            'type': proposal_result.get('error', 'invalid_proposal') if proposal_result else 'invalid_proposal',
            'severity': 'error',
            'message': proposal_result.get('message', 'The move proposal is invalid.') if proposal_result else 'The move proposal is invalid.',
            'related_block_ids': [
                block_id
                for block_id in (
                    proposal_result.get('source_block_id') if proposal_result else None,
                    proposal_result.get('target_block_id') if proposal_result else None,
                )
                if block_id
            ],
        }
        blocking_conflicts.append(conflict)
    else:
        for conflict in proposal_result.get('conflicts', []):
            policy_severity = MOVE_CONFLICT_SEVERITY.get(
                conflict.get('type'),
                conflict.get('severity', 'error'),
            )
            policy_conflict = {**conflict, 'severity': policy_severity}
            if policy_severity == 'info':
                informational_conflicts.append(policy_conflict)
            elif policy_severity == 'warning':
                warning_conflicts.append(policy_conflict)
            else:
                blocking_conflicts.append(policy_conflict)

    can_save = not blocking_conflicts
    if blocking_conflicts:
        identity_conflict_types = {'source_identity_mismatch', 'stale_source'}
        if any(conflict['type'] in identity_conflict_types for conflict in blocking_conflicts):
            operator_message = SOURCE_IDENTITY_CHANGED_MESSAGE
        else:
            operator_message = 'This proposed move cannot be saved.'
    elif warning_conflicts:
        operator_message = 'This proposed move would save with warnings.'
    else:
        operator_message = 'This proposed move would be saveable.'

    return {
        'can_save': can_save,
        'blocking_conflicts': blocking_conflicts,
        'warning_conflicts': warning_conflicts,
        'informational_conflicts': informational_conflicts,
        'operator_message': operator_message,
    }


def normalize_sched_data_structure(sched_data):
    if sched_data is None:
        normalized = {}
    elif isinstance(sched_data, dict):
        normalized = deepcopy(sched_data)
    else:
        raise ValueError(
            "Move could not be saved because this schedule's operational data is not a JSON object. "
            f'Found {type(sched_data).__name__}. Existing schedule data was left unchanged.'
        )

    manual_moves = normalized.get('manual_moves')
    if manual_moves is None:
        manual_moves = []
    elif not isinstance(manual_moves, list):
        raise ValueError(
            "Move could not be saved because this schedule's manual move data is not a list. "
            f'Found {type(manual_moves).__name__}. Existing schedule data was left unchanged.'
        )

    normalized.setdefault('version', 1)
    normalized['manual_moves'] = deepcopy(manual_moves)
    return normalized


def diagnose_sched_data_structure(sched_data):
    if sched_data is None:
        return {
            'status': 'uninitialized',
            'recoverable': True,
            'value_type': 'null',
            'message': 'Schedule operational data is uninitialized and can be safely initialized.',
        }
    if not isinstance(sched_data, dict):
        return {
            'status': 'malformed',
            'recoverable': False,
            'value_type': type(sched_data).__name__,
            'message': (
                'Schedule operational data is malformed and requires administrator review. '
                f'Expected a JSON object but found {type(sched_data).__name__}.'
            ),
        }
    if 'manual_moves' in sched_data and not isinstance(sched_data['manual_moves'], list):
        return {
            'status': 'malformed',
            'recoverable': False,
            'value_type': type(sched_data['manual_moves']).__name__,
            'message': (
                'Schedule manual move data is malformed and requires administrator review. '
                f'Expected a list but found {type(sched_data["manual_moves"]).__name__}.'
            ),
        }
    if 'version' not in sched_data or 'manual_moves' not in sched_data:
        return {
            'status': 'incomplete',
            'recoverable': True,
            'value_type': 'dict',
            'message': 'Schedule operational data is valid and can be safely normalized.',
        }
    return {
        'status': 'valid',
        'recoverable': True,
        'value_type': 'dict',
        'message': 'Schedule operational data is valid.',
    }


def repair_sched_data_structure(schedule_obj):
    if not schedule_obj.pk:
        raise ValueError('Operational data repair requires a saved schedule record.')

    with transaction.atomic():
        locked_schedule = type(schedule_obj).objects.select_for_update().get(pk=schedule_obj.pk)
        normalized = normalize_sched_data_structure(locked_schedule.sched_data)
        locked_schedule.sched_data = normalized
        locked_schedule.save(update_fields=['sched_data'])

    schedule_obj.sched_data = deepcopy(normalized)
    return normalized


def persist_manual_move(schedule_obj, proposal_result):
    if not proposal_result or not proposal_result.get('source_identity_verified'):
        raise ValueError('Manual moves require a verified source identity.')
    if 'conflicts' not in proposal_result or not isinstance(proposal_result['conflicts'], list):
        raise ValueError('Manual moves require a validated proposal result.')

    save_readiness = evaluate_move_proposal_for_save(proposal_result)
    if not save_readiness['can_save']:
        raise ValueError('Manual moves require a saveable proposal result.')
    if proposal_result.get('move_type') != 'single_block':
        raise ValueError('Only single-block manual moves can be persisted.')

    missing_fields = [
        field
        for field in MANUAL_MOVE_REQUIRED_FIELDS
        if proposal_result.get(field) is None or proposal_result.get(field) == ''
    ]
    if missing_fields:
        raise ValueError(
            f'Manual move proposal is missing required fields: {", ".join(missing_fields)}.'
        )
    if not schedule_obj.pk:
        raise ValueError('Manual moves require a saved schedule record.')

    move_record = {
        field: proposal_result[field]
        for field in MANUAL_MOVE_REQUIRED_FIELDS
    }
    move_record.update({
        'move_type': 'single_block',
        'created_at': timezone.now().isoformat().replace('+00:00', 'Z'),
        'status': 'active',
    })

    with transaction.atomic():
        locked_schedule = type(schedule_obj).objects.select_for_update().get(pk=schedule_obj.pk)
        sched_data = normalize_sched_data_structure(locked_schedule.sched_data)
        sched_data['manual_moves'].append(move_record)
        locked_schedule.sched_data = sched_data
        locked_schedule.save(update_fields=['sched_data'])

    schedule_obj.sched_data = deepcopy(sched_data)
    return move_record


def validate_schedule_blocks(blocks):
    activity_blocks = [
        block
        for block in iter_schedule_blocks(blocks)
        if block['is_activity']
    ]
    for block in iter_schedule_blocks(blocks):
        block['conflicts'] = []

    conflict_summaries = []

    def attach_conflict(conflict_type, message, involved_blocks, severity='error'):
        related_block_ids = [block['block_id'] for block in involved_blocks]
        conflict = {
            'type': conflict_type,
            'severity': severity,
            'message': message,
            'related_block_ids': related_block_ids,
        }
        for block in involved_blocks:
            block['conflicts'].append(conflict)
        conflict_summaries.append(conflict)

    for block in activity_blocks:
        is_night_slot = 'night' in block['slot_key']
        activity_length = block.get('activity_length')
        if activity_length == 0 and not is_night_slot:
            attach_conflict(
                'invalid_time_slot',
                f'{block["display_value"]} is a night activity placed in a daytime slot.',
                [block],
            )
        elif activity_length and is_night_slot:
            attach_conflict(
                'invalid_time_slot',
                f'{block["display_value"]} is a daytime activity placed in a night slot.',
                [block],
            )

    multi_block_occurrences = {}
    for block in activity_blocks:
        if block.get('activity_length') == 2:
            multi_block_occurrences.setdefault(block['occurrence_id'], []).append(block)

    for occurrence_blocks in multi_block_occurrences.values():
        positions = {block.get('occurrence_position') for block in occurrence_blocks}
        first_slots = [block['slot_key'] for block in occurrence_blocks if block['slot_key'].endswith('1')]
        expected_slot_keys = {first_slots[0], f'{first_slots[0][:-1]}2'} if len(first_slots) == 1 else set()
        actual_slot_keys = {block['slot_key'] for block in occurrence_blocks}
        metadata_matches = all(
            block.get('is_multi_block')
            and block.get('occurrence_length') == 2
            for block in occurrence_blocks
        )
        occurrence_is_valid = (
            len(occurrence_blocks) == 2
            and len({block['group_index'] for block in occurrence_blocks}) == 1
            and len({block['raw_value'] for block in occurrence_blocks}) == 1
            and positions == {1, 2}
            and actual_slot_keys == expected_slot_keys
            and metadata_matches
        )
        if not occurrence_is_valid:
            attach_conflict(
                'broken_multi_block',
                f'{occurrence_blocks[0]["display_value"]} does not have a valid adjacent two-block occurrence.',
                occurrence_blocks,
            )

    blocks_by_group_slot = {}
    for block in activity_blocks:
        key = (block['group_index'], block['slot_key'])
        blocks_by_group_slot.setdefault(key, []).append(block)

    for duplicate_blocks in blocks_by_group_slot.values():
        if len(duplicate_blocks) > 1:
            activities = ', '.join(block['display_value'] for block in duplicate_blocks)
            first_block = duplicate_blocks[0]
            attach_conflict(
                'duplicate_group_slot',
                (
                    f'Multiple activities ({activities}) occupy '
                    f'{first_block["group_label"]} at {first_block["slot_label"]} '
                    f'({first_block["slot_key"]}).'
                ),
                duplicate_blocks,
                severity='warning',
            )

    return conflict_summaries
