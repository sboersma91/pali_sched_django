"""Read-only boundaries for future instructor assignment workflows."""

from .schedule_blocks import SCHEDULE_SLOT_KEYS
from .schedule_operations import iter_schedule_blocks
from .models import InstructorScheduleAvailability


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
    for occurrence in normalized_occurrences:
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
):
    """Classify qualified instructors using operational assignment constraints."""
    eligible_instructors = []
    rejected_instructors = []

    for instructor in qualified_instructors:
        availability_result = evaluate_instructor_availability(
            occurrence,
            instructor,
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
        constraint_result = evaluate_occurrence_constraints(
            occurrence,
            qualified_instructors,
            assignments,
            availability_records,
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
