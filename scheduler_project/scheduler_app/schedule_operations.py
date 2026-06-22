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
OVERLAP_MOVE_ACTION = 'overlap_move'
DISPLACEMENT_MOVE_ACTION = 'displacement_move'
SUPPORTED_MOVE_ACTIONS = {OVERLAP_MOVE_ACTION, DISPLACEMENT_MOVE_ACTION}
DEFAULT_NEW_MOVE_ACTION = DISPLACEMENT_MOVE_ACTION
GRID_SOURCE_KIND = 'grid'
HOLDING_SOURCE_KIND = 'holding'
SUPPORTED_SOURCE_KINDS = {GRID_SOURCE_KIND, HOLDING_SOURCE_KIND}
MOVE_CONFLICT_SEVERITY = {
    'duplicate_group_slot': 'warning',
    'broken_multi_block': 'error',
    'invalid_time_slot': 'warning',
    'persisted_override_replay': 'warning',
}
SUPPORTED_MOVE_TYPES = {'single_block', 'occurrence'}
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
HOLDING_MOVE_REQUIRED_FIELDS = (
    'source_holding_id',
    'source_activity_id',
    'source_activity_name',
    'source_occurrence_id',
    'target_group_index',
    'target_slot_key',
)
MANUAL_MOVE_OPTIONAL_LOCATION_FIELDS = (
    'source_location_id',
    'source_location_name',
    'target_location_id',
    'target_location_name',
)
LEGACY_SCHED_DATA_OPERATOR_MESSAGE = (
    'This schedule contains legacy operational data that must be repaired '
    'before schedule edits can be saved.'
)


class MalformedSchedDataError(ValueError):
    def __init__(self, debug_detail):
        self.operator_message = LEGACY_SCHED_DATA_OPERATOR_MESSAGE
        self.debug_detail = debug_detail
        super().__init__(f'{self.operator_message} Diagnostic: {debug_detail}')


def build_schedule_blocks(schedule, organization=None):
    activity_names = {
        value
        for day in SCHEDULE_DAYS
        for slot in day['slots']
        for value in schedule.get(slot['key'], [])
        if value not in SCHEDULE_DISPLAY_VALUES and value
    }
    activity_queryset = Course.objects.filter(course_name__in=activity_names)
    if organization is not None:
        activity_queryset = activity_queryset.filter(organization=organization)

    activities = {
        course_name: {
            'id': activity_id,
            'length': course_len,
            'abbreviation': (
                abbreviation
                if abbreviation and abbreviation != '5 character max'
                else None
            ),
        }
        for course_name, activity_id, course_len, abbreviation in activity_queryset.values_list(
            'course_name',
            'id',
            'course_len',
            'abriviation',
        )
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
                    'activity_abbreviation': activity.get('abbreviation'),
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
                    'is_persisted_override': False,
                    'override_status': None,
                    'override_source': None,
                    'replay_conflicts': [],
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


def get_block_grid_map(blocks):
    return {
        (block['group_index'], block['slot_key']): block
        for row in blocks
        for block in row['cells']
    }


def get_blocks_by_id(blocks):
    return {
        block['block_id']: block
        for block in iter_schedule_blocks(blocks)
    }


def get_occurrence_blocks(blocks, occurrence_id):
    if not occurrence_id:
        return []
    return [
        block
        for block in iter_schedule_blocks(blocks)
        if block.get('occurrence_id') == occurrence_id and block.get('is_activity')
    ]


def get_source_occurrence(blocks, source):
    if not source:
        return []
    occurrence_blocks = get_occurrence_blocks(blocks, source.get('occurrence_id'))
    return sorted(
        occurrence_blocks or [source],
        key=lambda block: block.get('occurrence_position') or 1,
    )


def expected_target_slot_keys(target_slot_key, occurrence_length):
    if occurrence_length == 1:
        return [target_slot_key]
    if occurrence_length == 2:
        if not target_slot_key or not target_slot_key.endswith('1'):
            return None
        return [target_slot_key, f'{target_slot_key[:-1]}2']
    return None


def get_target_footprint(blocks, target_group_index, target_slot_key, occurrence_length):
    slot_keys = expected_target_slot_keys(target_slot_key, occurrence_length)
    if not slot_keys:
        return None
    blocks_by_group_slot = get_block_grid_map(blocks)
    footprint = [
        blocks_by_group_slot.get((target_group_index, slot_key))
        for slot_key in slot_keys
    ]
    if any(block is None for block in footprint):
        return None
    return footprint


def occurrence_identity_matches(source_blocks, proposal):
    expected_activity_id = proposal.get('source_activity_id')
    expected_activity_name = proposal.get('source_activity_name')
    expected_occurrence_id = proposal.get('source_occurrence_id')
    expected_group_index = proposal.get('source_group_index')
    expected_slot_key = proposal.get('source_slot_key')

    if expected_activity_id is None or not expected_activity_name:
        return False
    if not source_blocks:
        return False

    first = source_blocks[0]
    if expected_occurrence_id is not None and first.get('occurrence_id') != expected_occurrence_id:
        return False
    if expected_group_index is not None and first.get('group_index') != expected_group_index:
        return False
    if expected_slot_key is not None and expected_slot_key not in {block.get('slot_key') for block in source_blocks}:
        return False

    return all(
        block.get('is_activity')
        and not block.get('is_empty')
        and not block.get('is_unavailable')
        and block.get('activity_id') == expected_activity_id
        and block.get('raw_value') == expected_activity_name
        and block.get('display_value') == expected_activity_name
        and block.get('occurrence_id') == first.get('occurrence_id')
        and block.get('group_index') == first.get('group_index')
        for block in source_blocks
    )


def clear_block_activity(block, empty_metadata=None):
    block.update({
        'raw_value': 'empty',
        'display_value': SCHEDULE_DISPLAY_VALUES['empty'],
        'activity_id': None,
        'activity_length': None,
        'activity_abbreviation': None,
        'is_activity': False,
        'is_empty': True,
        'is_unavailable': False,
        'occurrence_id': None,
        'occurrence_length': None,
        'occurrence_position': None,
        'is_multi_block': False,
        'has_overlap': False,
        'overlapping_blocks': [],
        **(empty_metadata or {}),
    })


def remove_occurrence_from_operational_blocks(blocks, source_blocks, empty_metadata=None):
    removal_results = []
    for source in list(source_blocks):
        removal_results.append(remove_activity_from_operational_blocks(blocks, source, empty_metadata))
    return {
        'source_removed': all(result['source_removed'] for result in removal_results),
        'promoted_blocks': [result['promoted_block'] for result in removal_results if result['promoted_block']],
    }


def build_activity_values_from_source(source):
    if source.get('is_holding'):
        return {
            'raw_value': source['activity_name'],
            'display_value': source['display_value'],
            'activity_id': source['activity_id'],
            'activity_length': source['activity_length'],
            'activity_abbreviation': source.get('activity_abbreviation'),
        }
    return {
        'raw_value': source.get('raw_value'),
        'display_value': source.get('display_value'),
        'activity_id': source.get('activity_id'),
        'activity_length': source.get('activity_length'),
        'activity_abbreviation': source.get('activity_abbreviation'),
    }


def source_occurrence_snapshots(source_blocks, occurrence_length):
    snapshots = deepcopy(source_blocks)
    return sorted(
        snapshots,
        key=lambda block: block.get('occurrence_position') or 1,
    )[:occurrence_length]


def holding_occurrence_snapshots(source):
    occurrence_length = source.get('occurrence_length') or source.get('activity_length') or 1
    snapshots = []
    for position in range(1, occurrence_length + 1):
        snapshots.append({
            **deepcopy(source),
            'occurrence_length': occurrence_length,
            'occurrence_position': position,
            'is_multi_block': occurrence_length > 1,
            'is_holding': True,
        })
    return snapshots


def collect_displaced_occurrences(blocks, target_blocks, exclude_block_ids=None):
    exclude_block_ids = set(exclude_block_ids or [])
    occupants = []
    for target in target_blocks:
        occupants.extend([target, *target.get('overlapping_blocks', [])])

    grouped = {}
    for occupant in occupants:
        if not occupant.get('is_activity') or occupant.get('block_id') in exclude_block_ids:
            continue
        occurrence_id = (
            occupant.get('occurrence_id')
            if (occupant.get('occurrence_length') or 1) > 1
            else occupant.get('block_id')
        ) or occupant.get('block_id')
        if occurrence_id not in grouped:
            full_occurrence = get_occurrence_blocks(blocks, occurrence_id)
            if full_occurrence:
                grouped[occurrence_id] = [
                    deepcopy(block)
                    for block in full_occurrence
                    if block.get('block_id') not in exclude_block_ids
                ]
            else:
                grouped[occurrence_id] = [deepcopy(occupant)]

    displaced = []
    for occurrence_blocks in grouped.values():
        occurrence_blocks = sorted(
            occurrence_blocks,
            key=lambda block: block.get('occurrence_position') or 1,
        )
        displaced.append(occurrence_blocks)
    return displaced


def remove_displaced_occurrences_from_grid(blocks, displaced_occurrences, empty_metadata=None):
    for occurrence_blocks in displaced_occurrences:
        if not occurrence_blocks:
            continue
        live_blocks = get_occurrence_blocks(blocks, occurrence_blocks[0].get('occurrence_id'))
        if live_blocks:
            remove_occurrence_from_operational_blocks(blocks, live_blocks, empty_metadata)


def reset_target_footprint_for_displacement(target_blocks):
    for target in target_blocks:
        target['overlapping_blocks'] = []
        target['has_overlap'] = False


def place_occurrence_in_targets(
    target_blocks,
    source_snapshots,
    *,
    source_identifier,
    override_source=None,
    is_persisted=False,
    is_proposed=False,
    action_type=OVERLAP_MOVE_ACTION,
    override_index=None,
):
    occurrence_length = len(source_snapshots)
    if is_persisted and action_type == OVERLAP_MOVE_ACTION:
        occurrence_id = f'occurrence:overlap:persisted:{override_index}:{target_blocks[0]["block_id"]}'
    elif is_persisted:
        occurrence_id = f'occurrence:persisted:{override_index}:{target_blocks[0]["block_id"]}'
    elif action_type == OVERLAP_MOVE_ACTION and any(target['is_activity'] for target in target_blocks):
        occurrence_id = f'occurrence:overlap:{target_blocks[0]["block_id"]}:{source_identifier}'
    else:
        occurrence_id = f'occurrence:{target_blocks[0]["block_id"]}'
    placed_blocks = []
    target_was_occupied = any(target['is_activity'] for target in target_blocks)

    for position, (target, snapshot) in enumerate(zip(target_blocks, source_snapshots), start=1):
        activity_values = build_activity_values_from_source(snapshot)
        common_values = {
            **activity_values,
            'group_index': target['group_index'],
            'group_label': target['group_label'],
            'slot_key': target['slot_key'],
            'slot_label': target['slot_label'],
            'is_activity': True,
            'is_empty': False,
            'is_unavailable': False,
            'occurrence_id': occurrence_id,
            'occurrence_length': occurrence_length,
            'occurrence_position': position,
            'is_multi_block': occurrence_length > 1,
            'is_persisted_override': is_persisted,
            'override_status': 'applied' if is_persisted else None,
            'override_source': override_source,
            'replay_conflicts': [],
            'conflicts': [],
        }
        if action_type == OVERLAP_MOVE_ACTION and target['is_activity']:
            placed = {
                **snapshot,
                **common_values,
                'block_id': (
                    f'overlap:persisted:{override_index}:{target["block_id"]}:{source_identifier}'
                    if is_persisted
                    else f'overlap:{target["block_id"]}:{source_identifier}'
                ),
                'is_proposed_source': False,
                'is_proposed_target': is_proposed,
                'proposed_from_block_id': source_identifier,
                'proposed_to_block_id': None,
                'has_overlap': False,
                'overlapping_blocks': [],
            }
            target['overlapping_blocks'].append(placed)
            target['has_overlap'] = True
        else:
            target.update({
                **common_values,
                'is_proposed_source': False,
                'is_proposed_target': is_proposed,
                'proposed_from_block_id': source_identifier,
                'proposed_to_block_id': None,
                'has_overlap': False,
                'overlapping_blocks': [],
            })
            placed = target
        placed_blocks.append(placed)

    return {
        'placed_blocks': placed_blocks,
        'target_was_occupied': target_was_occupied,
    }


def verify_move_proposal_source(blocks, proposal):
    all_blocks = list(iter_schedule_blocks(blocks))
    blocks_by_id = {block['block_id']: block for block in all_blocks}
    source_block_id = proposal.get('source_block_id')
    source = blocks_by_id.get(source_block_id)
    source_blocks = get_source_occurrence(blocks, source)
    result = {
        'verified': False,
        'error': None,
        'message': '',
        'source_block_id': source_block_id,
        'source': source,
        'source_blocks': source_blocks,
    }

    def reject(error, message):
        result.update({'error': error, 'message': message})
        return result

    if not source:
        return reject('invalid_source', 'The selected source block ID is invalid.')
    if not source['is_activity'] or source['is_empty'] or source['is_unavailable']:
        return reject('stale_source', SOURCE_IDENTITY_CHANGED_MESSAGE)
    if not occurrence_identity_matches(source_blocks, proposal):
        return reject('source_identity_mismatch', SOURCE_IDENTITY_CHANGED_MESSAGE)

    result['verified'] = True
    return result


def remove_activity_from_operational_blocks(blocks, source, empty_metadata=None):
    empty_metadata = empty_metadata or {}
    for row in blocks:
        for primary in row['cells']:
            if source is primary:
                if primary['overlapping_blocks']:
                    promoted = primary['overlapping_blocks'].pop(0)
                    remaining_overlaps = primary['overlapping_blocks']
                    primary.update({
                        **{
                            key: promoted.get(key)
                            for key in (
                                'raw_value',
                                'display_value',
                                'activity_id',
                                'activity_length',
                                'activity_abbreviation',
                                'is_activity',
                                'is_empty',
                                'is_unavailable',
                                'is_multi_block',
                                'is_persisted_override',
                                'override_status',
                                'override_source',
                                'replay_conflicts',
                            )
                        },
                        'occurrence_id': f'occurrence:{primary["block_id"]}',
                        'occurrence_length': 1,
                        'occurrence_position': 1,
                        'overlapping_blocks': remaining_overlaps,
                        'has_overlap': bool(remaining_overlaps),
                        'conflicts': [],
                    })
                    return {'source_removed': True, 'promoted_block': primary}

                primary.update({
                    'raw_value': 'empty',
                    'display_value': SCHEDULE_DISPLAY_VALUES['empty'],
                    'activity_id': None,
                    'activity_length': None,
                    'activity_abbreviation': None,
                    'is_activity': False,
                    'is_empty': True,
                    'is_unavailable': False,
                    'occurrence_id': None,
                    'occurrence_length': None,
                    'occurrence_position': None,
                    'is_multi_block': False,
                    'has_overlap': False,
                    'overlapping_blocks': [],
                    **empty_metadata,
                })
                return {'source_removed': True, 'promoted_block': None}

            for overlap in list(primary.get('overlapping_blocks', [])):
                if source is overlap:
                    primary['overlapping_blocks'].remove(overlap)
                    primary['has_overlap'] = bool(primary['overlapping_blocks'])
                    return {'source_removed': True, 'promoted_block': None}

    return {'source_removed': False, 'promoted_block': None}


def apply_move_proposal(blocks, proposal):
    valid_group_indices = {row['group_index'] for row in blocks}
    valid_slot_keys = {
        slot['key']
        for day in SCHEDULE_DAYS
        for slot in day['slots']
    }
    source_block_id = proposal.get('source_block_id')
    target_slot_key = proposal.get('target_slot_key')
    target_group_index = proposal.get('target_group_index')
    action_type = proposal.get('action_type') or DEFAULT_NEW_MOVE_ACTION
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
        'action_type': action_type,
        'source_kind': GRID_SOURCE_KIND,
        'source_identity_verified': source_verification['verified'],
    }

    def reject(error, message):
        result.update({'error': error, 'message': message})
        return result

    if not source_verification['verified']:
        return reject(source_verification['error'], source_verification['message'])
    if action_type not in SUPPORTED_MOVE_ACTIONS:
        return reject('unsupported_action_type', 'The requested operational move action is not supported.')
    source = source_verification['source']
    source_blocks = source_verification['source_blocks']
    occurrence_length = source.get('occurrence_length') or 1
    result.update({
        'source_activity_id': source['activity_id'],
        'source_activity_name': source['raw_value'],
        'source_occurrence_id': source['occurrence_id'],
        'source_group_index': source['group_index'],
        'source_slot_key': source['slot_key'],
        'source_block_ids': [block['block_id'] for block in source_blocks],
        'occurrence_length': occurrence_length,
        'move_type': 'occurrence' if occurrence_length > 1 else 'single_block',
    })
    if not isinstance(target_group_index, int):
        return reject('invalid_target_group', 'The target activity group is invalid.')
    if target_group_index not in valid_group_indices:
        return reject('invalid_target_group', 'The target activity group is invalid or stale.')
    if target_slot_key not in valid_slot_keys:
        return reject('invalid_target_slot', 'The target time slot is invalid.')

    target_blocks = get_target_footprint(blocks, target_group_index, target_slot_key, occurrence_length)
    if not target_blocks:
        return reject('invalid_target_footprint', 'The target does not have a valid footprint for this activity occurrence.')
    target = target_blocks[0]
    result['target_block_id'] = target['block_id']
    result['target_block_ids'] = [block['block_id'] for block in target_blocks]
    if {block['block_id'] for block in target_blocks} == {block['block_id'] for block in source_blocks}:
        return reject('same_source_and_target', 'The target is the current activity block.')
    if any(block['is_unavailable'] for block in target_blocks):
        return reject('target_unavailable', 'The target block is unavailable for this activity group.')
    if any(not block['is_empty'] and not block['is_activity'] for block in target_blocks):
        return reject('target_not_available', 'The target block is not available for a move proposal.')

    source_snapshots = source_occurrence_snapshots(source_blocks, occurrence_length)
    target_activities = [
        block['display_value']
        for block in target_blocks
        if block['is_activity']
    ]
    target_was_occupied = bool(target_activities)
    displaced_occurrences = (
        collect_displaced_occurrences(
            blocks,
            target_blocks,
            exclude_block_ids=[block['block_id'] for block in source_blocks],
        )
        if target_was_occupied and action_type == DISPLACEMENT_MOVE_ACTION
        else []
    )
    source_removal = remove_occurrence_from_operational_blocks(blocks, source_blocks, {
        'is_proposed_source': True,
        'is_proposed_target': False,
        'proposed_from_block_id': None,
        'proposed_to_block_id': target['block_id'],
    })
    if not source_removal['source_removed']:
        return reject('stale_source', SOURCE_IDENTITY_CHANGED_MESSAGE)
    if displaced_occurrences:
        remove_displaced_occurrences_from_grid(blocks, displaced_occurrences, {
            'is_proposed_source': True,
            'is_proposed_target': False,
        })
    placement = place_occurrence_in_targets(
        target_blocks,
        source_snapshots,
        source_identifier=source['block_id'],
        is_proposed=True,
        action_type=action_type,
    )
    proposed_target = placement['placed_blocks'][0]
    result.update({
        'target_block_id': proposed_target['block_id'],
        'target_was_occupied': target_was_occupied,
        'target_activity': ', '.join(target_activities) if target_was_occupied else None,
        'proposal_holding_area': [
            build_holding_area_item(occurrence_blocks, 'proposal', position)
            for position, occurrence_blocks in enumerate(displaced_occurrences, start=1)
        ],
    })
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


def apply_holding_reassignment_proposal(blocks, holding_area, proposal):
    valid_group_indices = {row['group_index'] for row in blocks}
    valid_slot_keys = {
        slot['key']
        for day in SCHEDULE_DAYS
        for slot in day['slots']
    }
    source_holding_id = proposal.get('source_holding_id')
    target_slot_key = proposal.get('target_slot_key')
    target_group_index = proposal.get('target_group_index')
    action_type = proposal.get('action_type') or DEFAULT_NEW_MOVE_ACTION
    source = next(
        (item for item in holding_area if item.get('holding_id') == source_holding_id),
        None,
    )
    result = {
        'applied': False,
        'error': None,
        'message': '',
        'source_kind': HOLDING_SOURCE_KIND,
        'source_block_id': None,
        'source_holding_id': source_holding_id,
        'source_activity_id': proposal.get('source_activity_id'),
        'source_activity_name': proposal.get('source_activity_name'),
        'source_occurrence_id': proposal.get('source_occurrence_id'),
        'source_group_index': None,
        'source_slot_key': None,
        'target_block_id': None,
        'target_slot_key': target_slot_key,
        'target_group_index': target_group_index,
        'move_type': 'single_block',
        'action_type': action_type,
        'source_identity_verified': False,
    }

    def reject(error, message):
        result.update({'error': error, 'message': message})
        return result

    if action_type not in SUPPORTED_MOVE_ACTIONS:
        return reject('unsupported_action_type', 'The requested operational move action is not supported.')
    if not source:
        return reject('stale_holding_source', 'The selected holding-area item is no longer available.')
    if (
        proposal.get('source_activity_id') is None
        or source.get('activity_id') != proposal.get('source_activity_id')
        or source.get('activity_name') != proposal.get('source_activity_name')
        or source.get('occurrence_id') != proposal.get('source_occurrence_id')
    ):
        return reject('source_identity_mismatch', SOURCE_IDENTITY_CHANGED_MESSAGE)
    occurrence_length = source.get('occurrence_length') or source.get('activity_length') or 1
    if not isinstance(target_group_index, int):
        return reject('invalid_target_group', 'The target activity group is invalid.')
    if target_group_index not in valid_group_indices:
        return reject('invalid_target_group', 'The target activity group is invalid or stale.')
    if target_slot_key not in valid_slot_keys:
        return reject('invalid_target_slot', 'The target time slot is invalid.')

    target_blocks = get_target_footprint(blocks, target_group_index, target_slot_key, occurrence_length)
    if not target_blocks:
        return reject('invalid_target_footprint', 'The target does not have a valid footprint for this activity occurrence.')
    target = target_blocks[0]
    result['target_block_id'] = target['block_id']
    result['target_block_ids'] = [block['block_id'] for block in target_blocks]
    if any(block['is_unavailable'] for block in target_blocks):
        return reject('target_unavailable', 'The target block is unavailable for this activity group.')
    if any(not block['is_empty'] and not block['is_activity'] for block in target_blocks):
        return reject('target_not_available', 'The target block is not available for a move proposal.')

    result['source_identity_verified'] = True
    result.update({
        'source_activity_id': source['activity_id'],
        'source_activity_name': source['activity_name'],
        'source_occurrence_id': source['occurrence_id'],
        'source_block_ids': source.get('source_block_ids', []),
        'occurrence_length': occurrence_length,
        'move_type': 'occurrence' if occurrence_length > 1 else 'single_block',
    })
    source_snapshots = holding_occurrence_snapshots(source)
    target_activities = [
        block['display_value']
        for block in target_blocks
        if block['is_activity']
    ]
    displaced_occurrences = (
        collect_displaced_occurrences(blocks, target_blocks)
        if target_activities and action_type == DISPLACEMENT_MOVE_ACTION
        else []
    )
    holding_area.remove(source)
    if displaced_occurrences:
        remove_displaced_occurrences_from_grid(blocks, displaced_occurrences, {
            'is_proposed_source': True,
            'is_proposed_target': False,
        })
    placement = place_occurrence_in_targets(
        target_blocks,
        source_snapshots,
        source_identifier=source['holding_id'],
        is_proposed=True,
        action_type=action_type,
    )
    proposed_target = placement['placed_blocks'][0]
    result.update({
        'target_block_id': proposed_target['block_id'],
        'target_was_occupied': bool(target_activities),
        'target_activity': ', '.join(target_activities) if target_activities else None,
        'proposal_holding_area': [
            build_holding_area_item(occurrence_blocks, 'proposal', position)
            for position, occurrence_blocks in enumerate(displaced_occurrences, start=1)
        ],
    })

    result.update({
        'applied': True,
        'message': 'The holding-area reassignment proposal was applied in memory only.',
        'activity': proposed_target['display_value'],
        'source_group_label': 'Holding Area',
        'source_slot_label': 'Non-grid holding',
        'target_group_label': target['group_label'],
        'target_slot_label': target['slot_label'],
    })
    return result


def build_holding_area_item(block_or_blocks, override_index, displacement_position):
    occurrence_blocks = block_or_blocks if isinstance(block_or_blocks, list) else [block_or_blocks]
    occurrence_blocks = sorted(
        occurrence_blocks,
        key=lambda block: block.get('occurrence_position') or 1,
    )
    block = occurrence_blocks[0]
    occurrence_length = block.get('occurrence_length') or len(occurrence_blocks)
    origin_group_index = block.get('group_index')
    group_accent_number = ((origin_group_index or 0) % 4) + 1
    return {
        'holding_id': (
            f'holding:override:{override_index}:{block["group_index"]}:'
            f'{block["slot_key"]}:{displacement_position}'
        ),
        'activity_id': block.get('activity_id'),
        'activity_name': block.get('raw_value'),
        'display_value': block.get('display_value'),
        'activity_length': block.get('activity_length'),
        'activity_abbreviation': block.get('activity_abbreviation'),
        'occurrence_id': block.get('occurrence_id'),
        'occurrence_length': occurrence_length,
        'source_block_ids': [occurrence_block.get('block_id') for occurrence_block in occurrence_blocks],
        'source_slot_keys': [occurrence_block.get('slot_key') for occurrence_block in occurrence_blocks],
        'origin_block_id': block.get('block_id'),
        'origin_group_index': origin_group_index,
        'origin_group_label': block.get('group_label'),
        'origin_slot_key': block.get('slot_key'),
        'origin_slot_label': block.get('slot_label'),
        'group_accent_class': f'schedule-row-accent-{group_accent_number}',
        'displaced_by_override_index': override_index,
        'holding_status': 'awaiting_assignment',
        'is_holding': True,
    }


def apply_persisted_overrides(schedule_obj, blocks, replay_mode='overlap'):
    if replay_mode not in {'overlap', 'displacement'}:
        raise ValueError(f'Unsupported persisted override replay mode: {replay_mode}.')

    replay_result = {
        'replay_mode': replay_mode,
        'applied_overrides': [],
        'ignored_overrides': [],
        'replay_conflicts': [],
        'holding_area': [],
    }

    def add_replay_conflict(
        message,
        override_index=None,
        involved_blocks=None,
        severity='warning',
        override_status='failed_replay',
    ):
        involved_blocks = involved_blocks or []
        conflict = {
            'type': 'persisted_override_replay',
            'severity': severity,
            'message': message,
            'related_block_ids': [block['block_id'] for block in involved_blocks],
            'override_index': override_index,
            'override_status': override_status,
        }
        for block in involved_blocks:
            block['replay_conflicts'].append(conflict)
            if not block.get('override_status'):
                block['override_status'] = override_status
        replay_result['replay_conflicts'].append(conflict)
        replay_result['ignored_overrides'].append({
            'override_index': override_index,
            'reason': override_status,
        })
        return conflict

    sched_data = schedule_obj.sched_data
    if sched_data is None or sched_data == {}:
        return replay_result
    if not isinstance(sched_data, dict):
        add_replay_conflict(
            'Saved schedule overrides could not be replayed because the legacy operational data is malformed.'
        )
        return replay_result

    manual_moves = sched_data.get('manual_moves', [])
    if not isinstance(manual_moves, list):
        add_replay_conflict(
            'Saved schedule overrides could not be replayed because the manual move data is malformed.'
        )
        return replay_result

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

    for override_index, override in enumerate(manual_moves):
        if not isinstance(override, dict):
            add_replay_conflict(
                f'Saved override {override_index + 1} is invalid and was not replayed.',
                override_index=override_index,
            )
            continue
        if override.get('status') != 'active':
            replay_result['ignored_overrides'].append({
                'override_index': override_index,
                'reason': override.get('status') or 'invalid_status',
            })
            continue
        if override.get('move_type') not in SUPPORTED_MOVE_TYPES:
            add_replay_conflict(
                f'Saved override {override_index + 1} uses an unsupported move type and was not replayed.',
                override_index=override_index,
            )
            continue
        action_type = override.get('action_type') or OVERLAP_MOVE_ACTION
        if action_type not in SUPPORTED_MOVE_ACTIONS:
            add_replay_conflict(
                (
                    f'Saved override {override_index + 1} uses unsupported action type '
                    f'"{action_type}" and was not replayed.'
                ),
                override_index=override_index,
            )
            continue
        source_kind = override.get('source_kind') or GRID_SOURCE_KIND
        if source_kind not in SUPPORTED_SOURCE_KINDS:
            add_replay_conflict(
                (
                    f'Saved override {override_index + 1} uses unsupported source kind '
                    f'"{source_kind}" and was not replayed.'
                ),
                override_index=override_index,
            )
            continue

        blocks_by_id = get_blocks_by_id(blocks)
        target_group_index = override.get('target_group_index')
        target_slot_key = override.get('target_slot_key')
        source = (
            blocks_by_id.get(override.get('source_block_id'))
            if source_kind == GRID_SOURCE_KIND
            else next(
                (
                    item
                    for item in replay_result['holding_area']
                    if item.get('holding_id') == override.get('source_holding_id')
                ),
                None,
            )
        )
        source_blocks = (
            get_source_occurrence(blocks, source)
            if source_kind == GRID_SOURCE_KIND
            else holding_occurrence_snapshots(source) if source else []
        )
        occurrence_length = (
            (source_blocks[0].get('occurrence_length') or 1)
            if source_blocks
            else override.get('occurrence_length') or 1
        )
        target_blocks = get_target_footprint(blocks, target_group_index, target_slot_key, occurrence_length)
        target = target_blocks[0] if target_blocks else None
        related_blocks = [
            block
            for block in [*source_blocks, *(target_blocks or [])]
            if block and block.get('block_id')
        ]

        required_fields = (
            MANUAL_MOVE_REQUIRED_FIELDS
            if source_kind == GRID_SOURCE_KIND
            else HOLDING_MOVE_REQUIRED_FIELDS
        )
        required_values_present = all(
            override.get(field) is not None and override.get(field) != ''
            for field in required_fields
        )
        if not required_values_present:
            add_replay_conflict(
                f'Saved override {override_index + 1} is incomplete and was not replayed.',
                override_index=override_index,
                involved_blocks=related_blocks,
            )
            continue
        if source_kind == GRID_SOURCE_KIND:
            if (
                not source
                or not occurrence_identity_matches(source_blocks, {
                    'source_activity_id': override.get('source_activity_id'),
                    'source_activity_name': override.get('source_activity_name'),
                    'source_occurrence_id': override.get('source_occurrence_id'),
                    'source_group_index': override.get('source_group_index'),
                    'source_slot_key': override.get('source_slot_key'),
                })
            ):
                add_replay_conflict(
                    (
                        f'Saved override {override_index + 1} is stale because its source activity '
                        'no longer matches the generated operational schedule.'
                    ),
                    override_index=override_index,
                    involved_blocks=related_blocks,
                    override_status='stale',
                )
                continue
        else:
            if (
                not source
                or source.get('activity_id') != override.get('source_activity_id')
                or source.get('activity_name') != override.get('source_activity_name')
                or source.get('occurrence_id') != override.get('source_occurrence_id')
            ):
                add_replay_conflict(
                    (
                        f'Saved override {override_index + 1} is stale because its holding-area '
                        'source is no longer available.'
                    ),
                    override_index=override_index,
                    involved_blocks=related_blocks,
                    override_status='stale',
                )
                continue
        if (
            not isinstance(target_group_index, int)
            or target_slot_key not in valid_slot_keys
            or not target_blocks
            or any(target_block.get('is_unavailable') for target_block in target_blocks)
            or any(not target_block.get('is_empty') and not target_block.get('is_activity') for target_block in target_blocks)
            or (
                source_kind == GRID_SOURCE_KIND
                and {target_block['block_id'] for target_block in target_blocks}
                == {source_block['block_id'] for source_block in source_blocks}
            )
        ):
            add_replay_conflict(
                f'Saved override {override_index + 1} has an invalid target and was not replayed.',
                override_index=override_index,
                involved_blocks=related_blocks,
            )
            continue

        override_source = deepcopy(override)
        source_snapshots = source_occurrence_snapshots(source_blocks, occurrence_length)
        target_was_occupied = any(target_block['is_activity'] for target_block in target_blocks)
        displaced_target_occurrences = []
        if target_was_occupied and action_type == DISPLACEMENT_MOVE_ACTION:
            displaced_target_occurrences = collect_displaced_occurrences(
                blocks,
                target_blocks,
                exclude_block_ids=[block.get('block_id') for block in source_blocks],
            )
        if source_kind == GRID_SOURCE_KIND:
            source_removal = remove_occurrence_from_operational_blocks(blocks, source_blocks, {
                'is_persisted_override': True,
                'override_status': 'applied_source',
                'override_source': override_source,
            })
            if not source_removal['source_removed']:
                add_replay_conflict(
                    f'Saved override {override_index + 1} could not safely remove its source activity.',
                    override_index=override_index,
                    involved_blocks=related_blocks,
                )
                continue
        else:
            replay_result['holding_area'].remove(source)

        displaced_holding_ids = []
        source_identifier = (
            source.get('block_id')
            if source_kind == GRID_SOURCE_KIND
            else source.get('holding_id')
        )
        if displaced_target_occurrences:
            remove_displaced_occurrences_from_grid(blocks, displaced_target_occurrences, {
                'is_persisted_override': True,
                'override_status': 'displaced',
                'override_source': override_source,
            })
            holding_items = [
                build_holding_area_item(occurrence_blocks, override_index, position)
                for position, occurrence_blocks in enumerate(displaced_target_occurrences, start=1)
            ]
            replay_result['holding_area'].extend(holding_items)
            displaced_holding_ids = [item['holding_id'] for item in holding_items]
            reset_target_footprint_for_displacement(target_blocks)

        placement = place_occurrence_in_targets(
            target_blocks,
            source_snapshots,
            source_identifier=source_identifier,
            override_source=override_source,
            is_persisted=True,
            action_type=action_type,
            override_index=override_index,
        )
        persisted_target = placement['placed_blocks'][0]

        replay_result['applied_overrides'].append({
            'override_index': override_index,
            'override_status': 'active',
            'action_type': action_type,
            'source_kind': source_kind,
            'source_block_id': source.get('block_id'),
            'source_holding_id': source.get('holding_id'),
            'moved_activity_id': source_snapshots[0].get('activity_id'),
            'moved_activity_name': (
                source_snapshots[0].get('raw_value')
                or source_snapshots[0].get('activity_name')
            ),
            'target_block_id': persisted_target['block_id'],
            'target_block_ids': [block['block_id'] for block in placement['placed_blocks']],
            'target_was_occupied': target_was_occupied,
            'displaced_holding_ids': displaced_holding_ids,
            'displaced_activity_ids': [
                occurrence_blocks[0].get('activity_id')
                for occurrence_blocks in displaced_target_occurrences
            ],
        })

    return replay_result


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
        raise MalformedSchedDataError(
            f'Expected sched_data to be a JSON object; found {type(sched_data).__name__}. '
            'Existing schedule data was left unchanged.'
        )

    manual_moves = normalized.get('manual_moves')
    if manual_moves is None:
        manual_moves = []
    elif not isinstance(manual_moves, list):
        raise MalformedSchedDataError(
            f'Expected sched_data.manual_moves to be a list; found {type(manual_moves).__name__}. '
            'Existing schedule data was left unchanged.'
        )

    normalized.setdefault('version', 1)
    normalized['manual_moves'] = deepcopy(manual_moves)
    return normalized


def diagnose_sched_data_structure(sched_data):
    if sched_data is None:
        return {
            'status': 'uninitialized',
            'recoverable': True,
            'repairable': True,
            'value_type': 'null',
            'message': 'Schedule operational data is uninitialized and can be safely initialized.',
            'debug_detail': 'sched_data is null.',
        }
    if not isinstance(sched_data, dict):
        is_blank = isinstance(sched_data, str) and not sched_data.strip()
        return {
            'status': 'malformed',
            'recoverable': is_blank,
            'repairable': is_blank,
            'value_type': type(sched_data).__name__,
            'message': LEGACY_SCHED_DATA_OPERATOR_MESSAGE,
            'debug_detail': (
                f'Expected sched_data to be a JSON object; found '
                f'{type(sched_data).__name__}{" containing only whitespace" if is_blank else ""}.'
            ),
        }
    if 'manual_moves' in sched_data and not isinstance(sched_data['manual_moves'], list):
        return {
            'status': 'malformed',
            'recoverable': False,
            'repairable': False,
            'value_type': type(sched_data['manual_moves']).__name__,
            'message': LEGACY_SCHED_DATA_OPERATOR_MESSAGE,
            'debug_detail': (
                f'Expected sched_data.manual_moves to be a list; found '
                f'{type(sched_data["manual_moves"]).__name__}.'
            ),
        }
    if 'version' not in sched_data or 'manual_moves' not in sched_data:
        return {
            'status': 'incomplete',
            'recoverable': True,
            'repairable': True,
            'value_type': 'dict',
            'message': 'Schedule operational data is valid and can be safely normalized.',
            'debug_detail': 'sched_data is a JSON object missing version and/or manual_moves.',
        }
    return {
        'status': 'valid',
        'recoverable': True,
        'repairable': False,
        'value_type': 'dict',
        'message': 'Schedule operational data is valid.',
        'debug_detail': 'sched_data has the expected top-level structure.',
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


def repair_malformed_sched_data(schedule_obj):
    if not schedule_obj.pk:
        raise ValueError('Operational data repair requires a saved schedule record.')

    with transaction.atomic():
        locked_schedule = type(schedule_obj).objects.select_for_update().get(pk=schedule_obj.pk)
        sched_data = locked_schedule.sched_data
        if sched_data is None or (isinstance(sched_data, str) and not sched_data.strip()):
            repaired = {'version': 1, 'manual_moves': []}
        else:
            diagnosis = diagnose_sched_data_structure(sched_data)
            raise MalformedSchedDataError(
                f'{diagnosis["debug_detail"]} Automatic repair was refused because the value is populated.'
            )
        locked_schedule.sched_data = repaired
        locked_schedule.save(update_fields=['sched_data'])

    schedule_obj.sched_data = deepcopy(repaired)
    return repaired


def persist_manual_move(schedule_obj, proposal_result):
    if not proposal_result or not proposal_result.get('source_identity_verified'):
        raise ValueError('Manual moves require a verified source identity.')
    if 'conflicts' not in proposal_result or not isinstance(proposal_result['conflicts'], list):
        raise ValueError('Manual moves require a validated proposal result.')

    save_readiness = evaluate_move_proposal_for_save(proposal_result)
    if not save_readiness['can_save']:
        raise ValueError('Manual moves require a saveable proposal result.')
    if proposal_result.get('move_type') not in SUPPORTED_MOVE_TYPES:
        raise ValueError('Only supported manual move types can be persisted.')
    action_type = proposal_result.get('action_type') or DEFAULT_NEW_MOVE_ACTION
    if action_type not in SUPPORTED_MOVE_ACTIONS:
        raise ValueError('Only supported operational move actions can be persisted.')

    source_kind = proposal_result.get('source_kind') or GRID_SOURCE_KIND
    if source_kind not in SUPPORTED_SOURCE_KINDS:
        raise ValueError('Only supported operational move sources can be persisted.')
    required_fields = (
        MANUAL_MOVE_REQUIRED_FIELDS
        if source_kind == GRID_SOURCE_KIND
        else HOLDING_MOVE_REQUIRED_FIELDS
    )
    missing_fields = [
        field
        for field in required_fields
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
        for field in required_fields
    }
    move_record.update({
        'source_kind': source_kind,
        'move_type': proposal_result.get('move_type'),
        'action_type': action_type,
        'occurrence_length': proposal_result.get('occurrence_length', 1),
        'source_block_ids': proposal_result.get('source_block_ids', []),
        'target_block_ids': proposal_result.get('target_block_ids', []),
        'created_at': timezone.now().isoformat().replace('+00:00', 'Z'),
        'status': 'active',
    })
    for field in MANUAL_MOVE_OPTIONAL_LOCATION_FIELDS:
        if proposal_result.get(field) not in (None, ''):
            move_record[field] = proposal_result[field]

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

    seen_replay_conflicts = set()
    for block in iter_schedule_blocks(blocks):
        for conflict in block.get('replay_conflicts', []):
            block['conflicts'].append(conflict)
            conflict_key = (
                conflict.get('type'),
                conflict.get('override_index'),
                conflict.get('message'),
            )
            if conflict_key in seen_replay_conflicts:
                continue
            seen_replay_conflicts.add(conflict_key)
            conflict_summaries.append(conflict)

    return conflict_summaries
