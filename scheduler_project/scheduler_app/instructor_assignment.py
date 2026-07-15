"""Read-only boundaries for future instructor assignment workflows."""

from .schedule_blocks import SCHEDULE_SLOT_KEYS
from .schedule_operations import iter_schedule_blocks


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


def assign_occurrences_deterministically(
    occurrences,
    candidate_instructors,
    instructor_certifications,
    course_requirements,
):
    """Assign the first qualified instructor to each supplied occurrence."""
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
        assigned_instructor = qualified_instructors[0] if qualified_instructors else None

        assignments.append({
            "occurrence": occurrence,
            "assigned_instructor": assigned_instructor,
            "status": "assigned" if assigned_instructor else "unstaffed",
            "reason": None if assigned_instructor else "No qualified instructors available.",
        })

    return assignments
