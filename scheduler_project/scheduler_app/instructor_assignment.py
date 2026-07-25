"""Read-only boundaries for future instructor assignment workflows."""

from dataclasses import dataclass

from django.core.exceptions import ValidationError

from .schedule_blocks import (
    DAILY_OFF_ELIGIBLE_SLOT_KEYS_BY_DAY,
    SCHEDULE_SLOT_KEYS,
)
from .schedule_operations import iter_schedule_blocks
from .instructor_availability import (
    preload_instructor_schedule_participation,
    resolve_schedule_participating_instructors,
)
from .models import (
    ActivityCertificationRequirement,
    Course,
    InstructorCertification,
    Instructor,
    InstructorScheduleAvailability,
    InstructorScheduleParticipation,
)


@dataclass(frozen=True)
class _ResolvedAvailabilityRecord:
    organization_id: int
    instructor_id: int
    schedule_id: int
    slot_key: str
    state: str


DAILY_OFF_SATISFIED_BY_AVAILABILITY = 'satisfied_by_availability'
DAILY_OFF_RESERVATION_REQUIRED = 'reservation_required'


def normalize_daily_off_requirements(
    organization_id,
    schedule_id,
    participating_instructors,
    availability_records,
):
    """Normalize daily OFF evidence from preloaded participation and availability."""
    if not isinstance(participating_instructors, (list, tuple)):
        raise TypeError('participating_instructors must be a preloaded list or tuple.')
    if not isinstance(availability_records, (list, tuple)):
        raise TypeError('availability_records must be a preloaded list or tuple.')

    instructors_by_id = {
        instructor.pk: instructor
        for instructor in participating_instructors
        if instructor.organization_id == organization_id
    }
    unavailable_slot_keys_by_instructor = {
        instructor_id: [] for instructor_id in instructors_by_id
    }
    seen_cells = set()

    for record in availability_records:
        if getattr(record, 'organization_id', None) != organization_id:
            continue
        if getattr(record, 'schedule_id', None) != schedule_id:
            continue

        instructor_id = getattr(record, 'instructor_id', None)
        if instructor_id not in instructors_by_id:
            continue
        slot_key = getattr(record, 'slot_key', None)
        state = getattr(record, 'state', None)
        cell_key = (instructor_id, slot_key)

        if slot_key not in SCHEDULE_SLOT_KEYS:
            raise ValidationError({
                'availability': 'Availability record has an invalid schedule slot.'
            })
        if state not in {
            InstructorScheduleAvailability.AVAILABLE,
            InstructorScheduleAvailability.UNAVAILABLE,
        }:
            raise ValidationError({
                'availability': 'Availability record has an invalid state.'
            })
        if cell_key in seen_cells:
            raise ValidationError({
                'availability': 'Duplicate instructor availability record.'
            })
        seen_cells.add(cell_key)

        if state == InstructorScheduleAvailability.UNAVAILABLE:
            unavailable_slot_keys_by_instructor[instructor_id].append(slot_key)

    slot_order = {slot_key: index for index, slot_key in enumerate(SCHEDULE_SLOT_KEYS)}
    ordered_instructors = sorted(
        instructors_by_id.values(),
        key=lambda instructor: (instructor.lname, instructor.fname, instructor.pk),
    )
    requirements = []
    normalized_unavailable = {}
    for instructor in ordered_instructors:
        unavailable_slot_keys = tuple(sorted(
            unavailable_slot_keys_by_instructor[instructor.pk],
            key=lambda slot_key: slot_order[slot_key],
        ))
        normalized_unavailable[instructor.pk] = unavailable_slot_keys

        unavailable_slot_key_set = frozenset(unavailable_slot_keys)
        for day_key, eligible_slot_keys in DAILY_OFF_ELIGIBLE_SLOT_KEYS_BY_DAY:
            satisfaction_slot_key = next(
                (
                    slot_key
                    for slot_key in eligible_slot_keys
                    if slot_key in unavailable_slot_key_set
                ),
                None,
            )
            requirements.append({
                'instructor_id': instructor.pk,
                'day_key': day_key,
                'status': (
                    DAILY_OFF_SATISFIED_BY_AVAILABILITY
                    if satisfaction_slot_key is not None
                    else DAILY_OFF_RESERVATION_REQUIRED
                ),
                'satisfaction_slot_key': satisfaction_slot_key,
            })

    return {
        'requirements': tuple(requirements),
        'unavailable_slot_keys_by_instructor': normalized_unavailable,
    }


def extract_operational_occurrences(schedule):
    """Return one normalized record per activity occurrence in a stored schedule."""
    display_result = schedule.get_display_schedule_result()
    slot_order = {slot_key: index for index, slot_key in enumerate(SCHEDULE_SLOT_KEYS)}
    occurrences = {}

    for block in iter_schedule_blocks(display_result["schedule_rows"]):
        if not block.get("is_activity"):
            continue

        occurrence_key = (block.get("occurrence_id"), block.get("activity_id"))
        occurrence = occurrences.setdefault(
            occurrence_key,
            {
                "schedule_id": schedule.pk,
                "organization_id": schedule.organization_id,
                "activity_id": block.get("activity_id"),
                "activity_display_name": block.get("display_value"),
                "group_index": block.get("group_index"),
                "group_label": block.get("group_label"),
                "occurrence_id": block.get("occurrence_id"),
                "slot_footprint": [],
            },
        )
        occurrence["slot_footprint"].append(
            {
                "block_id": block.get("block_id"),
                "slot_key": block.get("slot_key"),
                "slot_label": block.get("slot_label"),
                "position": block.get("occurrence_position"),
            }
        )

    normalized_occurrences = list(occurrences.values())
    activity_ids = {
        occurrence['activity_id']
        for occurrence in normalized_occurrences
        if occurrence.get('activity_id') is not None
    }
    required_counts_by_activity_id = dict(
        Course.objects.filter(
            organization_id=schedule.organization_id,
            id__in=activity_ids,
        ).values_list('id', 'required_instructor_count')
    )
    for occurrence in normalized_occurrences:
        occurrence['required_instructor_count'] = required_counts_by_activity_id.get(
            occurrence.get('activity_id')
        )
        occurrence["slot_footprint"].sort(
            key=lambda slot: (
                slot_order.get(slot["slot_key"], len(slot_order)),
                slot["position"] or 0,
                slot["block_id"] or "",
            )
        )

    return sorted(
        normalized_occurrences,
        key=lambda occurrence: (
            occurrence["group_index"],
            min(
                slot_order.get(slot["slot_key"], len(slot_order))
                for slot in occurrence["slot_footprint"]
            ),
            occurrence["occurrence_id"] or "",
            occurrence["activity_id"] or 0,
        ),
    )


def evaluate_occurrence_qualifications(
    occurrence,
    candidate_instructors,
    instructor_certifications,
    course_requirements,
):
    """Classify supplied instructors using all-required certification semantics.

    ``instructor_certifications`` maps instructor IDs to certification ID
    iterables. ``course_requirements`` maps Course IDs to required
    certification ID iterables. The function performs no database access.
    """
    required_certification_ids = frozenset(
        course_requirements.get(occurrence["activity_id"], ())
    )
    occurrence_organization_id = occurrence.get("organization_id")
    qualified_instructors = []
    unqualified_instructors = []

    for instructor in sorted(candidate_instructors, key=lambda candidate: candidate.pk):
        possessed_certification_ids = frozenset(
            instructor_certifications.get(instructor.pk, ())
        )
        missing_certification_ids = tuple(
            sorted(required_certification_ids - possessed_certification_ids)
        )
        organization_mismatch = (
            occurrence_organization_id is not None
            and instructor.organization_id != occurrence_organization_id
        )

        if not organization_mismatch and not missing_certification_ids:
            qualified_instructors.append(instructor)
            continue

        unqualified_instructors.append({
            "instructor": instructor,
            "missing_certification_ids": missing_certification_ids,
            "organization_mismatch": organization_mismatch,
        })

    return {
        "qualified_instructors": qualified_instructors,
        "unqualified_instructors": unqualified_instructors,
    }


def evaluate_instructor_availability(
    occurrence,
    candidate_instructor,
    availability_records,
):
    """Evaluate explicit schedule-slot availability without database access."""
    if not isinstance(availability_records, (list, tuple)):
        raise TypeError('availability_records must be a preloaded list or tuple.')

    schedule_id = occurrence.get("schedule_id")
    organization_id = occurrence.get("organization_id")
    instructor_id = candidate_instructor.pk
    matching_records_by_slot = {}

    for record in availability_records:
        if getattr(record, "instructor_id", None) != instructor_id:
            continue
        if getattr(record, "schedule_id", None) != schedule_id:
            continue
        if getattr(record, "organization_id", None) != organization_id:
            continue
        if getattr(record, "organization_id", None) != candidate_instructor.organization_id:
            continue

        slot_key = getattr(record, "slot_key", None)
        matching_records_by_slot.setdefault(slot_key, []).append(record)

    failed_slots = []
    for slot in occurrence.get("slot_footprint", ()):
        slot_key = slot.get("slot_key")
        matching_records = matching_records_by_slot.get(slot_key, ())

        if not matching_records:
            reason_code = "missing_availability"
        elif len(matching_records) > 1:
            reason_code = "duplicate_availability"
        else:
            state = getattr(matching_records[0], "state", None)
            if state == "available":
                continue
            if state == "unavailable":
                reason_code = "explicitly_unavailable"
            else:
                reason_code = "invalid_availability_state"

        failed_slots.append({
            "slot_key": slot_key,
            "slot_label": slot.get("slot_label"),
            "reason_code": reason_code,
        })

    failed_slots = tuple(failed_slots)
    if not failed_slots:
        return {
            "passes": True,
            "code": None,
            "message": None,
            "severity": None,
            "rule": "explicit_schedule_slot_availability",
            "details": {"failed_slots": ()},
        }

    distinct_reason_codes = {failure["reason_code"] for failure in failed_slots}
    code = (
        next(iter(distinct_reason_codes))
        if len(distinct_reason_codes) == 1
        else "availability_requirements_not_met"
    )
    slot_keys = ", ".join(str(failure["slot_key"]) for failure in failed_slots)
    return {
        "passes": False,
        "code": code,
        "message": f"Instructor is not explicitly available for {slot_keys}.",
        "severity": "blocking",
        "rule": "explicit_schedule_slot_availability",
        "details": {"failed_slots": failed_slots},
    }


def _participation_failure(code, message):
    return {
        "passes": False,
        "code": code,
        "message": message,
        "severity": "blocking",
        "rule": "explicit_schedule_participation",
        "details": {"failed_slots": ()},
    }


def evaluate_resolved_instructor_availability(
    occurrence,
    candidate_instructor,
    participation_records,
    availability_records,
):
    """Resolve broad participation plus detailed slot records without queries.

    Precedence is deliberate: participation is opt-out and supplies an available
    baseline for every canonical slot;
    explicit slot rows override or confirm that baseline; invalid or ambiguous
    detailed records remain blocking through the raw availability evaluator.
    """
    if not isinstance(participation_records, (list, tuple)):
        raise TypeError('participation_records must be a preloaded list or tuple.')
    if not isinstance(availability_records, (list, tuple)):
        raise TypeError('availability_records must be a preloaded list or tuple.')

    matching_participation = [
        record
        for record in participation_records
        if getattr(record, 'organization_id', None) == occurrence.get('organization_id')
        and getattr(record, 'schedule_id', None) == occurrence.get('schedule_id')
        and getattr(record, 'instructor_id', None) == candidate_instructor.pk
        and getattr(record, 'organization_id', None) == candidate_instructor.organization_id
    ]
    if len(matching_participation) > 1:
        return _participation_failure(
            'duplicate_participation',
            'Instructor has duplicate participation decisions for this schedule.',
        )

    participation_state = (
        getattr(matching_participation[0], 'state', None)
        if matching_participation
        else InstructorScheduleParticipation.PARTICIPATING
    )
    if participation_state == InstructorScheduleParticipation.NOT_PARTICIPATING:
        return _participation_failure(
            'not_participating',
            'Instructor is not participating in this schedule.',
        )
    if participation_state != InstructorScheduleParticipation.PARTICIPATING:
        return _participation_failure(
            'invalid_participation_state',
            'Instructor has an invalid participation decision for this schedule.',
        )

    resolved_records = list(availability_records)
    for slot in occurrence.get('slot_footprint', ()):
        slot_key = slot.get('slot_key')
        matching_slot_records = [
            record
            for record in availability_records
            if getattr(record, 'organization_id', None) == occurrence.get('organization_id')
            and getattr(record, 'schedule_id', None) == occurrence.get('schedule_id')
            and getattr(record, 'instructor_id', None) == candidate_instructor.pk
            and getattr(record, 'organization_id', None) == candidate_instructor.organization_id
            and getattr(record, 'slot_key', None) == slot_key
        ]
        if matching_slot_records:
            continue
        resolved_records.append(_ResolvedAvailabilityRecord(
            organization_id=occurrence.get('organization_id'),
            instructor_id=candidate_instructor.pk,
            schedule_id=occurrence.get('schedule_id'),
            slot_key=slot_key,
            state=InstructorScheduleAvailability.AVAILABLE,
        ))

    return evaluate_instructor_availability(
        occurrence,
        candidate_instructor,
        resolved_records,
    )


def preload_instructor_availability(
    organization_id,
    schedule_id,
    candidate_instructors,
):
    """Return one materialized, scoped availability context for an assignment run."""
    instructor_ids = tuple(sorted({instructor.pk for instructor in candidate_instructors}))
    if not instructor_ids:
        return ()

    return tuple(
        InstructorScheduleAvailability.objects.filter(
            organization_id=organization_id,
            schedule_id=schedule_id,
            instructor_id__in=instructor_ids,
        ).order_by("instructor_id", "slot_key", "pk")
    )


def evaluate_instructor_assignment_overlap(
    occurrence,
    candidate_instructor,
    existing_assignments,
):
    """Return whether a candidate is free for the occurrence's schedule slots."""
    proposed_slot_keys = frozenset(
        slot.get("slot_key")
        for slot in occurrence.get("slot_footprint", ())
        if slot.get("slot_key") is not None
    )

    for assignment in existing_assignments:
        if assignment.get("assigned_instructor") != candidate_instructor:
            continue

        assigned_occurrence = assignment.get("occurrence") or {}
        if assigned_occurrence.get("schedule_id") != occurrence.get("schedule_id"):
            continue

        assigned_slot_keys = frozenset(
            slot.get("slot_key")
            for slot in assigned_occurrence.get("slot_footprint", ())
            if slot.get("slot_key") is not None
        )
        overlapping_slot_keys = tuple(sorted(proposed_slot_keys & assigned_slot_keys))
        if not overlapping_slot_keys:
            continue

        return {
            "passes": False,
            "code": "overlapping_assignment",
            "message": (
                "Instructor is already assigned during "
                f"{', '.join(overlapping_slot_keys)}."
            ),
            "severity": "blocking",
            "rule": "no_overlapping_assignments",
            "details": {
                "conflicting_occurrence_id": assigned_occurrence.get("occurrence_id"),
                "overlapping_slot_keys": overlapping_slot_keys,
            },
        }

    return {
        "passes": True,
        "code": None,
        "message": None,
        "severity": None,
        "rule": "no_overlapping_assignments",
        "details": {
            "conflicting_occurrence_id": None,
            "overlapping_slot_keys": (),
        },
    }


def evaluate_occurrence_constraints(
    occurrence,
    qualified_instructors,
    existing_assignments,
    availability_records,
    participation_records=None,
):
    """Classify qualified instructors using operational assignment constraints."""
    eligible_instructors = []
    rejected_instructors = []

    for instructor in qualified_instructors:
        if participation_records is None:
            availability_result = evaluate_instructor_availability(
                occurrence,
                instructor,
                availability_records,
            )
        else:
            availability_result = evaluate_resolved_instructor_availability(
                occurrence,
                instructor,
                participation_records,
                availability_records,
            )
        if not availability_result["passes"]:
            rejected_instructors.append({
                "instructor": instructor,
                "reasons": [availability_result],
            })
            continue

        overlap_result = evaluate_instructor_assignment_overlap(
            occurrence,
            instructor,
            existing_assignments,
        )
        if overlap_result["passes"]:
            eligible_instructors.append(instructor)
            continue

        rejected_instructors.append({
            "instructor": instructor,
            "reasons": [overlap_result],
        })

    return {
        "eligible_instructors": eligible_instructors,
        "rejected_instructors": rejected_instructors,
        "warnings": [],
    }


def assign_occurrences_deterministically(
    occurrences,
    candidate_instructors,
    instructor_certifications,
    course_requirements,
    availability_records,
    participation_records=None,
):
    """Assign the first qualified and eligible instructor to each occurrence."""
    candidates = tuple(candidate_instructors)
    assignments = []

    for occurrence in occurrences:
        qualification_result = evaluate_occurrence_qualifications(
            occurrence,
            candidates,
            instructor_certifications,
            course_requirements,
        )
        qualified_instructors = qualification_result["qualified_instructors"]
        if participation_records is None:
            constraint_result = evaluate_occurrence_constraints(
                occurrence,
                qualified_instructors,
                assignments,
                availability_records,
            )
        else:
            constraint_result = evaluate_occurrence_constraints(
                occurrence,
                qualified_instructors,
                assignments,
                availability_records,
                participation_records,
            )
        eligible_instructors = constraint_result["eligible_instructors"]
        assigned_instructor = eligible_instructors[0] if eligible_instructors else None

        if assigned_instructor:
            reason = None
        elif qualified_instructors:
            reason = "No eligible instructors available."
        else:
            reason = "No qualified instructors available."

        assignments.append({
            "occurrence": occurrence,
            "assigned_instructor": assigned_instructor,
            "status": "assigned" if assigned_instructor else "unstaffed",
            "reason": reason,
            "constraint_rejections": constraint_result["rejected_instructors"],
        })

    return assignments


def _planner_occurrence_sort_key(occurrence):
    slot_order = {slot_key: index for index, slot_key in enumerate(SCHEDULE_SLOT_KEYS)}
    footprint = occurrence.get('slot_footprint') or ()
    first_slot_index = min(
        (
            slot_order.get(slot.get('slot_key'), len(slot_order))
            for slot in footprint
        ),
        default=len(slot_order),
    )
    group_index = occurrence.get('group_index')
    return (
        group_index if isinstance(group_index, int) else 0,
        first_slot_index,
        occurrence.get('occurrence_id') or '',
        occurrence.get('activity_id') or 0,
    )


def _daily_off_rejection(occurrence, instructor, day_key, before, after):
    occurrence_slot_keys = tuple(
        slot.get('slot_key')
        for slot in occurrence.get('slot_footprint') or ()
        if slot.get('slot_key') is not None
    )
    consumed_candidate_slot_keys = tuple(
        slot_key for slot_key in occurrence_slot_keys if slot_key in before
    )
    return {
        'passes': False,
        'code': 'daily_off_requirement',
        'message': (
            f'Assignment would consume the instructor\'s final eligible '
            f'{day_key} OFF slot.'
        ),
        'severity': 'blocking',
        'rule': 'required_daily_off_block',
        'details': {
            'instructor_id': instructor.pk,
            'day_key': day_key,
            'affected_slot_keys': occurrence_slot_keys,
            'consumed_off_candidate_slot_keys': consumed_candidate_slot_keys,
            'remaining_candidate_slot_keys_before': tuple(before),
            'remaining_candidate_slot_keys_after': tuple(after),
        },
    }


def _group_continuity_key(occurrence):
    return (
        occurrence.get('schedule_id'),
        occurrence.get('group_index'),
    )


def _order_candidates_for_group_continuity(
    occurrence,
    eligible_candidates,
    continuity_state_by_group,
):
    """Order hard-valid candidates using branch-local group continuity.

    A pending pre-interruption instructor is preferred when valid again. The
    group's current instructor is next, followed by canonical instructor-PK
    order. The input candidate order does not affect the result.
    """
    state = continuity_state_by_group.get(_group_continuity_key(occurrence), {})
    last_instructor_id = state.get('last_instructor_id')
    pending_return_instructor_id = state.get('pending_return_instructor_id')

    def continuity_sort_key(instructor):
        if instructor.pk == pending_return_instructor_id:
            category = 0
        elif instructor.pk == last_instructor_id:
            category = 1
        else:
            category = 2
        return category, instructor.pk

    return sorted(eligible_candidates, key=continuity_sort_key)


def _advance_group_continuity_state(
    occurrence,
    selected_instructor,
    eligible_candidates,
    continuity_state_by_group,
):
    """Return branch-local continuity state after one staffed occurrence.

    ``pending_return_instructor_id`` records only the most recent instructor
    displaced because that instructor was hard-invalid. It survives additional
    unavoidable replacements, clears on return, and clears when a valid pending
    return or valid current instructor is bypassed. Unstaffed occurrences do not
    call this helper and therefore do not alter continuity state.
    """
    group_key = _group_continuity_key(occurrence)
    current_state = continuity_state_by_group.get(group_key, {})
    last_instructor_id = current_state.get('last_instructor_id')
    pending_return_instructor_id = current_state.get(
        'pending_return_instructor_id'
    )
    eligible_instructor_ids = {
        instructor.pk for instructor in eligible_candidates
    }
    selected_instructor_id = selected_instructor.pk

    if last_instructor_id is None:
        next_pending_return_instructor_id = None
    elif pending_return_instructor_id is not None:
        if selected_instructor_id == pending_return_instructor_id:
            next_pending_return_instructor_id = None
        elif pending_return_instructor_id in eligible_instructor_ids:
            next_pending_return_instructor_id = None
        elif (
            selected_instructor_id == last_instructor_id
            or last_instructor_id not in eligible_instructor_ids
        ):
            next_pending_return_instructor_id = pending_return_instructor_id
        else:
            next_pending_return_instructor_id = None
    elif selected_instructor_id == last_instructor_id:
        next_pending_return_instructor_id = None
    elif last_instructor_id not in eligible_instructor_ids:
        next_pending_return_instructor_id = last_instructor_id
    else:
        next_pending_return_instructor_id = None

    next_state = dict(continuity_state_by_group)
    next_state[group_key] = {
        'last_instructor_id': selected_instructor_id,
        'pending_return_instructor_id': next_pending_return_instructor_id,
    }
    return next_state


def plan_instructor_assignments_with_daily_off(
    occurrences,
    candidate_instructors,
    instructor_certifications,
    course_requirements,
    availability_records,
    participation_records,
    normalized_daily_off_requirements,
    fixed_assignments=(),
    _search_stats=None,
    _calculate_fixed_baseline=True,
):
    """Return a maximum-coverage, deterministic, in-memory daily-OFF plan.

    ``fixed_assignments`` is a pure, transient planning input containing zero
    or more ``{"occurrence": ..., "instructor": ...}`` mappings.
    """
    if _search_stats is not None:
        _search_stats.clear()
        _search_stats.update(
            explored_node_count=0,
            completed_plan_count=0,
        )

    for name, value in (
        ('occurrences', occurrences),
        ('candidate_instructors', candidate_instructors),
        ('availability_records', availability_records),
        ('participation_records', participation_records),
        ('fixed_assignments', fixed_assignments),
    ):
        if not isinstance(value, (list, tuple)):
            raise TypeError(f'{name} must be a preloaded list or tuple.')

    ordered_occurrences = tuple(sorted(occurrences, key=_planner_occurrence_sort_key))
    candidates = tuple(sorted(candidate_instructors, key=lambda instructor: instructor.pk))
    candidate_ids = {instructor.pk for instructor in candidates}
    eligible_slots_by_day = dict(DAILY_OFF_ELIGIBLE_SLOT_KEYS_BY_DAY)
    requirements = tuple(normalized_daily_off_requirements.get('requirements') or ())
    requirements_by_key = {}

    for requirement in requirements:
        instructor_id = requirement.get('instructor_id')
        day_key = requirement.get('day_key')
        key = (instructor_id, day_key)
        if instructor_id not in candidate_ids or day_key not in eligible_slots_by_day:
            raise ValidationError({'daily_off': 'Daily OFF requirement is out of scope.'})
        if key in requirements_by_key:
            raise ValidationError({'daily_off': 'Duplicate daily OFF requirement.'})
        status = requirement.get('status')
        if status not in {
            DAILY_OFF_SATISFIED_BY_AVAILABILITY,
            DAILY_OFF_RESERVATION_REQUIRED,
        }:
            raise ValidationError({'daily_off': 'Invalid daily OFF requirement status.'})
        requirements_by_key[key] = requirement

    expected_requirement_keys = {
        (instructor.pk, day_key)
        for instructor in candidates
        for day_key in eligible_slots_by_day
    }
    if set(requirements_by_key) != expected_requirement_keys:
        raise ValidationError({
            'daily_off': 'Daily OFF requirements must cover every participating instructor.'
        })

    initial_remaining_off_candidates = {
        key: tuple(eligible_slots_by_day[key[1]])
        for key, requirement in requirements_by_key.items()
        if requirement.get('status') == DAILY_OFF_RESERVATION_REQUIRED
    }
    qualification_by_occurrence_id = {}
    for occurrence in ordered_occurrences:
        qualification_by_occurrence_id[id(occurrence)] = evaluate_occurrence_qualifications(
            occurrence,
            candidates,
            instructor_certifications,
            course_requirements,
        )

    fixed_reservations = []
    fixed_diagnostics = []
    fixed_by_occurrence_id = {}
    fixed_slot_keys_by_instructor = {}
    rejected_fixed_set = False
    indexed_fixed_assignments = sorted(
        enumerate(fixed_assignments),
        key=lambda item: (
            _planner_occurrence_sort_key(
                item[1].get('occurrence')
                if isinstance(item[1], dict)
                and isinstance(item[1].get('occurrence'), dict)
                else {}
            ),
            getattr(
                item[1].get('instructor')
                if isinstance(item[1], dict) else None,
                'pk',
                -1,
            ),
            item[0],
        ),
    )
    for fixed_index, fixed_assignment in indexed_fixed_assignments:
        if not isinstance(fixed_assignment, dict):
            raise TypeError('Each fixed assignment must be a mapping.')
        submitted_occurrence = fixed_assignment.get('occurrence')
        submitted_instructor = fixed_assignment.get('instructor')
        fixed_occurrence = next(
            (
                occurrence
                for occurrence in ordered_occurrences
                if occurrence is submitted_occurrence
                or occurrence == submitted_occurrence
            ),
            None,
        )
        fixed_instructor = submitted_instructor
        fixed_rejection = None
        fixed_slot_keys = ()
        if fixed_occurrence is None:
            fixed_rejection = {
                'code': 'occurrence_not_found',
                'details': {'affected_slot_keys': ()},
            }
        elif fixed_occurrence.get('required_instructor_count') not in (None, 1):
            fixed_rejection = {
                'code': 'unsupported_instructor_count',
                'details': {
                    'affected_slot_keys': tuple(
                        slot.get('slot_key')
                        for slot in fixed_occurrence.get('slot_footprint') or ()
                    ),
                },
            }
        elif (
            fixed_instructor is None
            or fixed_instructor.organization_id
            != fixed_occurrence.get('organization_id')
        ):
            fixed_rejection = {
                'code': 'organization_mismatch',
                'details': {'affected_slot_keys': ()},
            }
        elif fixed_instructor.pk not in candidate_ids:
            participation_state = next(
                (
                    record.state
                    for record in participation_records
                    if record.organization_id == fixed_occurrence.get('organization_id')
                    and record.schedule_id == fixed_occurrence.get('schedule_id')
                    and record.instructor_id == fixed_instructor.pk
                ),
                None,
            )
            fixed_rejection = {
                'code': (
                    'not_participating'
                    if participation_state
                    == InstructorScheduleParticipation.NOT_PARTICIPATING
                    else 'instructor_not_candidate'
                ),
                'details': {'affected_slot_keys': ()},
            }
        else:
            fixed_slot_keys = tuple(
                slot.get('slot_key')
                for slot in fixed_occurrence.get('slot_footprint') or ()
                if slot.get('slot_key') is not None
            )
            qualification = qualification_by_occurrence_id[id(fixed_occurrence)]
            if fixed_instructor not in qualification['qualified_instructors']:
                fixed_rejection = {
                    'code': 'qualification_requirements_not_met',
                    'details': {'affected_slot_keys': fixed_slot_keys},
                }
            else:
                constraint_result = evaluate_occurrence_constraints(
                    fixed_occurrence,
                    [fixed_instructor],
                    [],
                    availability_records,
                    participation_records,
                )
                if not constraint_result['eligible_instructors']:
                    fixed_rejection = (
                        constraint_result['rejected_instructors'][0]['reasons'][0]
                    )
        occurrence_key = (
            fixed_occurrence.get('occurrence_id')
            if fixed_occurrence is not None
            else None
        )
        if fixed_rejection is None and occurrence_key in fixed_by_occurrence_id:
            fixed_rejection = {
                'code': 'duplicate_fixed_occurrence',
                'details': {'affected_slot_keys': fixed_slot_keys},
            }
        prior_slot_keys = fixed_slot_keys_by_instructor.get(
            getattr(fixed_instructor, 'pk', None),
            frozenset(),
        )
        overlapping_slot_keys = tuple(
            slot_key for slot_key in fixed_slot_keys
            if slot_key in prior_slot_keys
        )
        if fixed_rejection is None and overlapping_slot_keys:
            fixed_rejection = {
                'code': 'instructor_overlap',
                'details': {'overlapping_slot_keys': overlapping_slot_keys},
            }

        rejection_details = (
            (fixed_rejection.get('details') or {})
            if fixed_rejection else {}
        )
        failed_slot_keys = tuple(
            failure.get('slot_key')
            for failure in rejection_details.get('failed_slots', ())
            if failure.get('slot_key') is not None
        )
        diagnostic = {
            'fixed_assignment_index': fixed_index,
            'occurrence': fixed_occurrence or submitted_occurrence,
            'instructor': fixed_instructor,
            'accepted': fixed_rejection is None,
            'rejection_code': (
                fixed_rejection.get('code') if fixed_rejection else None
            ),
            'affected_slot_keys': tuple(
                failed_slot_keys
                or rejection_details.get('overlapping_slot_keys')
                or rejection_details.get('affected_slot_keys')
                or fixed_slot_keys
            ),
        }
        fixed_diagnostics.append(diagnostic)
        if fixed_rejection is not None:
            rejected_fixed_set = True
            continue
        fixed_by_occurrence_id[occurrence_key] = {
            'occurrence': fixed_occurrence,
            'instructor': fixed_instructor,
            'slot_keys': fixed_slot_keys,
            'diagnostic': diagnostic,
        }
        fixed_slot_keys_by_instructor[fixed_instructor.pk] = frozenset(
            prior_slot_keys | frozenset(fixed_slot_keys)
        )

    for fixed in fixed_by_occurrence_id.values():
        instructor = fixed['instructor']
        for day_key, _eligible_slot_keys in DAILY_OFF_ELIGIBLE_SLOT_KEYS_BY_DAY:
            key = (instructor.pk, day_key)
            before = initial_remaining_off_candidates.get(key)
            if before is None:
                continue
            after = tuple(
                slot_key for slot_key in before
                if slot_key not in fixed['slot_keys']
            )
            if not after:
                fixed['diagnostic'].update({
                    'accepted': False,
                    'rejection_code': 'daily_off_requirement',
                    'affected_slot_keys': tuple(before),
                })
                rejected_fixed_set = True
                break
            initial_remaining_off_candidates[key] = after

    baseline_result = None
    if fixed_assignments and _calculate_fixed_baseline:
        baseline_result = plan_instructor_assignments_with_daily_off(
            occurrences,
            candidate_instructors,
            instructor_certifications,
            course_requirements,
            availability_records,
            participation_records,
            normalized_daily_off_requirements,
            fixed_assignments=(),
            _calculate_fixed_baseline=False,
        )

    if rejected_fixed_set:
        coverage_after = baseline_result['coverage']['assigned_occurrence_count']
        for diagnostic in fixed_diagnostics:
            diagnostic.update({
                'coverage_before': coverage_after,
                'coverage_after': coverage_after,
                'coverage_delta': 0,
                'requires_confirmation': False,
            })
        return {
            **baseline_result,
            'accepted_fixed_assignments': (),
            'fixed_assignment_diagnostics': tuple(fixed_diagnostics),
        }

    fixed_reservations = [
        {
            'occurrence': fixed['occurrence'],
            'assigned_instructor': fixed['instructor'],
            'status': 'assigned',
            'reason': None,
            'constraint_rejections': (),
            'planning_diagnostics': (),
            'assignment_source': 'fixed',
        }
        for fixed in fixed_by_occurrence_id.values()
    ]

    best = {
        'assigned_count': -1,
        'assignments': (),
        'remaining_off_candidates': None,
        'occupied_slot_keys_by_instructor': None,
    }

    def search(
        occurrence_index,
        assignments,
        remaining_off_candidates,
        occupied_slot_keys_by_instructor,
        assigned_count,
        continuity_state_by_group,
    ):
        if _search_stats is not None:
            _search_stats['explored_node_count'] += 1
        remaining_occurrence_count = len(ordered_occurrences) - occurrence_index
        if assigned_count + remaining_occurrence_count <= best['assigned_count']:
            return
        if occurrence_index == len(ordered_occurrences):
            if _search_stats is not None:
                _search_stats['completed_plan_count'] += 1
            best.update({
                'assigned_count': assigned_count,
                'assignments': tuple(assignments),
                'remaining_off_candidates': dict(remaining_off_candidates),
                'occupied_slot_keys_by_instructor': {
                    instructor_id: tuple(sorted(
                        slot_keys,
                        key=SCHEDULE_SLOT_KEYS.index,
                    ))
                    for instructor_id, slot_keys in occupied_slot_keys_by_instructor.items()
                },
            })
            return

        occurrence = ordered_occurrences[occurrence_index]
        fixed = fixed_by_occurrence_id.get(occurrence.get('occurrence_id'))
        qualification_result = qualification_by_occurrence_id[id(occurrence)]
        qualified_instructors = qualification_result['qualified_instructors']
        constraint_result = evaluate_occurrence_constraints(
            occurrence,
            qualified_instructors,
            assignments + [
                reservation
                for reservation in fixed_reservations
                if reservation['occurrence'] is not occurrence
            ],
            availability_records,
            participation_records,
        )
        eligible_instructors = constraint_result['eligible_instructors']
        eligible_with_off_capacity = []
        off_rejections = []
        footprint_slot_keys = frozenset(
            slot.get('slot_key')
            for slot in occurrence.get('slot_footprint') or ()
            if slot.get('slot_key') is not None
        )

        instructors_requiring_off_evaluation = (
            [] if fixed is not None else eligible_instructors
        )
        if fixed is not None:
            eligible_with_off_capacity.append((
                fixed['instructor'],
                remaining_off_candidates,
            ))
        for instructor in instructors_requiring_off_evaluation:
            candidate_remaining = dict(remaining_off_candidates)
            candidate_off_reasons = []
            for day_key, eligible_slot_keys in DAILY_OFF_ELIGIBLE_SLOT_KEYS_BY_DAY:
                key = (instructor.pk, day_key)
                before = candidate_remaining.get(key)
                if before is None:
                    continue
                after = tuple(
                    slot_key
                    for slot_key in before
                    if slot_key not in footprint_slot_keys
                )
                if not after:
                    candidate_off_reasons.append(_daily_off_rejection(
                        occurrence,
                        instructor,
                        day_key,
                        before,
                        after,
                    ))
                candidate_remaining[key] = after

            if candidate_off_reasons:
                off_rejections.append({
                    'instructor': instructor,
                    'reasons': candidate_off_reasons,
                })
                continue
            eligible_with_off_capacity.append((instructor, candidate_remaining))

        remaining_by_instructor_id = {
            instructor.pk: candidate_remaining
            for instructor, candidate_remaining in eligible_with_off_capacity
        }
        continuity_ordered_instructors = _order_candidates_for_group_continuity(
            occurrence,
            [instructor for instructor, _remaining in eligible_with_off_capacity],
            continuity_state_by_group,
        )
        if fixed is not None:
            continuity_ordered_instructors = [fixed['instructor']]

        for instructor in continuity_ordered_instructors:
            if fixed is not None:
                candidate_remaining = remaining_off_candidates
            else:
                candidate_remaining = remaining_by_instructor_id[instructor.pk]
            assignment = {
                'occurrence': occurrence,
                'assigned_instructor': instructor,
                'status': 'assigned',
                'reason': None,
                'constraint_rejections': tuple(
                    constraint_result['rejected_instructors'] + off_rejections
                ),
                'planning_diagnostics': (),
            }
            if fixed is not None:
                assignment['assignment_source'] = 'fixed'
            candidate_occupied = dict(occupied_slot_keys_by_instructor)
            candidate_occupied[instructor.pk] = frozenset(
                candidate_occupied.get(instructor.pk, frozenset())
                | footprint_slot_keys
            )
            search(
                occurrence_index + 1,
                assignments + [assignment],
                candidate_remaining,
                candidate_occupied,
                assigned_count + 1,
                _advance_group_continuity_state(
                    occurrence,
                    instructor,
                    continuity_ordered_instructors,
                    continuity_state_by_group,
                ),
            )

        if fixed is not None:
            return
        if not qualified_instructors:
            reason = 'No qualified instructors available.'
            planning_diagnostics = ()
        elif not eligible_with_off_capacity:
            reason = 'No eligible instructors available.'
            planning_diagnostics = ()
        else:
            reason = 'Left unstaffed to preserve maximum feasible hard-constraint coverage.'
            planning_diagnostics = ({
                'code': 'global_planning_choice',
                'message': reason,
                'severity': 'info',
                'rule': 'maximum_feasible_coverage',
                'details': {
                    'occurrence_id': occurrence.get('occurrence_id'),
                },
            },)
        unstaffed_assignment = {
            'occurrence': occurrence,
            'assigned_instructor': None,
            'status': 'unstaffed',
            'reason': reason,
            'constraint_rejections': tuple(
                constraint_result['rejected_instructors'] + off_rejections
            ),
            'planning_diagnostics': planning_diagnostics,
        }
        search(
            occurrence_index + 1,
            assignments + [unstaffed_assignment],
            remaining_off_candidates,
            occupied_slot_keys_by_instructor,
            assigned_count,
            continuity_state_by_group,
        )

    search(
        0,
        [],
        initial_remaining_off_candidates,
        {
            instructor.pk: fixed_slot_keys_by_instructor.get(
                instructor.pk,
                frozenset(),
            )
            for instructor in candidates
        },
        0,
        {},
    )
    final_requirements = []
    off_reservations = []
    availability_satisfied_requirements = []
    for requirement in requirements:
        normalized = dict(requirement)
        key = (requirement['instructor_id'], requirement['day_key'])
        if requirement['status'] == DAILY_OFF_RESERVATION_REQUIRED:
            remaining_candidates = best['remaining_off_candidates'][key]
            selected_slot_key = remaining_candidates[0]
            normalized['remaining_candidate_slot_keys'] = remaining_candidates
            normalized['selected_reservation_slot_key'] = selected_slot_key
            off_reservations.append({
                'instructor_id': requirement['instructor_id'],
                'day_key': requirement['day_key'],
                'slot_key': selected_slot_key,
            })
        else:
            normalized['remaining_candidate_slot_keys'] = ()
            normalized['selected_reservation_slot_key'] = None
            availability_satisfied_requirements.append(normalized)
        final_requirements.append(normalized)

    assignments = best['assignments']
    unstaffed_occurrences = tuple(
        assignment for assignment in assignments
        if assignment['status'] == 'unstaffed'
    )
    result = {
        'assignments': assignments,
        'unstaffed_occurrences': unstaffed_occurrences,
        'off_requirements': tuple(final_requirements),
        'off_reservations': tuple(off_reservations),
        'requirements_satisfied_by_availability': tuple(
            availability_satisfied_requirements
        ),
        'unavailable_slot_keys_by_instructor': dict(
            normalized_daily_off_requirements.get(
                'unavailable_slot_keys_by_instructor'
            ) or {}
        ),
        'occupied_slot_keys_by_instructor': best['occupied_slot_keys_by_instructor'],
        'coverage': {
            'assigned_occurrence_count': best['assigned_count'],
            'unstaffed_occurrence_count': len(unstaffed_occurrences),
            'complete': not unstaffed_occurrences,
        },
    }
    if fixed_assignments:
        coverage_after = result['coverage']['assigned_occurrence_count']
        coverage_before = baseline_result['coverage']['assigned_occurrence_count']
        for diagnostic in fixed_diagnostics:
            diagnostic.update({
                'coverage_before': coverage_before,
                'coverage_after': coverage_after,
                'coverage_delta': coverage_after - coverage_before,
                'requires_confirmation': coverage_after < coverage_before,
            })
        result.update({
            'accepted_fixed_assignments': tuple(fixed_reservations),
            'fixed_assignment_diagnostics': tuple(fixed_diagnostics),
        })
    return result


def _run_instructor_assignment_core(schedule, fixed_assignments=()):
    """Run one non-persisted assignment for one schedule and organization."""
    if schedule.pk is None:
        raise ValidationError({'schedule': 'Assignment requires a saved schedule.'})

    organization = schedule.organization
    occurrences = extract_operational_occurrences(schedule)
    for occurrence in occurrences:
        if occurrence.get('schedule_id') != schedule.pk:
            raise ValidationError({
                'occurrences': 'Assignment occurrences must belong to one schedule.'
            })
        if occurrence.get('organization_id') != organization.pk:
            raise ValidationError({
                'occurrences': 'Assignment occurrences must belong to one organization.'
            })
        if occurrence.get('required_instructor_count') is None:
            raise ValidationError({
                'occurrences': (
                    'An operational occurrence does not resolve to a valid Course '
                    'in the schedule organization.'
                )
            })
        if occurrence.get('required_instructor_count') != 1:
            raise ValidationError({
                'occurrences': (
                    'Multi-instructor occurrence staffing is not supported in '
                    'the current release; required instructor count must be 1.'
                )
            })

    participation_records = preload_instructor_schedule_participation(
        organization,
        schedule,
    )
    organization_instructors = tuple(
        Instructor.objects.filter(organization=organization).order_by(
            'lname', 'fname', 'pk'
        )
    )
    candidate_instructors = resolve_schedule_participating_instructors(
        organization.pk,
        schedule.pk,
        organization_instructors,
        participation_records,
    )
    availability_records = preload_instructor_availability(
        organization.pk,
        schedule.pk,
        candidate_instructors,
    )

    instructor_certifications = {}
    for instructor_id, certification_id in InstructorCertification.objects.filter(
        instructor_id__in=[instructor.pk for instructor in candidate_instructors],
        instructor__organization=organization,
        certification__organization=organization,
    ).values_list('instructor_id', 'certification_id'):
        instructor_certifications.setdefault(instructor_id, set()).add(certification_id)

    activity_ids = {
        occurrence.get('activity_id')
        for occurrence in occurrences
        if occurrence.get('activity_id') is not None
    }
    course_requirements = {}
    for course_id, certification_id in ActivityCertificationRequirement.objects.filter(
        course_id__in=activity_ids,
        course__organization=organization,
        certification__organization=organization,
    ).values_list('course_id', 'certification_id'):
        course_requirements.setdefault(course_id, set()).add(certification_id)

    normalized_daily_off_requirements = normalize_daily_off_requirements(
        organization.pk,
        schedule.pk,
        candidate_instructors,
        availability_records,
    )
    planning_result = plan_instructor_assignments_with_daily_off(
        occurrences,
        candidate_instructors,
        instructor_certifications,
        course_requirements,
        availability_records,
        participation_records,
        normalized_daily_off_requirements,
        fixed_assignments=fixed_assignments,
    )
    result = {
        'schedule_id': schedule.pk,
        'schedule_name': schedule.sched_name,
        'organization_id': organization.pk,
        'candidate_instructors': candidate_instructors,
        'occurrences': occurrences,
    }
    result.update(planning_result)
    return result


def run_instructor_assignment(schedule, fixed_assignments=()):
    """Run assignment planning, applying persisted intent for normal reads.

    Explicit transient fixed assignments retain the existing pure planning
    contract and deliberately bypass persisted override loading.
    """
    if fixed_assignments:
        return _run_instructor_assignment_core(
            schedule,
            fixed_assignments=fixed_assignments,
        )

    from .instructor_overrides import load_and_apply_instructor_override

    return load_and_apply_instructor_override(schedule)
