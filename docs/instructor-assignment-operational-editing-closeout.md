# Instructor Assignment Operational Editing Closeout

## Status and milestone objective

Instructor Assignment Operational Editing is complete.

The milestone lets an operator fix one participating instructor to an existing
operational occurrence without moving or rewriting that occurrence. Automatic
planning continues to fill every non-fixed occurrence subject to the same hard
constraints, OFF feasibility, maximum-coverage objective, and bounded
continuity preference.

An operator can use the accessible form or drag an instructor handle onto a
staffed or unstaffed occurrence, retain several manual assignments, replace an
assignment by setting another instructor, return one occurrence to automatic,
or reset all instructor assignments to automatic.

## Supported operator workflow

The assignment page is
`schedule_detail/<pk>/instructor-assignments/`, implemented by
`views.instructor_assignment_schedule` and rendered by
`templates/pay_end/instructor_assignment_schedule.html`.

Supported write operations are:

- `set`: fix one instructor to one existing operational occurrence.
- Same-occurrence replacement: another `set` supersedes the prior active set.
- `reset`: remove one exactly identified active intent and return that
  occurrence to automatic planning.
- `reset_all`: deactivate all prior instructor intent and return the schedule
  to fully automatic instructor planning.

Several changes are made as several active overrides, not as a batch request.
Replacement is supported by setting another instructor. Release is supported
by reset one. Full return to automatic planning is supported by reset all.

## Explicitly unsupported operations

This milestone does not provide dedicated instructor move, swap, displacement
chains, batch assignment editing, multi-instructor occurrences, stale-record
repair, history editing, undo reset, or complete-plan persistence. These are
not requirements for the current workflow and are not committed next work.

## Source of truth

The activity source of truth remains `TheSched.sched_data.generated_schedule`
plus replayed activity `manual_moves`. `TheSched.get_display_schedule_result()`
builds that operational activity view before
`instructor_assignment.extract_operational_occurrences()` extracts staffing
occurrences.

Instructor override history is stored separately in
`TheSched.sched_data.manual_instructor_overrides`, with optimistic concurrency
state in `TheSched.sched_data.instructor_override_revision`. Overrides express
operator intent; the complete calculated instructor plan is never persisted.
Every GET recalculates the current plan from current operational activities,
constraints, and applicable intent.

Instructor editing does not change activity, location, group, slot, occurrence
footprint, generated output, or activity `manual_moves`.

## Production workflow map

### Automatic page load

```text
GET instructor-assignment-schedule
→ views.instructor_assignment_schedule
→ instructor_assignment.run_instructor_assignment
→ instructor_overrides.load_and_apply_instructor_override
→ instructor_assignment._run_instructor_assignment_core
→ TheSched.get_display_schedule_result / activity override replay
→ instructor_assignment.extract_operational_occurrences
→ instructor_overrides._active_records
→ instructor_overrides._resolve_active_overrides
→ instructor_assignment.plan_instructor_assignments_with_daily_off
→ instructor_assignment_presentation.build_instructor_assignment_presentation
→ pay_end/instructor_assignment_schedule.html
```

Invalid or stale active intent is diagnosed and omitted. Valid intent is passed
to the planner as one combined fixed set. Remaining occurrences are planned
automatically, followed by final OFF selection and presentation adaptation.
GET does not persist results.

### Set instructor

The route
`schedule_detail/<pk>/instructor-assignments/set/`
(`instructor-override-set`) calls `views.instructor_override_set`, which parses
the form or JSON request and delegates all writes to
`instructor_overrides.persist_manual_instructor_override`.

The service uses `transaction.atomic()` and `select_for_update()`, verifies the
revision, extracts current operational occurrences after activity replay,
resolves current active history, validates the complete proposed fixed set,
compares current-set coverage with proposed-set coverage, and either rejects,
requests confirmation, or appends the set intent and increments the revision.
The endpoint returns a PRG redirect or a JSON summary without rerunning public
orchestration.

### Reset one

The route
`schedule_detail/<pk>/instructor-assignments/reset/`
(`instructor-override-reset`) calls `views.instructor_override_reset` and then
`instructor_overrides.reset_manual_instructor_override`.

The locked service verifies the revision, replays active history, matches the
submitted signed composite identity exactly against stored active intent,
appends a `reset` event, increments the revision, and recalculates around all
remaining intent. The stored occurrence need not still exist, so stale and
missing-instructor intent can be removed safely.

### Reset all

The confirmed route
`schedule_detail/<pk>/instructor-assignments/reset-all/`
(`instructor-override-reset-all`) calls
`views.instructor_override_reset_all` and
`instructor_overrides.reset_all_manual_instructor_overrides`.

The locked service verifies the revision, appends one `reset_all` event,
increments the revision, and calculates a fully automatic plan. With no
resettable intent it returns a no-op without writing or incrementing revision.

## Persistence structure and lifecycle

All values are JSON-safe and remain inside `TheSched.sched_data`; no dedicated
assignment or override model exists.

A `set` record includes its server UUID, action, active lifecycle status,
schedule and organization scope, composite occurrence identity, instructor ID,
timestamp, coverage comparison, and confirmation state.

Same-occurrence replacement retains the old record as `superseded` and appends
the replacement. A `reset` event identifies the exact target override UUID and
copies its occurrence identity. A `reset_all` event records its target revision
and clears all earlier active intent during ordered replay. A later set after
either reset becomes active normally; older set records cannot reactivate.

Schedule regeneration deliberately resets
`manual_instructor_overrides` to `[]` and
`instructor_override_revision` to `0` while the activity schedule is regenerated.

## Composite occurrence identity

`instructor_overrides.build_occurrence_identity()` records:

- `schedule_id`
- `organization_id`
- `occurrence_id`
- `group_index`
- `activity_id`
- ordered complete `slot_footprint`, including block ID, slot key, and position

Set validates this identity against the current operational occurrence. Reset
one matches the submitted signed snapshot to stored active intent, allowing a
stale intent to be removed without confusing it with a similar occurrence.

## Revision, locking, and organization scope

Every successful set, reset one, or effective reset all increments
`instructor_override_revision` exactly once. Every write compares the submitted
expected revision after locking the owning `TheSched` row. Conflicts write
nothing.

Endpoints obtain schedules through the authenticated user's organization.
Instructor and occurrence organization checks are server-side; browser-provided
organization identity is not authoritative.

## Constraint boundary and coverage confirmation

Fixed and automatic assignments share these hard constraints:

- schedule and organization ownership
- explicit schedule participation
- activity certification qualification
- resolved availability across the complete footprint
- one instructor per occurrence
- no overlapping footprints for one instructor
- supported `required_instructor_count == 1`
- Tuesday-through-Thursday OFF feasibility
- complete multi-slot integrity

All active fixed assignments are validated collectively before automatic search.
A hard-invalid set is rejected and cannot be confirmed.

For a hard-valid proposal, `coverage_before` is the best result using the
current valid active fixed set and `coverage_after` uses the complete proposed
set. A decrease requires explicit server-validated confirmation. Browser
coverage values are never authoritative.

## OFF, continuity, and multi-slot behavior

Every fixed footprint is reserved before automatic planning and deducted from
the instructor's eligible OFF capacity. The planner rejects a combined fixed
set that removes the final valid OFF option for Tuesday, Wednesday, or Thursday.
Final OFF reservations are selected around both fixed and automatic work.

Fixed occurrences participate in chronological group staffing history at their
actual positions. Maximum staffed-occurrence coverage remains dominant;
continuity remains deterministic bounded branch ordering rather than a new
global optimization.

A multi-slot activity is one operational occurrence. Qualification,
availability, overlap, assignment, reset presentation, and OFF accounting use
its complete footprint. The UI emits one logical reset action even though the
manual assignment is displayed in each occupied cell.

## Activity-move interaction

Activity `manual_moves` replay before occurrence extraction. Moving an activity
may make an instructor override stale because its composite identity no longer
matches. The stale instructor intent remains stored and diagnosed, but it is
not applied. Instructor set and reset operations preserve generated activity
output and activity `manual_moves`.

## Stale, invalid, and malformed intent

Stale, missing-instructor, and hard-invalid active intent remains stored and is
excluded from the applicable fixed set. Valid overrides continue to apply when
another stored intent is stale or invalid. Safely identified stale or invalid
intent receives a reset-one action.

Ambiguous or malformed lifecycle history is diagnosed and is not silently
targeted. When individual targeting is unsafe, reset all supplies the supported
clean boundary; this is removal, not stale-record repair or history editing.

## Drag-and-drop and accessible form semantics

The drag gesture remains instructor-to-occurrence. Instructor handles are drag
sources; staffed cells and unstaffed occurrence cards expose signed,
server-generated occurrence targets. JavaScript functions `submitOverride`,
`renderConfirmation`, and `messageForResult` call the existing set endpoint,
show server responses, and reload after success. The browser never calculates
or optimistically writes assignment truth.

The ordinary POST form exposes the same set operation without requiring drag.
Reset actions are semantic POST forms with labeled buttons. Reset all uses an
in-page disclosure plus an explicit submitted `confirm_reset_all` field.
Controls are keyboard accessible, and drag feedback uses an ARIA live region.

## Presentation states

`instructor_assignment_presentation.build_instructor_assignment_presentation`
adapts the calculated plan. Cells explicitly render assigned, OFF, unavailable,
or admin-time states. Assigned cells use `assignment_source == "fixed"` to show
`Manual`; other assigned cells show `Automatic`.

Reset one removes only its target's manual state after recalculation. Reset all
removes all manual states. Stale or invalid active intent is warned about
without falsely marking the displayed automatic assignment as manual.

## Query and orchestration behavior

Candidate, participation, availability, certification, and requirement inputs
are loaded in bounded queries by
`instructor_assignment._run_instructor_assignment_core`. Active instructor IDs
are bulk-resolved with `Instructor.objects.in_bulk`; there is no database query
per override.

Coverage comparison may perform current-set and proposed-set planning, but
database work is bounded by the operation rather than the number of overrides.
GET calls public orchestration once. POST services use internal recalculation
and do not recursively call public orchestration.

## Permanent test coverage

The permanent regression map is:

- Automatic assignment, qualification, availability, overlap, organization
  isolation, determinism, and no-write behavior:
  `scheduler_app/tests.py`.
- OFF feasibility, maximum coverage, multi-slot integrity, combined fixed sets,
  continuity, deterministic and incomplete-plan scalability:
  `scheduler_app/test_instructor_off_planning.py`.
- Unsupported instructor counts:
  `scheduler_app/test_required_instructor_count.py`.
- Presentation, accessible form fallback, read-only GET, one orchestration call,
  unstaffed rendering, and page organization isolation:
  `scheduler_app/test_instructor_assignment_page.py`.
- Normalization, composite identity, stale and invalid intent, append-only
  lifecycle, locking, revisions, coverage confirmation, reset services,
  regeneration clearing, activity-move preservation, and bounded replay:
  `scheduler_app/test_instructor_overrides.py`.
- HTML and JSON endpoints, organization scope, hard rejection, confirmation,
  drag adapter, server-truth reload, reset controls, and POST orchestration:
  `scheduler_app/test_instructor_override_workflow.py`.
- Activity manual-move persistence, replay, regeneration, malformed schedule
  data, and operational schedule isolation:
  `scheduler_app/tests.py`.

## Known limitations and deferred decisions

- Exactly one instructor per operational occurrence is supported.
- There is no dedicated instructor move, swap, or displacement operation.
- There is no batch assignment request or unsaved multi-change workspace.
- There is no stale-record repair or history editor.
- There is no undo-reset command; a later set creates new active intent.
- The complete automatic plan is recalculated, not persisted.
- Composite identity depends on current schedule/group/activity/footprint
  coordinates rather than a separate immutable occurrence model.

These decisions are intentionally deferred and are not committed next work.

## Definition of milestone completion

The milestone is complete because an operator can:

- assign an instructor to a staffed or unstaffed occurrence;
- retain several manual instructor assignments;
- use drag-and-drop or the accessible form;
- receive immediate hard-constraint rejection;
- explicitly confirm a hard-valid coverage reduction;
- refresh without losing accepted intent;
- distinguish automatic and manual assignments;
- remove one saved manual assignment;
- return all instructor assignments to automatic planning;
- continue using activity manual moves independently; and
- regenerate the activity schedule and deliberately clear instructor overrides.

Dedicated move and swap operations are not required for this completion.
