# Supporting Workspace Role

## Current Assessment

The former Operational Holding Area is now the Supporting Workspace. It is a
secondary/supporting workspace component, not the primary schedule-editing
surface.

Direct grid editing is now the primary operator workflow:

```text
drag activity within its schedule row -> POST manual move -> replay generated schedule + manual moves
```

Supporting Workspace cards use the same activity-card mental model as schedule
cards:

```text
drag displaced activity card -> drop on same-group schedule slot -> POST holding-source manual move
```

The Supporting Workspace keeps displaced activities visible when an occupied
target is replaced and provides a recovery path for assigning those activities
back into the grid.

## Current Uses

The Supporting Workspace is currently created by displacement moves:

- A `displacement_move` places the moved activity into the target grid cell.
- Any previous target occupant is removed from the grid.
- The displaced occupant appears as a derived holding item in
  `replay_result["holding_area"]`.

The Supporting Workspace is currently consumed by holding-source reassignment:

- The schedule detail view renders a panel when `holding_area_preview` has
  items.
- Operators can drag a displaced activity card onto a same-group schedule slot.
- Drag/drop submits a holding-source payload to the existing POST-only manual
  move endpoint.
- Occupied targets default to displacement: the dragged displaced activity
  takes the target slot and the previous target occupant becomes displaced.
- A collapsed fallback reassignment form remains available for non-drag use.
- Saving appends another manual move with `source_kind: "holding"` and
  `source_holding_id`.
- Replay consumes the matching derived holding item when reassignment succeeds.

The Supporting Workspace is not persisted as its own list. It is derived by
replaying manual moves.

## Actions That Require It

- Reassigning an activity that was displaced by a saved `displacement_move`.
- Preserving visibility of an occupied target's previous occupant after direct
  drag/drop replaces that target.
- Recovering from stale or failed holding reassignment records without silently
  losing the unresolved displaced item.
- Keeping two-block displaced occurrences atomic while they wait for
  reassignment.

## Actions That Benefit From It

- Dense schedule reconstruction, where one move intentionally displaces another
  activity and the operator needs to place the displaced activity afterward.
- Auditing why an activity is temporarily outside the grid; holding items retain
  origin group, origin slot, and the saved override that displaced them.
- Future staging workflows, if the application later supports intentional
  temporary removal from the grid.
- Future unscheduled-activity visibility, if incomplete generated output is
  represented as operator-placeable items.

## Actions That No Longer Require It

- Simple activity moves within the same schedule row.
- Direct operator adjustments from one time block to another.
- Previewing or saving ordinary grid-to-grid manual moves.
- Reconstructing the displayed schedule after reload; this is handled by
  `generated_schedule + manual_moves`.

## Recommendation

Option B is the best current fit: the Supporting Workspace should remain a
secondary component.

It should behave as a recovery queue for displaced activities, not as the main
manual editing workflow. The primary workflow should remain direct grid editing
through drag/drop and the server-side manual move endpoint.

The fallback reassignment form should remain available for now, but it should
stay visually secondary because card drag/drop is the approved Phase 1 operator
workflow.

## Future Evolution

Option C may become appropriate later if the workspace adds richer operations:

- an intentional "remove from grid" action
- unscheduled generated activities that need manual placement
- operator staging before committing several moves
- bulk reconstruction after stale generated output or imported operational
  history

If those workflows are added, the Supporting Workspace could evolve into a broader
"Unplaced Activities" or "Staging Queue" component. That should wait until the
workflow exists; the current implementation should stay narrow.

## Risks

- If the fallback form looks primary, operators may think they need it for every
  move even though direct drag/drop is now simpler.
- If the panel is hidden or minimized too aggressively, displaced activities may
  be missed.
- Current holding identities are derived from override order, group index, and
  slot key. Stable action IDs are still needed before import/export, history
  repair, or collaborative editing.
- The holding area does not currently model intentionally unscheduled generated
  activities. Treating it as a general backlog too early would blur its meaning.

## Implementation Note

The schedule detail panel labels holding items as displaced activities awaiting
reassignment. Items render as draggable cards that inherit their origin group's
schedule color. The collapsed reassignment form is retained as a fallback.
