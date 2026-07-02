# Manual Schedule Override Data Shape

Manual schedule overrides are stored inside the existing `TheSched.sched_data`
JSON field. This document defines the version 1 contract for the completed
Constraint-Aware Operational Schedule Editing v1 milestone.

Generated schedules are treated as operational drafts. The scheduling engine
produces a starting point; operators are expected to make manual adjustments in
the operational layer when real-world constraints require it. Operational
editing is therefore a first-class workflow, not an exception path.

```json
{
  "version": 1,
  "manual_moves": [
    {
      "source_block_id": "0:thur_am1",
      "source_activity_id": 123,
      "source_activity_name": "Archery",
      "source_occurrence_id": "occurrence:0:thur_am1",
      "source_group_index": 0,
      "source_slot_key": "thur_am1",
      "target_group_index": 1,
      "target_slot_key": "thur_pm1",
      "move_type": "single_block",
      "action_type": "displacement_move",
      "created_at": "2026-06-14T15:30:00Z",
      "status": "active"
    }
  ]
}
```

Holding-area reassignment records use the same append-only list and identify a
derived holding source instead of a grid source:

```json
{
  "source_kind": "holding",
  "source_holding_id": "holding:override:0:0:tue_am1:1",
  "source_activity_id": 456,
  "source_activity_name": "Climbing",
  "source_occurrence_id": "occurrence:0:tue_am1",
  "target_group_index": 0,
  "target_slot_key": "wed_am1",
  "source_location_id": 11,
  "source_location_name": "Original Range",
  "target_location_id": 12,
  "target_location_name": "Backup Range",
  "move_type": "occurrence",
  "action_type": "displacement_move",
  "occurrence_length": 2,
  "source_block_ids": ["0:tue_am1", "0:tue_am2"],
  "target_block_ids": ["0:wed_am1", "0:wed_am2"],
  "created_at": "2026-06-15T10:30:00Z",
  "status": "active"
}
```

## Field Contract

- `version`: schema version for the complete `sched_data` object.
- `manual_moves`: ordered list of schedule-specific operational overrides.
- `source_kind`: optional source discriminator. Missing means legacy grid source; supported values are `grid` and `holding`.
- `source_block_id`: normalized source cell identity from the regenerated base schedule.
- `source_holding_id`: derived holding-area identity for reassignment records.
- `source_activity_id`: expected `Course` primary key used for stale-write protection.
- `source_activity_name`: expected raw/display activity name used for stale-write protection.
- `source_occurrence_id`: expected normalized occurrence identity when available.
- `source_group_index` and `source_slot_key`: explicit source coordinates for diagnostics and future migration.
- `target_group_index` and `target_slot_key`: requested destination coordinates.
- `source_location_id` and `source_location_name`: optional source location snapshot for future location-aware editing.
- `target_location_id` and `target_location_name`: optional target location snapshot for future location-aware editing.
- `move_type`: supported values are `single_block` and `occurrence`. Two-block
  activities persist as occurrence moves and must replay as a whole.
- `action_type`: explicit replay behavior. Supported values are `overlap_move` and `displacement_move`.
- `occurrence_length`: optional diagnostic length for occurrence moves.
- `source_block_ids` and `target_block_ids`: optional diagnostic footprints for
  occurrence moves. The authoritative replay identity remains source identity
  plus target group/slot.
- `created_at`: UTC ISO 8601 timestamp assigned by the server when persistence is implemented.
- `status`: append-only lifecycle marker. Supported values are `active`, `superseded`, `stale`, and `failed_replay`.

Status behavior:

| Status | Replay behavior |
| --- | --- |
| `active` | Attempt replay in stored order. |
| `superseded` | Preserve for audit history; do not replay. |
| `stale` | Preserve for audit history; do not replay. |
| `failed_replay` | Preserve for audit history; do not replay. |

## Source Identity Verification

Before a move can be considered saveable, the server must regenerate the base
schedule, replay saved overrides, rebuild normalized blocks, and verify that
the submitted source identity still matches the current source occurrence.

Grid-source saves require:

- Source block exists and contains an activity.
- Source is neither empty nor unavailable.
- Source activity ID matches `source_activity_id`.
- Source raw and display activity names match `source_activity_name`.
- Source occurrence ID matches `source_occurrence_id` when submitted.
- Source group and slot match expected submitted coordinates when present.
- Multi-block sources are verified at occurrence level. A two-block occurrence
  must remain a complete adjacent occurrence and can only move to a valid
  two-cell target footprint.

Holding reassignment requires:

- Source kind is `holding`.
- The referenced `source_holding_id` exists in the derived holding area after earlier actions replay.
- Source activity ID, name, and occurrence ID match the derived holding item.
- Target group and slot are valid.
- Multi-block holding items reassign as one occurrence. No partial holding
  reassignment is valid.

## Composite Grid Identity

Operational grid coordinates always use `group_index` and `slot_key` together. A slot key such as `mon_pm1` is repeated in every schedule row and never identifies a target by itself.

- Primary normalized `block_id` values encode both coordinates, such as `1:mon_pm1`.
- Proposal forms carry expected source group and slot identity in addition to `source_block_id`.
- Proposal and replay target lookups use `(target_group_index, target_slot_key)`.
- Overlap blocks inherit the target block's group and slot coordinates.
- Displacement holding records preserve the displaced target's origin group and slot.
- Duplicate occupancy validation groups activities by `(group_index, slot_key)`, so the same time slot in different rows is valid.

Existing links or requests that omit the additional expected source group/slot fields remain compatible, but newly rendered forms include them for stale-source protection.

Any mismatch blocks save readiness with:

> This proposal cannot be saved because the generated schedule changed since selection.

## Persistence Boundary

`persist_manual_move(schedule_obj, proposal_result)` appends a verified,
saveable operational move to `manual_moves`.

The service:

- Re-evaluates save readiness before writing.
- Preserves unrelated `sched_data` keys and existing manual moves.
- Uses `normalize_sched_data_structure()` to initialize `None`, empty objects, and missing `version` or `manual_moves` keys.
- Assigns `created_at` and `status` server-side.
- Rejects malformed non-object data or non-list `manual_moves` with an operator-safe message rather than overwriting it.

`diagnose_sched_data_structure()` distinguishes uninitialized/incomplete recoverable data from malformed data requiring administrator review. `repair_malformed_sched_data()` initializes only `None`, empty-string, and whitespace-only legacy values. Populated invalid structures are never overwritten automatically. The temporary POST repair action uses PRG and exposes technical diagnostics only to staff users.

Active, supported overrides are replayed onto normalized operational blocks
during schedule detail rendering. Replay does not modify generated schedule
matrices or scheduling recursion.

Replay semantics are selected per record:

- Records without `action_type` are legacy-compatible and resolve to `overlap_move`.
- New proposals default to `displacement_move` when no explicit action is submitted.
- `overlap_move` preserves the target occupant and adds the moved activity as a visible overlap.
- `displacement_move` makes the moved activity primary and derives holding-area items from previous target occupants.
- Holding-source reassignment consumes the referenced derived holding item and places it back into the grid using the record's `action_type`.
- Unknown action types are skipped with replay warnings before operational blocks are mutated.
- `single_block` and two-block `occurrence` moves share the same replay pipeline.
  A multi-block occurrence is never replayed, displaced, held, or reassigned one
  cell at a time.

Replay runs in stored order before operational validation. Successfully replayed
blocks carry persisted-override metadata. Explicit overlap actions retain both
activities so normal duplicate-occupancy validation can report the overlap.
Displacement actions remove duplicate grid occupancy by deriving holding-area
items for the displaced target occupants. Non-active records are ignored.
Unsupported, invalid, or stale active records remain stored, are not applied,
and produce `persisted_override_replay` warnings for operator review.

## Chained Replay Semantics

Overrides replay sequentially in `manual_moves` order against the evolving normalized operational state.

- Only records with `status: active` are applied.
- `superseded`, `stale`, and `failed_replay` records remain in audit history but are excluded from replay.
- An active override whose source no longer matches is skipped with an in-memory `stale` replay status and warning.
- An active override that is malformed, unsupported, or has an invalid target is skipped with an in-memory `failed_replay` status and warning.
- Replay never persists these in-memory statuses or deletes history.
- `manual_moves` order is part of the replay contract and must remain append-only. Reordering records can invalidate chained overlap source identities.
- Holding-area state is derived during replay. A successful holding reassignment removes the item from the derived holding list; a stale reassignment leaves unresolved holding items visible.
- Occurrence state is derived during normalization and replay. A multi-block
  occurrence must remain complete; partial placement, partial displacement, or
  orphaned occurrence cells are validation errors.

Occupied targets use a visible primary activity plus nested overlap activities. Previously, moving the primary activity emptied its cell while leaving nested overlaps attached to the now-empty cell. The overlaps remained in memory but were no longer rendered, which made activities appear to disappear.

Source removal now preserves visibility:

- Moving a primary activity with overlaps promotes the first remaining overlap into the visible primary cell.
- Moving an overlap activity detaches only that overlap from its parent cell.
- If source removal cannot be completed safely, the override is skipped and the existing operational state remains visible.

The displacement and holding-area model is documented in
`docs/operational-holding-area-prototype.md`. Existing persisted records
without `action_type` continue using overlap replay by default.

## Save Endpoint Boundary

The POST-only confirmation and saved-move endpoints use Post-Redirect-Get. They regenerate the base schedule and recompute source identity verification, the move proposal, operational validation, and save readiness before redirecting to a clean GET response. Client-submitted readiness flags are ignored.
