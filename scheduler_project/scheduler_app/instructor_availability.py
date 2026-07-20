from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from .forms import InstructorAvailabilityChangeForm
from .models import Instructor, InstructorScheduleAvailability
from .schedule_blocks import SCHEDULE_SLOT_KEYS


@dataclass(frozen=True)
class InstructorAvailabilityCell:
    slot_key: str
    state: str | None


@dataclass(frozen=True)
class InstructorAvailabilityRow:
    instructor: Instructor
    cells: tuple[InstructorAvailabilityCell, ...]


@dataclass(frozen=True)
class InstructorAvailabilityMatrix:
    organization_id: int
    schedule_id: int
    slot_keys: tuple[str, ...]
    rows: tuple[InstructorAvailabilityRow, ...]


@dataclass(frozen=True)
class InstructorAvailabilityChangeResult:
    created: int
    updated: int
    deleted: int
    unchanged: int


def _validate_schedule_ownership(organization, schedule):
    if schedule.organization_id != organization.pk:
        raise ValidationError(
            {'schedule': 'Schedule does not belong to the authorized organization.'}
        )


def build_instructor_availability_matrix(organization, schedule, instructors=None):
    """Build one immutable, schedule-scoped availability matrix without writes."""
    _validate_schedule_ownership(organization, schedule)

    if instructors is None:
        instructors = tuple(
            Instructor.objects.filter(organization=organization).order_by(
                'lname', 'fname', 'pk'
            )
        )
    else:
        instructors = tuple(instructors)
        if any(instructor.organization_id != organization.pk for instructor in instructors):
            raise ValidationError({
                'instructors': 'Every instructor must belong to the authorized organization.'
            })
        instructors = tuple(
            sorted(instructors, key=lambda instructor: (
                instructor.lname,
                instructor.fname,
                instructor.pk,
            ))
        )

    instructor_ids = tuple(instructor.pk for instructor in instructors)
    records = tuple(
        InstructorScheduleAvailability.objects.filter(
            organization=organization,
            schedule=schedule,
            instructor_id__in=instructor_ids,
        ).order_by('instructor_id', 'slot_key', 'pk')
    ) if instructor_ids else ()
    states_by_cell = {
        (record.instructor_id, record.slot_key): record.state
        for record in records
    }

    rows = tuple(
        InstructorAvailabilityRow(
            instructor=instructor,
            cells=tuple(
                InstructorAvailabilityCell(
                    slot_key=slot_key,
                    state=states_by_cell.get((instructor.pk, slot_key)),
                )
                for slot_key in SCHEDULE_SLOT_KEYS
            ),
        )
        for instructor in instructors
    )
    return InstructorAvailabilityMatrix(
        organization_id=organization.pk,
        schedule_id=schedule.pk,
        slot_keys=tuple(SCHEDULE_SLOT_KEYS),
        rows=rows,
    )


def _submitted_instructor_ids(changes):
    instructor_ids = set()
    for index, change in enumerate(changes):
        if not isinstance(change, dict):
            raise ValidationError({'changes': f'Change {index} must be a mapping.'})
        try:
            instructor_id = int(change.get('instructor_id'))
        except (TypeError, ValueError):
            raise ValidationError({'changes': f'Change {index} has an invalid instructor.'})
        instructor_ids.add(instructor_id)
    return instructor_ids


def apply_instructor_availability_changes(organization, schedule, changes):
    """Validate and atomically apply one schedule's explicit availability changes."""
    _validate_schedule_ownership(organization, schedule)
    if not isinstance(changes, (list, tuple)):
        raise ValidationError({'changes': 'Changes must be a list or tuple.'})

    with transaction.atomic():
        instructor_ids = _submitted_instructor_ids(changes)
        instructors_by_id = {
            instructor.pk: instructor
            for instructor in Instructor.objects.filter(
                organization=organization,
                pk__in=instructor_ids,
            )
        }

        normalized_changes = []
        seen_cells = set()
        validation_errors = []
        for index, change in enumerate(changes):
            form = InstructorAvailabilityChangeForm(
                change,
                organization=organization,
                instructors_by_id=instructors_by_id,
            )
            if not form.is_valid():
                validation_errors.append(f'Change {index}: {form.errors.as_text()}')
                continue

            instructor = form.cleaned_data['instructor_id']
            slot_key = form.cleaned_data['slot_key']
            cell_key = (instructor.pk, slot_key)
            if cell_key in seen_cells:
                validation_errors.append(
                    f'Change {index}: duplicate instructor and slot change.'
                )
                continue
            seen_cells.add(cell_key)
            normalized_changes.append((
                instructor,
                slot_key,
                form.cleaned_data['action'],
            ))

        if validation_errors:
            raise ValidationError({'changes': validation_errors})

        existing_records = {
            (record.instructor_id, record.slot_key): record
            for record in InstructorScheduleAvailability.objects.filter(
                organization=organization,
                schedule=schedule,
                instructor_id__in=instructor_ids,
                slot_key__in={slot_key for _instructor, slot_key, _action in normalized_changes},
            )
        }
        to_create = []
        to_update = []
        to_delete = []
        unchanged = 0

        for instructor, slot_key, action in normalized_changes:
            existing = existing_records.get((instructor.pk, slot_key))
            if action == InstructorAvailabilityChangeForm.CLEAR:
                if existing is None:
                    unchanged += 1
                else:
                    to_delete.append(existing.pk)
                continue

            if existing is None:
                to_create.append(InstructorScheduleAvailability(
                    organization=organization,
                    instructor=instructor,
                    schedule=schedule,
                    slot_key=slot_key,
                    state=action,
                ))
            elif existing.state == action:
                unchanged += 1
            else:
                existing.state = action
                to_update.append(existing)

        if to_create:
            InstructorScheduleAvailability.objects.bulk_create(to_create)
        if to_update:
            InstructorScheduleAvailability.objects.bulk_update(to_update, ['state'])
        if to_delete:
            InstructorScheduleAvailability.objects.filter(pk__in=to_delete).delete()

        return InstructorAvailabilityChangeResult(
            created=len(to_create),
            updated=len(to_update),
            deleted=len(to_delete),
            unchanged=unchanged,
        )
