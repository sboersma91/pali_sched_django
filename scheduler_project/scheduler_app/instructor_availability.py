from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from .forms import InstructorAvailabilityChangeForm
from .models import (
    Instructor,
    InstructorScheduleAvailability,
    InstructorScheduleParticipation,
)
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


@dataclass(frozen=True)
class InstructorParticipationRow:
    instructor: Instructor
    state: str | None


@dataclass(frozen=True)
class InstructorParticipationMatrix:
    organization_id: int
    schedule_id: int
    rows: tuple[InstructorParticipationRow, ...]


@dataclass(frozen=True)
class InstructorParticipationChangeResult:
    created: int
    updated: int
    deleted: int
    unchanged: int


def _validate_schedule_ownership(organization, schedule):
    if schedule.organization_id != organization.pk:
        raise ValidationError(
            {'schedule': 'Schedule does not belong to the authorized organization.'}
        )


def preload_instructor_schedule_participation(organization, schedule):
    """Load one schedule's organization-scoped participation decisions."""
    _validate_schedule_ownership(organization, schedule)
    return tuple(
        InstructorScheduleParticipation.objects.select_related('instructor').filter(
            organization=organization,
            schedule=schedule,
            instructor__organization=organization,
        ).order_by('instructor__lname', 'instructor__fname', 'instructor_id', 'pk')
    )


def build_participating_instructors(organization_id, schedule_id, participation_records):
    """Return deterministically ordered explicit participants without queries."""
    if not isinstance(participation_records, (list, tuple)):
        raise TypeError('participation_records must be a preloaded list or tuple.')

    participants_by_id = {}
    seen_instructor_ids = set()
    for record in participation_records:
        if getattr(record, 'organization_id', None) != organization_id:
            continue
        if getattr(record, 'schedule_id', None) != schedule_id:
            continue

        instructor = getattr(record, 'instructor', None)
        instructor_id = getattr(record, 'instructor_id', None)
        if instructor is None or instructor_id is None:
            raise ValidationError({'participation': 'Participation record has no instructor.'})
        if instructor.organization_id != organization_id:
            raise ValidationError({
                'participation': 'Participation instructor belongs to another organization.'
            })
        if instructor_id in seen_instructor_ids:
            raise ValidationError({
                'participation': 'Duplicate instructor participation record.'
            })
        seen_instructor_ids.add(instructor_id)

        state = getattr(record, 'state', None)
        if state not in {
            InstructorScheduleParticipation.PARTICIPATING,
            InstructorScheduleParticipation.NOT_PARTICIPATING,
        }:
            raise ValidationError({'participation': 'Invalid participation state.'})
        if state == InstructorScheduleParticipation.PARTICIPATING:
            participants_by_id[instructor_id] = instructor

    return tuple(sorted(
        participants_by_id.values(),
        key=lambda instructor: (instructor.lname, instructor.fname, instructor.pk),
    ))


def resolve_schedule_participating_instructors(
    organization_id,
    schedule_id,
    instructors,
    participation_records,
):
    """Resolve the opt-out staffing pool from preloaded instructors and decisions."""
    if not isinstance(instructors, (list, tuple)):
        raise TypeError('instructors must be a preloaded list or tuple.')
    if not isinstance(participation_records, (list, tuple)):
        raise TypeError('participation_records must be a preloaded list or tuple.')

    instructors_by_id = {}
    for instructor in instructors:
        if instructor.organization_id != organization_id:
            continue
        instructors_by_id[instructor.pk] = instructor

    states_by_instructor_id = {}
    for record in participation_records:
        if getattr(record, 'organization_id', None) != organization_id:
            continue
        if getattr(record, 'schedule_id', None) != schedule_id:
            continue
        instructor = getattr(record, 'instructor', None)
        instructor_id = getattr(record, 'instructor_id', None)
        if instructor is None or instructor_id not in instructors_by_id:
            raise ValidationError({
                'participation': 'Participation record has an invalid instructor.'
            })
        if instructor.organization_id != organization_id:
            raise ValidationError({
                'participation': 'Participation instructor belongs to another organization.'
            })
        if instructor_id in states_by_instructor_id:
            raise ValidationError({
                'participation': 'Duplicate instructor participation record.'
            })
        state = getattr(record, 'state', None)
        if state not in {
            InstructorScheduleParticipation.PARTICIPATING,
            InstructorScheduleParticipation.NOT_PARTICIPATING,
        }:
            raise ValidationError({'participation': 'Invalid participation state.'})
        states_by_instructor_id[instructor_id] = state

    return tuple(
        instructor
        for instructor in sorted(
            instructors_by_id.values(),
            key=lambda candidate: (candidate.lname, candidate.fname, candidate.pk),
        )
        if states_by_instructor_id.get(instructor.pk)
        != InstructorScheduleParticipation.NOT_PARTICIPATING
    )


def build_instructor_participation_matrix(organization, schedule):
    """Build the complete schedule participation decision matrix without writes."""
    _validate_schedule_ownership(organization, schedule)
    instructors = tuple(
        Instructor.objects.filter(organization=organization).order_by(
            'lname', 'fname', 'pk'
        )
    )
    records = preload_instructor_schedule_participation(organization, schedule)
    states_by_instructor = {
        record.instructor_id: record.state
        for record in records
    }
    return InstructorParticipationMatrix(
        organization_id=organization.pk,
        schedule_id=schedule.pk,
        rows=tuple(
            InstructorParticipationRow(
                instructor=instructor,
                state=(
                    InstructorScheduleParticipation.NOT_PARTICIPATING
                    if states_by_instructor.get(instructor.pk)
                    == InstructorScheduleParticipation.NOT_PARTICIPATING
                    else InstructorScheduleParticipation.PARTICIPATING
                ),
            )
            for instructor in instructors
        ),
    )


def apply_instructor_participation_changes(organization, schedule, changes):
    """Validate and atomically apply one complete schedule participation form."""
    _validate_schedule_ownership(organization, schedule)
    if not isinstance(changes, (list, tuple)):
        raise ValidationError({'changes': 'Changes must be a list or tuple.'})

    with transaction.atomic():
        instructors = tuple(
            Instructor.objects.filter(organization=organization).order_by(
                'lname', 'fname', 'pk'
            )
        )
        instructors_by_id = {instructor.pk: instructor for instructor in instructors}
        expected_ids = set(instructors_by_id)
        normalized_states = {}

        for index, change in enumerate(changes):
            if not isinstance(change, dict):
                raise ValidationError({'changes': f'Change {index} must be a mapping.'})
            try:
                instructor_id = int(change.get('instructor_id'))
            except (TypeError, ValueError):
                raise ValidationError({
                    'changes': f'Change {index} has an invalid instructor.'
                })
            if instructor_id in normalized_states:
                raise ValidationError({
                    'changes': f'Change {index} duplicates an instructor.'
                })
            if instructor_id not in instructors_by_id:
                raise ValidationError({
                    'changes': f'Change {index} has a foreign or unexpected instructor.'
                })

            state = change.get('state')
            if state not in {
                InstructorScheduleParticipation.PARTICIPATING,
                InstructorScheduleParticipation.NOT_PARTICIPATING,
            }:
                raise ValidationError({
                    'changes': f'Change {index} has an invalid participation state.'
                })
            normalized_states[instructor_id] = state

        submitted_ids = set(normalized_states)
        if submitted_ids != expected_ids:
            raise ValidationError({
                'changes': 'Participation changes must include every organization instructor.'
            })

        existing_records = {
            record.instructor_id: record
            for record in InstructorScheduleParticipation.objects.filter(
                organization=organization,
                schedule=schedule,
                instructor_id__in=expected_ids,
            )
        }
        to_create = []
        to_update = []
        to_delete = []
        unchanged = 0

        for instructor_id, state in normalized_states.items():
            existing = existing_records.get(instructor_id)
            if state == InstructorScheduleParticipation.PARTICIPATING:
                if existing is None:
                    unchanged += 1
                else:
                    to_delete.append(existing.pk)
                continue
            if existing is None:
                to_create.append(InstructorScheduleParticipation(
                    organization=organization,
                    instructor=instructors_by_id[instructor_id],
                    schedule=schedule,
                    state=state,
                ))
            elif existing.state == state:
                unchanged += 1
            else:
                existing.state = state
                to_update.append(existing)

        if to_create:
            InstructorScheduleParticipation.objects.bulk_create(to_create)
        if to_update:
            InstructorScheduleParticipation.objects.bulk_update(to_update, ['state'])
        if to_delete:
            InstructorScheduleParticipation.objects.filter(pk__in=to_delete).delete()

        return InstructorParticipationChangeResult(
            created=len(to_create),
            updated=len(to_update),
            deleted=len(to_delete),
            unchanged=unchanged,
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
                    state=states_by_cell.get(
                        (instructor.pk, slot_key),
                        InstructorScheduleAvailability.AVAILABLE,
                    ),
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
            if action in {
                InstructorAvailabilityChangeForm.CLEAR,
                InstructorScheduleAvailability.AVAILABLE,
            }:
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
