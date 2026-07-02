# Operational Holding Area and Displacement Model

## Current Recommendation

Use displacement as the default behavior for operational moves into occupied
slots:

1. Remove the selected source activity or occurrence from its current operational position.
2. Place it as the only primary activity or occurrence in the target grid footprint.
3. Move every activity or occurrence previously occupying the target footprint into a separate operational holding area.
4. Keep displaced activities visible and explicitly awaiting reassignment.

The holding area is a derived, non-grid operational collection. It is not a schedule slot, time block, arrival/departure state, or part of the generated schedule matrix.

Displacement ownership is determined from the target cell before replay clears
the source. The moved source activity becomes the target's primary occupant and
must never become a holding-area item for its own move. Holding items retain
snapshots of the previous target occupant metadata.

## V1 Boundary

Persisted displacement behavior is selected explicitly with
`action_type: "displacement_move"`. Legacy records and records without an
action type remain overlap moves.

New occupied-target proposals default to displacement. Explicit `overlap_move`
remains available for legacy or exceptional workflows.

Persisted replay runs through:

```python
apply_persisted_overrides(schedule_obj, blocks)
```

Explicit displacement actions return holding records in:

```python
replay_result["holding_area"]
```

Each derived holding item includes:

```json
{
  "holding_id": "holding:override:0:0:mon_pm2:1",
  "activity_id": 123,
  "activity_name": "Archery",
  "display_value": "Archery",
  "activity_length": 1,
  "occurrence_id": "occurrence:0:mon_pm2",
  "occurrence_length": 1,
  "source_block_ids": ["0:mon_pm2"],
  "source_slot_keys": ["mon_pm2"],
  "origin_block_id": "0:mon_pm2",
  "origin_group_index": 0,
  "origin_group_label": "Example School 0",
  "origin_slot_key": "mon_pm2",
  "origin_slot_label": "PM2",
  "displaced_by_override_index": 0,
  "holding_status": "awaiting_assignment",
  "is_holding": true
}
```

Holding items intentionally have no current `slot_key`, `group_index`, or
`block_id`. Origin fields are diagnostic history, not current grid placement.
Two-block activities appear as one holding item with `occurrence_length: 2` and
the full source footprint in `source_block_ids` / `source_slot_keys`.

## Schedule Detail Supporting Workspace

The schedule detail page replays each saved action according to its explicit
action type:

1. Legacy and `overlap_move` records retain overlap behavior.
2. `displacement_move` records replace the target primary and derive holding items.

The Supporting Workspace appears when displacement actions produce holding
items. Operators can explicitly select `overlap_move` for advanced or legacy
workflows through fallback controls, but Phase 1 drag/drop defaults to
`displacement_move`.

Holding items expose a card-based reassignment workflow:

1. Drag a derived holding item card.
2. Drop it onto a valid schedule slot in the same origin group.
3. POST a holding-source payload to the existing manual move endpoint.
4. Persist a new manual move with `source_kind: "holding"`.

The older reassignment form remains available as a collapsed fallback:

1. Expand the fallback reassignment form.
2. Choose target group, target slot, and occupied-target behavior.
3. Preview the reassignment with GET parameters.
4. Confirm and save through the existing server-side POST contract.

Saved reassignment actions append new records with `source_kind: "holding"` and
`source_holding_id`. They do not mutate the earlier displacement record that
created the holding item.

Two-block holding items reassign as one occurrence. No partial reassignment is
valid.

## Validation Semantics

- Holding items remain operationally visible outside the schedule table.
- Holding items do not appear in `iter_schedule_blocks()`.
- Holding items do not participate in normal grid occupancy, time-slot, or multi-block validation.
- A displaced item should eventually produce a dedicated holding-area warning, such as `unassigned_displaced_activity`, rather than a grid conflict.
- Reassigning a holding item should validate only its proposed destination.
- A successful reassignment consumes the derived holding item so the activity does not remain visible in both holding and the grid.
- Stale holding references fail safely and leave unresolved holding items visible.
- Multi-block occurrences remain atomic. A two-block activity is never
  displaced into two holding items or reassigned one cell at a time.

## Recommended Persisted Model

Do not persist a mutable `holding_area` list as a second source of truth. Derive current grid and holding state by replaying append-only operational actions.

A future version should add stable action IDs and source reference types:

```json
{
  "version": 2,
  "operational_actions": [
    {
      "action_id": "move-uuid",
      "action_type": "move_with_displacement",
      "source": {
        "kind": "grid",
        "block_id": "0:mon_pm1",
        "activity_id": 123
      },
      "target_group_index": 0,
      "target_slot_key": "mon_pm2",
      "status": "active",
      "created_at": "2026-06-14T15:30:00Z"
    },
    {
      "action_id": "assign-uuid",
      "action_type": "assign_from_holding",
      "source": {
        "kind": "holding",
        "holding_id": "holding:move-uuid:activity-456",
        "activity_id": 456
      },
      "target_group_index": 0,
      "target_slot_key": "tue_am1",
      "status": "active",
      "created_at": "2026-06-14T15:35:00Z"
    }
  ]
}
```

Stable action IDs are preferable to v1's override-list index because indexes
become fragile if history is imported, merged, or repaired.

## Replay Implications

- Replay remains ordered and append-only.
- Occupied-target displacement removes duplicate grid occupancy immediately.
- Reassigning from holding consumes the derived holding item and places it back into the grid.
- Moving into another occupied target may create another holding item.
- Failed actions must preserve both existing grid and holding visibility and produce replay warnings.
- Multi-block activities use occurrence-level displacement and reassignment.
  If any cell of a two-block occurrence is displaced, the entire occurrence
  enters holding.

## Migration Concerns

- Existing `single_block` overlap overrides must continue replaying with overlap semantics by default.
- Do not reinterpret existing overlap history as displacement; that could unexpectedly remove activities from grid cells.
- New actions record an explicit behavior as `overlap_move` or `displacement_move`.
- A later migration may offer an operator-reviewed conversion from legacy overlaps to holding items.
- Existing source identities based on overlap block IDs remain order-dependent and should not be used as long-term holding identities.
- Stable group/action identifiers remain a future need. Current v1 identities
  are derived from generated group indexes, slot keys, occurrence ids, and
  override-list order.

## UI Implications

The schedule detail page includes a Supporting Workspace near the schedule grid:

- Show activity name, original group/slot, displacement reason, occurrence
  length, and origin group color.
- Make holding items draggable using their derived holding ID.
- Allow drag/drop assignment into a same-group grid destination.
- Keep the old preview assignment form as a fallback, not the primary workflow.
- Clearly show unassigned displaced activities when present.
- Keep holding items out of schedule table columns and conflict cell styling.
- Warn before leaving the workspace while holding items remain unassigned, but do not silently place them.

## Overlap Fallback

Retain overlap support for legacy records and explicit exceptional workflows.

## Incremental Implementation Path

1. Add clearer management controls for unresolved holding items.
2. Add stable action IDs before any import/export or history-repair workflow.
3. Add operator-managed supersession without deleting legacy overlap history.
4. Add workspace-state editing after override management is stable. Basic
   holding-source drag/drop is complete in Operational Workspace UX Phase 1.
