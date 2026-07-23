"""Shared presentation semantics for schedule-local activity-group colors."""


GROUP_ACCENT_CLASSES = (
    'schedule-row-accent-1',
    'schedule-row-accent-2',
    'schedule-row-accent-3',
    'schedule-row-accent-4',
)
DEFAULT_GROUP_ACCENT_CLASS = 'schedule-row-accent-default'


def group_accent_class(group_index):
    """Return the deterministic visual accent for a schedule-local group index."""
    if isinstance(group_index, bool) or not isinstance(group_index, int):
        return DEFAULT_GROUP_ACCENT_CLASS
    if group_index < 0:
        return DEFAULT_GROUP_ACCENT_CLASS
    return GROUP_ACCENT_CLASSES[group_index % len(GROUP_ACCENT_CLASSES)]
