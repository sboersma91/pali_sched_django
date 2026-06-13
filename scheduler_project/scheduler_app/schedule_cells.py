"""Helpers for structured schedule cells and legacy string compatibility."""


def generated_schedule_cell(activity_name, activity_id, location_name, location_id):
    return {
        'activity_name': activity_name,
        'activity_id': activity_id,
        'location_name': location_name,
        'location_id': location_id,
        'source': 'generated',
    }


def schedule_cell_activity_name(cell):
    if isinstance(cell, dict):
        return cell.get('activity_name', '')
    return cell


def schedule_cell_location_name(cell):
    if isinstance(cell, dict):
        return cell.get('location_name', '')
    return ''
