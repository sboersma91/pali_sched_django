"""Pure validation helpers for proposed manual schedule moves."""

from .schedule_blocks import SCHEDULE_BLOCK_KEYS, SCHEDULE_BLOCKS


BLOCK_KINDS = {block['key']: block['kind'] for block in SCHEDULE_BLOCKS}


def _result(errors, assignment_id=None, source_cells=None, destination_cells=None):
    return {
        'valid': not errors,
        'errors': errors,
        'warnings': [],
        'assignment_id': assignment_id,
        'source_cells': source_cells or [],
        'destination_cells': destination_cells or [],
    }


def _cell_at(schedule_payload, block_key, row_index):
    values = schedule_payload.get(block_key)
    if not isinstance(values, list) or not isinstance(row_index, int) or row_index < 0 or row_index >= len(values):
        return None, False
    return values[row_index], True


def _destination_blocks(destination_block_key, assignment_span):
    if destination_block_key not in SCHEDULE_BLOCK_KEYS:
        return []
    if assignment_span == 1:
        return [destination_block_key]
    if assignment_span == 2 and destination_block_key.endswith('1'):
        second_block = f'{destination_block_key[:-1]}2'
        if second_block in SCHEDULE_BLOCK_KEYS:
            return [destination_block_key, second_block]
    return []


def validate_schedule_move(
    schedule_payload,
    source_block_key,
    source_row_index,
    destination_block_key,
    destination_row_index,
):
    """Validate a proposed move without changing the schedule payload.

    The destination block is treated as the destination for assignment part 1.
    Linked assignment parts are derived from the source cell's assignment metadata.
    """
    errors = []
    if not isinstance(schedule_payload, dict):
        return _result(['Schedule payload must be a dictionary.'])

    source_cell, source_exists = _cell_at(schedule_payload, source_block_key, source_row_index)
    if not source_exists:
        return _result(['Source cell does not exist.'])
    if not isinstance(source_cell, dict):
        if source_cell in {'empty', 'g_box'}:
            return _result(['Placeholder cells cannot be moved as assignments.'])
        return _result(['Legacy string assignment cells cannot be moved.'])

    assignment_id = source_cell.get('assignment_id')
    assignment_span = source_cell.get('assignment_span')
    if not assignment_id:
        errors.append('Source assignment is missing assignment_id.')
    if not isinstance(assignment_span, int) or assignment_span < 1:
        errors.append('Source assignment has an invalid assignment_span.')
    if errors:
        return _result(errors, assignment_id=assignment_id)

    linked_cells = []
    for block_key in SCHEDULE_BLOCK_KEYS:
        cell, exists = _cell_at(schedule_payload, block_key, source_row_index)
        if exists and isinstance(cell, dict) and cell.get('assignment_id') == assignment_id:
            linked_cells.append({'block_key': block_key, 'row_index': source_row_index, 'cell': cell})

    expected_parts = set(range(1, assignment_span + 1))
    linked_parts = {linked['cell'].get('assignment_part') for linked in linked_cells}
    if len(linked_cells) != assignment_span or linked_parts != expected_parts:
        errors.append('All linked assignment parts must move together and have valid assignment parts.')
    if any(linked['cell'].get('assignment_span') != assignment_span for linked in linked_cells):
        errors.append('Linked assignment cells must have the same assignment_span.')

    destination_blocks = _destination_blocks(destination_block_key, assignment_span)
    if not destination_blocks:
        errors.append('Destination blocks do not support this assignment span.')

    destination_cells = []
    for block_key in destination_blocks:
        cell, exists = _cell_at(schedule_payload, block_key, destination_row_index)
        destination_cells.append({'block_key': block_key, 'row_index': destination_row_index})
        if not exists:
            errors.append(f'Destination cell {block_key} does not exist.')
        elif cell == 'g_box':
            errors.append(f'Destination cell {block_key} is unavailable for this group.')
        elif cell != 'empty':
            errors.append(f'Destination cell {block_key} is not empty.')

    source_kinds = {BLOCK_KINDS[linked['block_key']] for linked in linked_cells}
    destination_kinds = {BLOCK_KINDS[block_key] for block_key in destination_blocks}
    if len(source_kinds) == 1 and destination_kinds and destination_kinds != source_kinds:
        source_kind = next(iter(source_kinds))
        errors.append(f'{source_kind.capitalize()} assignments must remain in {source_kind} blocks.')

    source_cells = [
        {
            'block_key': linked['block_key'],
            'row_index': linked['row_index'],
            'assignment_part': linked['cell'].get('assignment_part'),
        }
        for linked in sorted(linked_cells, key=lambda linked: linked['cell'].get('assignment_part', 0))
    ]
    return _result(errors, assignment_id, source_cells, destination_cells)
