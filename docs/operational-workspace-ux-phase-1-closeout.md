# Operational Workspace UX Phase 1 Closeout

## Status

Operational Workspace UX Phase 1 is COMPLETE.

This phase turned generated schedule viewing into an operator-editable
workspace while preserving generated schedules as immutable generated output.

## Problem

Operators needed to adjust generated schedules directly after generation.
Earlier workflows either treated generated output as mostly static or required
technical/manual controls that did not match how operators think about
activities.

The target product model became:

```text
Generated Schedule
+ Manual Moves
= Displayed Operational Schedule
```

## Major Architectural Decisions

- Keep `TheSched.sched_data.generated_schedule` as the authoritative generated
  output.
- Store manual operator changes separately in
  `TheSched.sched_data.manual_moves`.
- Reconstruct the displayed operational schedule by replaying manual moves onto
  normalized copies of generated schedule blocks.
- Keep the scheduler/generator untouched during manual editing.
- Use a single POST-only manual move endpoint for both grid-source moves and
  Supporting Workspace holding-source moves.
- Treat client drag/drop state as non-authoritative; the server recomputes
  source identity, target footprint, ownership, validation, and save readiness.
- Keep manual moves append-only for Phase 1; do not implement undo/redo or
  version history.

## Major UX Decisions

- The schedule grid is the primary editing surface.
- Activities render as draggable cards.
- Manual moves stay within the same school/group schedule row; cross-row moves
  are rejected.
- Multi-block activities are treated as one occurrence. The first block is the
  draggable object and the following block renders as a continuation.
- Occupied targets default to displacement: the moved activity takes the target
  slot and the previous occupant becomes displaced.
- The former Operational Holding Area is now the Supporting Workspace.
- Displaced activities render as draggable cards using the same group color
  identity as the schedule row they came from.
- The legacy Supporting Workspace reassignment form remains as a collapsed
  fallback, not the primary workflow.

## Final Delivered Capability

Operators can:

1. Open a generated schedule.
2. Drag a scheduled activity card within its own school/group row.
3. Drop it onto another valid time block.
4. Persist the move without regenerating the schedule.
5. Replace an occupied target and see the previous occupant appear as a
   displaced activity in the Supporting Workspace.
6. Drag a displaced activity card from the Supporting Workspace back into the
   schedule.
7. Refresh the page and see the displayed operational schedule reconstructed
   from generated output plus manual moves.

## Supporting Workspace Role

The Supporting Workspace is a secondary recovery queue for displaced
activities. It is not persisted as a separate list. It is derived during replay
from displacement moves in `manual_moves`.

Each displaced item preserves:

- activity identity
- occurrence length
- origin group and slot
- displacement reason
- origin group color identity
- derived holding identity for reassignment

## Deferred Work

These items are intentionally deferred beyond Operational Workspace UX Phase 1:

- Preserve scroll position after operational edits.
- Multi-block drag preview.
- Drag from either half of multi-block activities.
- Overlap visualization refinement.
- Group displaced activities by origin group.
- Location reassignment.
- Conflict detection improvements.
- Supporting Workspace future evolution.
- Stable action IDs for operational history, import/export, and repair.
- Stable generated activity-group identifiers.
- Operator-facing manual move history management.
- Explicit deactivate/supersede controls for saved manual moves.

## Documentation Cleanup Recommendations

Keep:

- `docs/operational-workspace-ux-phase-1-closeout.md` as the Phase 1 product
  and roadmap closeout.
- `docs/operational-workspace-manual-edit-persistence.md` as the current
  persistence and application-flow overview.
- `docs/manual-schedule-overrides.md` as the detailed v1 JSON field contract.
- `docs/operational-move-severity-policy.md` as the save-readiness and conflict
  policy reference.

Merge or archive candidates:

- `docs/operational-holding-area-role.md` and
  `docs/operational-holding-area-prototype.md` now overlap. Keep the role doc
  as the product-level description and eventually merge unique displacement
  model details from the prototype doc into the persistence/override docs.
- `docs/constraint-aware-operational-editing-v1-closeout.md` is still useful as
  historical backend milestone context, but its deferred roadmap is partly
  superseded by this Phase 1 closeout. Consider archiving it under a historical
  milestones folder once a docs structure exists.

Potentially obsolete after merge:

- `docs/operational-holding-area-prototype.md` may no longer provide unique
  value after its displacement model details are consolidated.

## Next Milestone

Recommended next milestone: Operational Workspace Hardening v1.

Focus:

- improve edit recovery and history management
- preserve operator context after edits
- refine multi-block and overlap affordances
- introduce richer validation and conflict communication
- prepare for future location-aware reassignment without changing the
  generated schedule contract
