# Operational Workspace Manual Edit Persistence

## Goal

Manual schedule editing uses a base-plus-overrides model:

```text
stored generated schedule
+ ordered manual move records
= displayed operational schedule
```

The generated schedule remains the authoritative generated output. Manual edits
are stored as separate operational records and are replayed onto a copy of the
generated schedule when the schedule is displayed.

## Storage Recommendation

Store manual edits in the existing `TheSched.sched_data` JSON object under the
`manual_moves` key.

This keeps one schedule's generated output and operational overlay together
without adding a migration during the foundation phase:

```json
{
  "version": 1,
  "generated_schedule": {
    "ags": ["Example School 0"],
    "mon_pm1": ["Archery"],
    "tue_am1": ["empty"]
  },
  "manual_moves": []
}
```

`generated_schedule` and `manual_moves` are separate keys. Replay code mutates
only normalized in-memory display blocks, not the stored generated matrix.

## Manual Move Data Structure

Manual moves are append-only JSON objects. The current required identity and
target fields are:

```json
{
  "source_kind": "grid",
  "source_block_id": "0:mon_pm1",
  "source_activity_id": 123,
  "source_activity_name": "Archery",
  "source_occurrence_id": "occurrence:0:mon_pm1",
  "source_group_index": 0,
  "source_slot_key": "mon_pm1",
  "target_group_index": 0,
  "target_slot_key": "tue_am1",
  "move_type": "single_block",
  "action_type": "displacement_move",
  "occurrence_length": 1,
  "source_block_ids": ["0:mon_pm1"],
  "target_block_ids": ["0:tue_am1"],
  "created_at": "2026-06-20T12:00:00Z",
  "status": "active"
}
```

Optional location fields are reserved for future location-aware editing:

```json
{
  "source_location_id": 11,
  "source_location_name": "Original Range",
  "target_location_id": 12,
  "target_location_name": "Backup Range"
}
```

These fields allow a future drag operation or location picker to record "same
activity, same schedule ownership, different location" without changing the
generated schedule structure. Location validation and rendering are still
deferred because the generated schedule does not currently store selected
locations per activity placement.

## Application Flow

1. Generate schedule with `TheSched.generate_and_store_schedule()`.
2. Store output in `TheSched.sched_data.generated_schedule`.
3. Save future operator moves with `persist_manual_move()`.
4. Reconstruct the displayed schedule with `TheSched.get_display_schedule_result()`.
5. That method builds normalized blocks from stored generated output and calls
   `apply_persisted_overrides()` against the normalized copy.
6. Templates and future APIs should read from the displayed schedule result, not
   from `generated_schedule` directly.

## Ownership Boundary

Manual moves target a `target_group_index` within the same `TheSched` record.
There is no cross-schedule or cross-school target reference. The group row is
derived from the generated schedule's `ags` list, so drag/drop requests submit
the source and target group identity together with the slot and activity
identity.

## Future Drag-And-Drop Integration

Phase 1 drag-and-drop now submits the same fields persisted by
`persist_manual_move()`:

- `source_kind`, either `grid` or `holding`
- source block or derived holding item identity
- source activity identity
- source occurrence identity
- target group index
- target slot key
- optional target location identity
- action type, usually `displacement_move`

Grid-source drags use `source_block_id`, `source_group_index`, and
`source_slot_key`. Supporting Workspace drags use `source_kind: "holding"` and
`source_holding_id`. Both source types post to the same POST-only
`sched-manual-move` endpoint.

The server continues to recompute the proposal from stored generated output
plus active manual moves before saving. Client-side drag state is treated as a
convenience, not as authority.

## Phase 1 Implemented Workflow

Operational Workspace UX Phase 1 implements this displayed schedule contract:

```text
TheSched.sched_data.generated_schedule
+ TheSched.sched_data.manual_moves
= displayed operational schedule
```

The generated schedule is not mutated by manual editing. The displayed schedule
is reconstructed by:

1. Loading a copy of `generated_schedule`.
2. Building normalized operational blocks.
3. Replaying active `manual_moves` in stored order.
4. Returning schedule rows, replay metadata, and derived Supporting Workspace
   holding items for the template and endpoint responses.

The current operator workflow is:

1. Drag a scheduled activity card within its own school/group row.
2. Or drag a displaced activity card from the Supporting Workspace.
3. Drop onto a valid schedule slot in the same group.
4. POST to `sched-manual-move`.
5. Server validates ownership, source identity, target footprint, and
   save-readiness.
6. Server appends a manual move.
7. The page reloads and displays the replayed operational schedule.

Occupied target slots default to `displacement_move`: the moved activity becomes
the primary target occupant and the previous occupant becomes a derived
Supporting Workspace item. Explicit overlap behavior remains supported by the
manual move data model and older proposal controls, but Phase 1 drag/drop does
not expose an overlap-selection dialog.
