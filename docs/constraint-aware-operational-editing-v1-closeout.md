# Constraint-Aware Operational Schedule Editing v1 Closeout

## Status

Constraint-Aware Operational Schedule Editing v1 is complete.

The milestone delivered the first persisted operational editing layer while
preserving the scheduling engine as protected legacy/core logic.

## Product Philosophy

Generated schedules are operational drafts. The generator creates a useful
starting point, but operators are expected to make manual adjustments when
real-world constraints, preferences, staffing, or activity availability require
it.

Operational editing is now a first-class workflow:

- The schedule grid is rendered from generated output plus saved operational
  overrides.
- Manual edits are persisted as append-only operational records.
- Saved edits replay after page reload.
- Conflicts are visible rather than silently repaired.
- Operators can use displacement and holding-area reassignment to resolve dense
  schedule changes intentionally.

## Delivered Capabilities

- Read-only operational block normalization.
- Activity-block selection with metadata display.
- Occurrence grouping for two-block activities.
- Occurrence-aware highlighting and selection.
- In-memory move proposal previews.
- Server-side POST confirmation with PRG redirects.
- Save-readiness policy enforcement.
- Source identity verification for stale-write protection.
- Persisted manual overrides in `TheSched.sched_data.manual_moves`.
- Replay of persisted overrides during schedule rendering.
- Explicit `action_type` semantics:
  - `overlap_move`
  - `displacement_move`
- Displacement is the default for new occupied-target moves.
- Derived non-grid holding area for displaced activities.
- Holding-area reassignment back into the grid.
- Append-only operational history.
- Safe malformed legacy `sched_data` handling and explicit repair path.
- Single-block activity moves.
- Two-block occurrence moves.
- Two-block occurrence displacement and holding.
- Validation and conflict visibility after normalization, proposals, replay, and
  reassignment.

## Architectural Decisions

- Do not modify `TheSched.create_sched` recursion or assignment logic.
- Treat generated schedules as a base projection.
- Build operational behavior in `scheduler_app.schedule_operations`.
- Store manual edits in existing `TheSched.sched_data`.
- Replay saved operations onto normalized blocks rather than mutating generated
  matrices.
- Use append-only history; do not rewrite or delete prior operational records
  automatically.
- Use occurrence-level editing for multi-block activities.
- Keep overlap compatibility for legacy records while making displacement the
  default operator workflow.

## Completed Roadmap Items

- Persisted operational overrides.
- Displacement-based occupied-target moves.
- Holding-area panel and reassignment workflow.
- Occurrence-based editing for two-block activities.
- Replay-safe stale override handling.
- Conflict summary and selected-block conflict details.
- PRG workflow for confirmation/save actions.
- Operator-facing cleanup of internal school-form scheduling fields.

## Deferred Items

- Drag/drop editing.
- Multi-move unsaved workspace state.
- Override history management UI.
- Explicit override deactivate/supersede controls.
- Workspace reset/regenerate controls.
- Consequence forecasting before a move.
- Holding-area filtering/grouping.
- Dedicated navigation redesign.
- Stable action IDs.
- Stable generated activity-group identifiers.
- Location-aware validation based on actual engine-selected locations.

## Known Technical Debt

`schedule_operations.py` now owns normalization, occurrence helpers, proposals,
replay, persistence, holding-area behavior, save policy, malformed-data handling,
and validation. This is acceptable for the v1 milestone, but it should be split
before adding substantially richer workspace behavior.

Likely future modules:

- `schedule_occurrences.py`
- `schedule_proposals.py`
- `schedule_replay.py`
- `schedule_validation.py`
- `schedule_persistence.py`

Current operational identities still depend on generated `group_index`, slot
keys, occurrence ids, and override-list order. Stable group/action identifiers
should be introduced before import/export, collaborative editing, or advanced
history repair.

## Recommended Next Milestone

Next milestone: Operational Workspace Management v1.

Objectives:

- Give operators better control over saved operational history.
- Reduce recovery friction when an override becomes stale or unwanted.
- Improve holding-area clarity before drag/drop.
- Prepare the schedule detail page for a more focused editing workspace.

Success criteria:

- Operators can deactivate or supersede saved overrides without database access.
- Operators can understand which saved action created a visible grid or holding
  state.
- Holding items show clear origin and reassignment status.
- Bad operational edits can be recovered without deleting history.
- Full Django test suite remains passing.
- The scheduling engine recursion remains untouched.
