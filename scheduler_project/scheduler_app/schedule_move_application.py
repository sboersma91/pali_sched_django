"""Pure application helper for validated manual schedule moves."""

from copy import deepcopy

from .schedule_move_validation import validate_schedule_move


def apply_schedule_move(
    schedule_payload,
    source_block_key,
    source_row_index,
    destination_block_key,
    destination_row_index,
):
    """Return a moved schedule copy when valid, or the original payload when invalid."""
    validation = validate_schedule_move(
        schedule_payload,
        source_block_key,
        source_row_index,
        destination_block_key,
        destination_row_index,
    )
    if not validation['valid']:
        return {
            'applied': False,
            'schedule': schedule_payload,
            'errors': validation['errors'],
            'warnings': validation['warnings'],
        }

    updated_schedule = deepcopy(schedule_payload)
    assignment_cells = {}
    for source in validation['source_cells']:
        cell = updated_schedule[source['block_key']][source['row_index']]
        assignment_cells[source['assignment_part']] = deepcopy(cell)
        updated_schedule[source['block_key']][source['row_index']] = 'empty'

    for assignment_part, destination in enumerate(validation['destination_cells'], start=1):
        moved_cell = assignment_cells[assignment_part]
        moved_cell['assignment_part'] = assignment_part
        moved_cell['source'] = 'manual'
        updated_schedule[destination['block_key']][destination['row_index']] = moved_cell

    return {
        'applied': True,
        'schedule': updated_schedule,
        'errors': [],
        'warnings': validation['warnings'],
    }
