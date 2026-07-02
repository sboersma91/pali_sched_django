from collections import defaultdict

from .schedule_blocks import (
    DAY_OFFSETS,
    SCHEDULE_SLOT_BLOCKS,
)


SPECIAL_GENERATION_LOCATIONS = {"Various", "Manz"}
FEASIBILITY_WARNING_UTILIZATION = 0.8


def _plural(count, singular, plural=None):
    if count == 1:
        return singular
    return plural or f"{singular}s"


def _is_daytime(slot_key):
    return "night" not in slot_key


def _paired_daytime_footprints(slot_blocks):
    available_slot_keys = {slot_key for slot_key, _slot_kind in slot_blocks}
    return [
        (slot_key, slot_key[:-1] + "2")
        for slot_key, _slot_kind in slot_blocks
        if _is_daytime(slot_key) and "1" in slot_key and slot_key[:-1] + "2" in available_slot_keys
    ]


def _eligible_locations(activity_name, class_locs_lookup, master_locs_lookup):
    configured_locs = class_locs_lookup.get(activity_name, [])
    master_locs_set = set(master_locs_lookup)
    return [
        loc
        for loc in configured_locs
        if loc in master_locs_set or loc in SPECIAL_GENERATION_LOCATIONS
    ]


def _slot_blocks_for_school(school):
    if school.arrive not in DAY_OFFSETS or school.depart not in DAY_OFFSETS:
        return []
    return list(SCHEDULE_SLOT_BLOCKS[DAY_OFFSETS[school.arrive]:DAY_OFFSETS[school.depart]])


def _activity_slot_count(activity_len, slot_blocks):
    if activity_len == 0:
        return sum(1 for slot_key, _slot_kind in slot_blocks if not _is_daytime(slot_key))
    if activity_len == 2:
        return len(_paired_daytime_footprints(slot_blocks))
    return sum(1 for slot_key, _slot_kind in slot_blocks if _is_daytime(slot_key))


def _location_capacity(eligible_locs, class_locs_lookup, location_capacity_func):
    return sum(
        location_capacity_func(loc, class_locs_lookup)
        for loc in eligible_locs
    )


def _diagnostic(diagnostic_type, severity, reason, **details):
    return {
        "type": diagnostic_type,
        "severity": severity,
        "reason": reason,
        **details,
    }


def audit_schedule_feasibility(schools, class_locs_lookup, class_len_lookup, master_locs_lookup, location_capacity_func):
    """Return pre-recursion schedule feasibility diagnostics.

    The audit is intentionally read-only. It summarizes impossible or risky
    configurations without changing the legacy recursive scheduler's behavior.
    """
    diagnostics = []
    activity_totals = defaultdict(lambda: {
        "activity": None,
        "demand": 0,
        "slot_capacity": defaultdict(int),
        "eligible_locs": set(),
        "school_names": set(),
    })
    location_pressure = defaultdict(lambda: defaultdict(lambda: {
        "demand": 0,
        "activity_names": set(),
        "school_names": set(),
        "slot_keys": set(),
        "paired_footprints": set(),
    }))

    for school in schools:
        selected_activities = list(school.subject.all())
        slot_blocks = _slot_blocks_for_school(school)
        usable_total_blocks = len(slot_blocks) * school.ag_num
        usable_daytime_blocks = sum(1 for slot_key, _slot_kind in slot_blocks if _is_daytime(slot_key)) * school.ag_num
        usable_night_blocks = sum(1 for slot_key, _slot_kind in slot_blocks if not _is_daytime(slot_key)) * school.ag_num
        paired_footprints = _paired_daytime_footprints(slot_blocks)
        usable_paired_footprints = len(paired_footprints) * school.ag_num
        total_required_blocks = 0
        daytime_required_blocks = 0
        night_required_blocks = 0
        paired_required_footprints = 0

        for activity in selected_activities:
            activity_len = class_len_lookup.get(activity.course_name, activity.course_len)
            activity_demand = school.ag_num
            if activity_len == 0:
                total_required_blocks += activity_demand
                night_required_blocks += activity_demand
            elif activity_len == 2:
                total_required_blocks += activity_demand * 2
                daytime_required_blocks += activity_demand * 2
                paired_required_footprints += activity_demand
            else:
                total_required_blocks += activity_demand
                daytime_required_blocks += activity_demand

            eligible_locs = _eligible_locations(activity.course_name, class_locs_lookup, master_locs_lookup)
            if not eligible_locs:
                diagnostics.append(_diagnostic(
                    "activity_no_eligible_locations",
                    "error",
                    (
                        f"{school.school_name} may be unschedulable: {activity.course_name} "
                        "has no eligible available scheduling Locations."
                    ),
                    school=school.school_name,
                    activity=activity.course_name,
                    demand=activity_demand,
                    capacity=0,
                ))
                continue

            slot_capacity = _activity_slot_count(activity_len, slot_blocks)
            location_capacity = _location_capacity(eligible_locs, class_locs_lookup, location_capacity_func)
            capacity = slot_capacity * location_capacity
            if capacity < activity_demand:
                diagnostics.append(_diagnostic(
                    "activity_capacity_insufficient",
                    "error",
                    (
                        f"{school.school_name} — {activity.course_name} needs {activity_demand} "
                        f"{_plural(activity_demand, 'placement')}, but only {capacity} "
                        f"{'is' if capacity == 1 else 'are'} available in this trip window."
                    ),
                    school=school.school_name,
                    activity=activity.course_name,
                    demand=activity_demand,
                    capacity=capacity,
                ))
            elif capacity and activity_demand / capacity >= FEASIBILITY_WARNING_UTILIZATION:
                diagnostics.append(_diagnostic(
                    "activity_capacity_tight",
                    "warning",
                    (
                        f"{school.school_name} has tight capacity for {activity.course_name}: "
                        f"{activity_demand} of {capacity} eligible placements are required before recursion."
                    ),
                    school=school.school_name,
                    activity=activity.course_name,
                    demand=activity_demand,
                    capacity=capacity,
                ))

            activity_total = activity_totals[activity.course_name]
            activity_total["activity"] = activity
            activity_total["demand"] += activity_demand
            activity_total["slot_capacity"][activity_len] += slot_capacity
            activity_total["eligible_locs"].update(eligible_locs)
            activity_total["school_names"].add(school.school_name)

            for loc in eligible_locs:
                loc_pressure = location_pressure[loc][activity_len]
                loc_pressure["demand"] += activity_demand
                loc_pressure["activity_names"].add(activity.course_name)
                loc_pressure["school_names"].add(school.school_name)
                if activity_len == 0:
                    loc_pressure["slot_keys"].update(
                        slot_key for slot_key, _slot_kind in slot_blocks if not _is_daytime(slot_key)
                    )
                elif activity_len == 2:
                    loc_pressure["paired_footprints"].update(paired_footprints)
                else:
                    loc_pressure["slot_keys"].update(
                        slot_key for slot_key, _slot_kind in slot_blocks if _is_daytime(slot_key)
                    )

        if total_required_blocks > usable_total_blocks:
            diagnostics.append(_diagnostic(
                "school_trip_window_capacity_insufficient",
                "error",
                (
                    f"{school.school_name} may be unschedulable: requires {total_required_blocks} total "
                    f"activity {_plural(total_required_blocks, 'block')} but only {usable_total_blocks} usable "
                    "schedule blocks exist in its trip window."
                ),
                school=school.school_name,
                demand=total_required_blocks,
                capacity=usable_total_blocks,
            ))

        if night_required_blocks > usable_night_blocks:
            diagnostics.append(_diagnostic(
                "school_night_capacity_insufficient",
                "error",
                (
                    f"{school.school_name} may be unschedulable: requires {night_required_blocks} night "
                    f"activity {_plural(night_required_blocks, 'placement')} but only {usable_night_blocks} "
                    "usable night slots exist."
                ),
                school=school.school_name,
                demand=night_required_blocks,
                capacity=usable_night_blocks,
            ))

        if paired_required_footprints > usable_paired_footprints:
            diagnostics.append(_diagnostic(
                "school_two_block_footprint_capacity_insufficient",
                "error",
                (
                    f"{school.school_name} may be unschedulable: requires {paired_required_footprints} "
                    f"two-block paired {_plural(paired_required_footprints, 'footprint')} but only "
                    f"{usable_paired_footprints} usable paired footprints exist."
                ),
                school=school.school_name,
                demand=paired_required_footprints,
                capacity=usable_paired_footprints,
            ))

        if daytime_required_blocks > usable_daytime_blocks:
            diagnostics.append(_diagnostic(
                "school_daytime_capacity_insufficient",
                "error",
                (
                    f"{school.school_name} may be unschedulable: requires {daytime_required_blocks} daytime "
                    f"activity {_plural(daytime_required_blocks, 'placement')} but only {usable_daytime_blocks} "
                    "usable daytime slots exist."
                ),
                school=school.school_name,
                demand=daytime_required_blocks,
                capacity=usable_daytime_blocks,
            ))

    for activity_name, total in activity_totals.items():
        if len(total["school_names"]) == 1:
            continue
        activity = total["activity"]
        eligible_locs = total["eligible_locs"]
        location_capacity = _location_capacity(eligible_locs, class_locs_lookup, location_capacity_func)
        slot_capacity = total["slot_capacity"][activity.course_len]
        capacity = slot_capacity * location_capacity
        demand = total["demand"]
        if capacity < demand:
            diagnostics.append(_diagnostic(
                "activity_total_capacity_insufficient",
                "error",
                (
                    f"{activity_name} may be unschedulable across selected Schools: requires {demand} "
                    f"{_plural(demand, 'placement')} but only {capacity} eligible placements exist."
                ),
                activity=activity_name,
                schools=", ".join(sorted(total["school_names"])),
                demand=demand,
                capacity=capacity,
            ))
        elif capacity and demand / capacity >= FEASIBILITY_WARNING_UTILIZATION:
            diagnostics.append(_diagnostic(
                "activity_total_capacity_tight",
                "warning",
                (
                    f"{activity_name} is a bottleneck risk across selected Schools: {demand} of {capacity} "
                    "eligible placements are required."
                ),
                activity=activity_name,
                schools=", ".join(sorted(total["school_names"])),
                demand=demand,
                capacity=capacity,
            ))

    for loc, pressure_by_length in location_pressure.items():
        for activity_len, pressure in pressure_by_length.items():
            if activity_len == 2:
                slot_count = len(pressure["paired_footprints"])
                footprint_word = "paired footprint"
            else:
                slot_count = len(pressure["slot_keys"])
                footprint_word = "slot"
            capacity = slot_count * location_capacity_func(loc, class_locs_lookup)
            demand = pressure["demand"]
            if demand <= capacity or len(pressure["activity_names"]) == 1:
                continue
            diagnostics.append(_diagnostic(
                "location_bottleneck_insufficient",
                "error",
                (
                    f"{loc} may be unschedulable: {demand} requested placements across "
                    f"{len(pressure['activity_names'])} {_plural(len(pressure['activity_names']), 'Activity', 'Activities')} "
                    f"compete for {capacity} available location {_plural(capacity, footprint_word)}."
                ),
                location=loc,
                activities=", ".join(sorted(pressure["activity_names"])),
                schools=", ".join(sorted(pressure["school_names"])),
                demand=demand,
                capacity=capacity,
            ))

    errors = [diagnostic for diagnostic in diagnostics if diagnostic["severity"] == "error"]
    warnings = [diagnostic for diagnostic in diagnostics if diagnostic["severity"] == "warning"]
    info = [diagnostic for diagnostic in diagnostics if diagnostic["severity"] == "info"]
    return {
        "diagnostics": diagnostics,
        "errors": errors,
        "warnings": warnings,
        "info": info,
        "blocks_generation": bool(errors),
    }
