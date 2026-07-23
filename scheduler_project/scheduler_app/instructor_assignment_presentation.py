"""Template-safe presentation data for read-only instructor assignments."""

from dataclasses import dataclass

from django.core.exceptions import ValidationError

from .group_colors import group_accent_class
from .schedule_blocks import (
    DAILY_OFF_ELIGIBLE_SLOT_KEYS_BY_DAY,
    SCHEDULE_DAYS,
    SCHEDULE_SLOT_KEYS,
)


ASSIGNMENT_CELL_ASSIGNED = 'assigned'
ASSIGNMENT_CELL_OFF = 'off'
ASSIGNMENT_CELL_ADMIN_TIME = 'admin_time'
ASSIGNMENT_CELL_UNAVAILABLE = 'unavailable'
ASSIGNMENT_CELL_STATES = frozenset({
    ASSIGNMENT_CELL_ASSIGNED,
    ASSIGNMENT_CELL_OFF,
    ASSIGNMENT_CELL_ADMIN_TIME,
    ASSIGNMENT_CELL_UNAVAILABLE,
})


@dataclass(frozen=True)
class AssignmentSlotHeader:
    key: str
    label: str


@dataclass(frozen=True)
class AssignmentDayHeader:
    name: str
    slots: tuple[AssignmentSlotHeader, ...]

    @property
    def column_count(self):
        return len(self.slots)


@dataclass(frozen=True)
class InstructorAssignmentCell:
    slot_key: str
    slot_label: str
    state: str
    activity_name: str | None
    group_index: int | None
    group_label: str | None
    group_accent_class: str | None
    occurrence_id: str | None
    is_multi_slot: bool
    footprint_position: int | None

    @property
    def is_empty(self):
        """Compatibility alias for callers asking whether activity is absent."""
        return self.state != ASSIGNMENT_CELL_ASSIGNED


@dataclass(frozen=True)
class InstructorAssignmentRow:
    instructor_id: int
    instructor_name: str
    cells: tuple[InstructorAssignmentCell, ...]


@dataclass(frozen=True)
class AssignmentRejectionDetail:
    instructor_name: str
    reason: str
    affected_slots: tuple[str, ...]
    conflicting_occurrence_id: str | None


@dataclass(frozen=True)
class UnstaffedOccurrenceSummary:
    occurrence_id: str | None
    activity_name: str
    group_label: str | None
    occupied_slots: tuple[str, ...]
    reason: str
    rejection_details: tuple[AssignmentRejectionDetail, ...]


@dataclass(frozen=True)
class InstructorAssignmentPresentation:
    schedule_id: int
    schedule_name: str
    day_headers: tuple[AssignmentDayHeader, ...]
    slot_headers: tuple[AssignmentSlotHeader, ...]
    instructor_rows: tuple[InstructorAssignmentRow, ...]
    unstaffed_occurrences: tuple[UnstaffedOccurrenceSummary, ...]


def _canonical_headers():
    day_headers = tuple(
        AssignmentDayHeader(
            name=day['name'],
            slots=tuple(
                AssignmentSlotHeader(key=slot['key'], label=slot['label'])
                for slot in day['slots']
            ),
        )
        for day in SCHEDULE_DAYS
    )
    return day_headers, tuple(
        slot for day in day_headers for slot in day.slots
    )


def _display_slot(slot_key, slot_labels):
    return slot_labels.get(slot_key, slot_key or 'Unknown slot')


def _adapt_rejection(rejection, slot_labels):
    reasons = rejection.get('reasons') or ()
    reason = reasons[0] if reasons else {}
    code = reason.get('code')
    details = reason.get('details') or {}

    affected_slot_keys = tuple(
        failure.get('slot_key')
        for failure in details.get('failed_slots', ())
        if failure.get('slot_key')
    ) or tuple(details.get('overlapping_slot_keys') or ()) \
        or tuple(details.get('affected_slot_keys') or ())
    if code == 'overlapping_assignment':
        display_reason = 'Already assigned during an overlapping schedule slot.'
    elif code == 'explicitly_unavailable':
        display_reason = 'Unavailable for one or more required schedule slots.'
    elif code == 'missing_availability':
        display_reason = 'Availability could not be resolved for a required slot.'
    elif code in {'duplicate_availability', 'invalid_availability_state'}:
        display_reason = 'Detailed availability data needs operator review.'
    elif code == 'availability_requirements_not_met':
        display_reason = 'Detailed availability requirements were not met.'
    elif code == 'daily_off_requirement':
        day_key = details.get('day_key') or 'required day'
        display_reason = (
            f'Assignment would consume the final eligible {day_key} OFF slot.'
        )
    else:
        display_reason = 'Not eligible for this occurrence.'

    instructor = rejection.get('instructor')
    return AssignmentRejectionDetail(
        instructor_name=str(instructor) if instructor is not None else 'Unknown instructor',
        reason=display_reason,
        affected_slots=tuple(
            _display_slot(slot_key, slot_labels) for slot_key in affected_slot_keys
        ),
        conflicting_occurrence_id=details.get('conflicting_occurrence_id'),
    )


def _adapt_planning_diagnostic(diagnostic):
    code = diagnostic.get('code')
    if code == 'global_planning_choice':
        reason = (
            'Left unstaffed by the deterministic maximum-coverage plan to '
            'preserve hard constraints.'
        )
    else:
        reason = 'The assignment planner reported an operational constraint.'
    return AssignmentRejectionDetail(
        instructor_name='Planning result',
        reason=reason,
        affected_slots=(),
        conflicting_occurrence_id=None,
    )


def build_instructor_assignment_presentation(assignment_result):
    """Convert one orchestration result into deterministic template-safe data."""
    day_headers, slot_headers = _canonical_headers()
    slot_labels = {
        slot.key: f'{day.name} {slot.label}'
        for day in day_headers
        for slot in day.slots
    }
    assignments = tuple(assignment_result.get('assignments') or ())

    assigned_cells = {}
    for assignment in assignments:
        instructor = assignment.get('assigned_instructor')
        if instructor is None or assignment.get('status') != 'assigned':
            continue
        occurrence = assignment.get('occurrence') or {}
        footprint = tuple(occurrence.get('slot_footprint') or ())
        for slot in footprint:
            slot_key = slot.get('slot_key')
            cell_key = (instructor.pk, slot_key)
            if cell_key in assigned_cells:
                raise ValidationError({
                    'assignment': 'Duplicate assigned instructor-slot state.'
                })
            assigned_cells[cell_key] = InstructorAssignmentCell(
                slot_key=slot_key,
                slot_label=slot_labels.get(slot_key, slot.get('slot_label') or slot_key),
                state=ASSIGNMENT_CELL_ASSIGNED,
                activity_name=occurrence.get('activity_display_name'),
                group_index=occurrence.get('group_index'),
                group_label=occurrence.get('group_label'),
                group_accent_class=group_accent_class(
                    occurrence.get('group_index')
                ),
                occurrence_id=occurrence.get('occurrence_id'),
                is_multi_slot=len(footprint) > 1,
                footprint_position=slot.get('position'),
            )

    candidate_instructor_ids = {
        instructor.pk
        for instructor in assignment_result.get('candidate_instructors') or ()
    }
    off_cells = set()
    eligible_off_slots_by_day = dict(DAILY_OFF_ELIGIBLE_SLOT_KEYS_BY_DAY)
    for reservation in assignment_result.get('off_reservations') or ():
        cell_key = (reservation.get('instructor_id'), reservation.get('slot_key'))
        day_key = reservation.get('day_key')
        if (
            cell_key[0] not in candidate_instructor_ids
            or cell_key[1] not in eligible_off_slots_by_day.get(day_key, ())
        ):
            raise ValidationError({'off_reservations': 'OFF reservation is out of scope.'})
        if cell_key in off_cells:
            raise ValidationError({'off_reservations': 'Duplicate OFF reservation.'})
        off_cells.add(cell_key)

    unavailable_cells = set()
    unavailable_by_instructor = (
        assignment_result.get('unavailable_slot_keys_by_instructor') or {}
    )
    for instructor_id, unavailable_slot_keys in unavailable_by_instructor.items():
        if instructor_id not in candidate_instructor_ids:
            raise ValidationError({
                'unavailable_slots': 'Unavailable instructor is out of scope.'
            })
        for slot_key in unavailable_slot_keys:
            cell_key = (instructor_id, slot_key)
            if slot_key not in SCHEDULE_SLOT_KEYS:
                raise ValidationError({
                    'unavailable_slots': 'Unavailable schedule slot is invalid.'
                })
            if cell_key in unavailable_cells:
                raise ValidationError({
                    'unavailable_slots': 'Duplicate unavailable instructor-slot state.'
                })
            unavailable_cells.add(cell_key)

    if assigned_cells.keys() & off_cells:
        raise ValidationError({'cell_state': 'Instructor slot is both assigned and OFF.'})
    if assigned_cells.keys() & unavailable_cells:
        raise ValidationError({
            'cell_state': 'Instructor slot is both assigned and unavailable.'
        })
    if off_cells & unavailable_cells:
        raise ValidationError({'cell_state': 'Instructor slot is both OFF and unavailable.'})

    def build_nonassigned_cell(instructor_id, slot):
        cell_key = (instructor_id, slot.key)
        if cell_key in off_cells:
            state = ASSIGNMENT_CELL_OFF
        elif cell_key in unavailable_cells:
            state = ASSIGNMENT_CELL_UNAVAILABLE
        else:
            state = ASSIGNMENT_CELL_ADMIN_TIME
        return InstructorAssignmentCell(
            slot_key=slot.key,
            slot_label=slot_labels[slot.key],
            state=state,
            activity_name=None,
            group_index=None,
            group_label=None,
            group_accent_class=None,
            occurrence_id=None,
            is_multi_slot=False,
            footprint_position=None,
        )

    instructor_rows = tuple(sorted((
        InstructorAssignmentRow(
            instructor_id=instructor.pk,
            instructor_name=str(instructor),
            cells=tuple(
                assigned_cells.get(
                    (instructor.pk, slot.key),
                    build_nonassigned_cell(instructor.pk, slot),
                )
                for slot in slot_headers
            ),
        )
        for instructor in assignment_result.get('candidate_instructors') or ()
    ), key=lambda row: not any(
        cell.state == ASSIGNMENT_CELL_ASSIGNED for cell in row.cells
    )))

    unstaffed_occurrences = tuple(
        UnstaffedOccurrenceSummary(
            occurrence_id=(assignment.get('occurrence') or {}).get('occurrence_id'),
            activity_name=(assignment.get('occurrence') or {}).get(
                'activity_display_name'
            ) or 'Unnamed activity',
            group_label=(assignment.get('occurrence') or {}).get('group_label'),
            occupied_slots=tuple(
                _display_slot(slot.get('slot_key'), slot_labels)
                for slot in (assignment.get('occurrence') or {}).get(
                    'slot_footprint', ()
                )
            ),
            reason=assignment.get('reason') or 'No instructor was assigned.',
            rejection_details=tuple(
                _adapt_rejection(rejection, slot_labels)
                for rejection in assignment.get('constraint_rejections') or ()
            ) + tuple(
                _adapt_planning_diagnostic(diagnostic)
                for diagnostic in assignment.get('planning_diagnostics') or ()
            ),
        )
        for assignment in assignments
        if assignment.get('status') == 'unstaffed'
    )

    return InstructorAssignmentPresentation(
        schedule_id=assignment_result['schedule_id'],
        schedule_name=assignment_result['schedule_name'],
        day_headers=day_headers,
        slot_headers=slot_headers,
        instructor_rows=instructor_rows,
        unstaffed_occurrences=unstaffed_occurrences,
    )
