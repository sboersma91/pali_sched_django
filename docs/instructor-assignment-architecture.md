# Instructor Assignment Architecture

> **Documentation scope:** This document describes the original automatic
> assignment, participation, availability, OFF, and continuity foundation.
> Manual operational editing was implemented later. For the current production
> workflow and persistence contract, see
> `docs/instructor-assignment-operational-editing-closeout.md`.

## Status

The Instructor Availability foundation, schedule-participation foundation,
and first operator availability workflow are implemented. The application can
extract operational activity occurrences, build an explicit schedule staffing
pool, evaluate certification and organization qualification, resolve detailed
schedule-slot exceptions, reject same-slot assignment overlap, and select
eligible instructors with deterministic maximum-coverage planning. Among
equally staffed valid plans, the planner prefers instructor continuity for each
operational activity group.

Instructor assignment remains separate from the Activity Scheduling Engine.
It does not move activities, modify generated schedules, or persist instructor
assignments. Assignment results remain in memory.

## Current Staffing Invariant

The supported release assigns exactly one instructor to each activity-group
occurrence. Separate groups attending the same activity at the same time remain
separate occurrences and each needs its own group instructor. Multi-slot
activities remain one occurrence with one instructor across the complete slot
footprint, subject to daily OFF planning.

`Course.required_instructor_count` is dormant future groundwork retained for
migration compatibility and normalized occurrence data. Supported Course data
must keep this value at `1`. Normal Course forms, pages, and admin editing do
not expose it, and production assignment orchestration fails closed if another
value is encountered.

Location capacity and instructor demand are separate concepts. Location
capacity controls how many groups may attend simultaneously; it does not create
shared staffing positions or change the one-instructor-per-group-occurrence
rule.

## Current Pipeline

```text
Activity Schedule
→ Operational Schedule
→ Occurrence Extraction
→ Explicit Schedule Participation
→ Qualification Evaluation
→ Resolved Availability Constraint Evaluation
→ Same-Slot Assignment Overlap Evaluation
→ Daily OFF Feasibility
→ Maximum-Coverage and Group-Continuity Planning
→ In-Memory Assignment Results
```

The assignment pipeline is implemented in
`scheduler_app/instructor_assignment.py`. Availability storage and operator
editing use `scheduler_app/models.py`, `scheduler_app/instructor_availability.py`,
and the schedule-specific availability view.

`run_instructor_assignment(schedule)` is the bounded orchestration entry point.
It starts from one `TheSched`, loads only that schedule's organization-owned
participants, certifications, course requirements, and detailed availability,
rejects inconsistent extracted occurrence scope, and returns an in-memory
result. It does not persist assignment output.

### Activity and operational schedules

`TheSched.sched_data.generated_schedule` contains the generated Activity
Schedule. `TheSched.get_display_schedule_result()` constructs the Operational
Schedule by building normalized schedule blocks and replaying persisted manual
activity moves. Instructor assignment reads this operational result without
modifying it.

For instructor availability, one `TheSched` represents one operational week.
This is an operational schedule identity, not a claim that the model contains
calendar dates.

### Occurrence extraction

`extract_operational_occurrences(schedule)` returns one normalized in-memory
record per activity occurrence. Multi-slot activity blocks are grouped into one
occurrence with a complete `slot_footprint`, and occurrences are returned in
stable group and canonical-slot order.

Each occurrence contains schedule, organization, activity, group, and
occurrence identity. Each footprint entry contains its block ID, canonical slot
key, display label, and position.

### Qualification evaluation

`evaluate_occurrence_qualifications(...)` determines whether supplied
instructors satisfy all activity certification requirements and belong to the
occurrence organization. Certification inputs are explicit; the evaluator does
not query the database.

Qualification answers whether an instructor may lead the activity. It remains
independent from availability, overlap, and deterministic selection.

### Constraint evaluation

`evaluate_occurrence_constraints(...)` receives an occurrence, already
qualified instructors, earlier assignment results, and a materialized
availability context. It preserves qualified-candidate order and returns:

```python
{
    "eligible_instructors": [...],
    "rejected_instructors": [
        {
            "instructor": instructor,
            "reasons": [...],
        },
    ],
    "warnings": [],
}
```

Blocking constraints run in this deterministic order:

1. Resolved schedule participation and schedule-slot availability
2. Same-slot existing-assignment overlap

The evaluator currently preserves first-blocking-failure behavior for each
candidate. If availability fails, overlap is not evaluated for that candidate.
If availability passes, overlap may still reject the candidate. The two rules
retain separate identities and diagnostics.

### Deterministic assignment strategies

`assign_occurrences_deterministically(...)` coordinates the layers. For each
occurrence it evaluates qualification, passes qualified instructors into the
constraint layer, and selects the first eligible instructor.

Qualification sorts candidates by instructor primary key. Constraint
evaluation preserves that order, so selection remains deterministic regardless
of candidate input order. Availability policy and overlap logic are not
embedded in candidate selection.

Production orchestration uses
`plan_instructor_assignments_with_daily_off(...)`, a deterministic backtracking
planner. Qualification, participation, resolved availability, same-slot
overlap, organization scope, and Tuesday-through-Thursday OFF capacity remain
hard feasibility rules. The planner first maximizes the number of staffed
operational occurrences. It never trades staffing coverage for continuity.

Continuity is a deterministic candidate branch-ordering preference, not a
global optimization across every maximum-coverage plan. For each
`(schedule_id, group_index)`, branch-local state records the last staffed
instructor and a possible pre-interruption instructor to return to. A valid
pending return is tried first, followed by the current instructor and then
other valid candidates in instructor-primary-key order.

The pending return is retained only while that instructor is hard-invalid for
intervening staffed occurrences. Qualification, resolved availability, OFF
protection, or overlap with another partial-plan assignment may make the
instructor invalid; the state does not assign a unique reason. It clears when
the instructor returns or when a valid pending or current instructor is
bypassed. Unstaffed occurrences do not alter the state. Continuity spans day
boundaries, while multi-slot activities remain one occurrence with one
instructor across their unchanged complete footprint.

The search still backtracks to find exact maximum coverage, but it retains the
first plan at a given coverage and prunes branches whose optimistic coverage
cannot exceed that result. It therefore does not promise the mathematically
minimum possible handoff or interruption tuple. This bounded behavior prevents
combinatorial enumeration of equal-coverage plans when many instructors are
interchangeable. The preference does not create a primary instructor or persist
an instructor/group relationship.

Callers performing bounded diagnostics may supply a private per-call statistics
mapping for explored-node and completed-plan counts. These statistics are
optional diagnostic state only: they are not part of assignment results,
persistence, templates, views, or APIs.

### Assignment results

Each result remains an in-memory dictionary:

```python
{
    "occurrence": occurrence,
    "assigned_instructor": instructor_or_none,
    "status": "assigned" or "unstaffed",
    "reason": reason_or_none,
    "constraint_rejections": [...],
}
```

`No qualified instructors available.` means qualification rejected every
candidate. `No eligible instructors available.` means at least one instructor
was qualified but a blocking constraint rejected every qualified candidate.
Structured constraint diagnostics are retained in the latter result.

No assignment model or assignment persistence layer exists.

## Schedule Participation and Availability Semantics

Availability follows these rules:

- One `TheSched` represents one operational week.
- `InstructorScheduleParticipation` records the primary staffing-pool decision
  for one instructor and schedule.
- Participation is opt-out. No participation record means the instructor is
  participating and belongs to the assignment candidate pool.
- An explicit `participating` record remains compatible and includes the
  instructor, while an explicit `not_participating` record excludes them.
- Default participation is canonically stored as no participation row; saving
  Participating removes an existing opt-out rather than creating a default row.
- A participant is available for every canonical schedule slot by default and
  does not need one `available` row per slot.
- A non-participant is excluded from assignment.
- Availability belongs to one organization, instructor, schedule, and
  canonical slot.
- Identical slot keys in different schedules do not share availability.
- An instructor may be available and assigned in the same canonical slot in
  different schedules because those schedules represent different operational
  weeks.
- Detailed availability is recorded per individual canonical slot.
- For a participant, no detailed slot row means Available.
- An explicit `unavailable` row is a restrictive exception that blocks that slot.
- An explicit `available` row confirms the baseline and remains supported for
  backward compatibility, but is not normally necessary.
- Duplicate or invalid detailed records fail closed.
- A multi-slot occurrence is available only when every slot in its complete
  footprint resolves as available.
- Once assigned to a multi-slot occurrence, the overlap rule treats the
  instructor as occupied across every footprint slot.
- Availability survives schedule generation and regeneration because it is
  attached to the schedule and canonical slots, not to generated activity
  assignments.
- Deleting an instructor or schedule cascades to its availability rows.

Manual instructor-assignment overrides and splitting a multi-slot instructor
assignment are not implemented.

Availability does not represent real calendar dates, recurring weekly
patterns, or cross-schedule simultaneity. `TheSched.timestamp_og` remains
record-creation metadata and is not an operational date.

## Participation and Availability Models

`InstructorScheduleParticipation` is the schedule-specific staffing-pool
record. It contains organization, instructor, schedule, and state; enforces one
record per instructor and schedule; validates matching ownership; cascades with
instructor or schedule deletion; and protects its organization. The migration
does not infer participation from existing detailed availability rows.

`InstructorScheduleAvailability` is the normalized availability record. Its
minimum fields are:

- `organization`
- `instructor`
- `schedule`
- `slot_key`
- `state`

The model enforces or validates:

- `slot_key` belongs to the canonical schedule-slot vocabulary.
- `state` is explicitly `available` or `unavailable`.
- Instructor and schedule ownership match the availability organization.
- Only one row exists for an instructor, schedule, and slot key.
- Instructor and schedule deletion cascade to availability rows.
- Organization deletion is protected while owned availability exists.

For the existing matrix, missing detailed availability remains absence of a
row. In production assignment resolution, that absence inherits the
instructor's available baseline. The low-level raw evaluator retains its
fail-closed missing-record behavior only for unresolved and legacy contexts.

## Pure Availability Evaluation and Diagnostics

`evaluate_instructor_availability(...)` remains the low-level evaluator for
explicit per-slot records. `evaluate_resolved_instructor_availability(...)`
resolves opt-out participation, supplies the available baseline, and then
delegates detailed validation to the low-level evaluator.

The evaluator:

- Performs no database queries or writes.
- Does not mutate the occurrence, instructor, or supplied context.
- Considers only matching organization, instructor, schedule, and footprint
  slots.
- Evaluates every slot in the occurrence footprint.
- Preserves failed slots in occurrence order.
- Passes resolved participant availability when every required slot is either
  covered by the participation baseline or has one explicit `available` row.
- Blocks an explicit `unavailable` override.
- Fails closed on malformed relevant state or duplicate relevant records.

Stable failed-slot reason codes are:

- `missing_availability`
- `explicitly_unavailable`
- `duplicate_availability`
- `invalid_availability_state`

Mixed failure types use the top-level code
`availability_requirements_not_met`, while each failed slot retains its
specific reason code.

A blocking result follows the existing constraint shape:

```python
{
    "passes": False,
    "code": "missing_availability",
    "message": "Instructor is not explicitly available for mon_pm1.",
    "severity": "blocking",
    "rule": "explicit_schedule_slot_availability",
    "details": {
        "failed_slots": (
            {
                "slot_key": "mon_pm1",
                "slot_label": "PM1",
                "reason_code": "missing_availability",
            },
        ),
    },
}
```

Malformed context never authorizes an assignment.

## Availability Preloading

`preload_instructor_availability(...)` performs the database boundary for one
assignment run. It is explicitly scoped to:

- One organization
- One `TheSched`
- The relevant candidate instructor IDs

It performs one bounded query and returns a fully materialized tuple ordered by
instructor, slot, and record primary key. Candidate and occurrence evaluation
then uses this tuple without N+1 database access. The pure evaluator never
queries `InstructorScheduleAvailability` itself.

## Same-Slot Assignment Overlap

`evaluate_instructor_assignment_overlap(...)` prevents an instructor from
staffing two occurrences in the same schedule when their footprints share one
or more canonical slot keys.

Within one schedule, an identical slot key means simultaneous operational time
across schools and groups. The rule:

- Checks the complete footprint of multi-slot occurrences.
- Uses successful assignments earlier in the same deterministic run.
- Ignores unstaffed results and assignments for other instructors.
- Ignores assignments associated with another schedule.
- Allows instructor reuse in non-overlapping slots.
- Returns blocking `overlapping_assignment` diagnostics.

If multiple earlier assignments conflict, the rule reports the first conflict
from the explicit assignment context. This is sufficient for first-failure
candidate rejection and is not a claim that no additional conflicts exist.

## Operator Participation and Availability Workflows

The primary schedule-specific staffing workflow is **Instructor
Participation**. From one schedule, an authenticated organization member sees
every organization-owned instructor and chooses one state:

- **Participating**
- **Not participating**

The complete form is validated and applied atomically. All organization
instructors participate by default; Not participating is an explicit opt-out.
The workflow uses POST/Redirect/GET and is scoped to the authenticated user's
organization.

The separate **Detailed Instructor Availability** workflow shows:

- Organization-owned instructors as rows
- Canonical schedule slots as columns in canonical order
- One displayed state per cell: **Available** or **Unavailable**

This detailed page answers:

> Does this participant have an exception or explicit confirmation in a
> particular schedule slot?

It is separate from the read-only instructor-assignment schedule.

The matrix remains a detailed exception editor. Participation supplies the
broad available baseline; an explicit unavailable row restricts a slot, and an
explicit available row remains a supported confirmation.

The current page uses standard server-rendered form submission:

- Operators may submit multiple cell changes together.
- **Available** deletes a restrictive exception and returns the cell to its
  inherited available baseline.
- **Unavailable** creates or updates an explicit restrictive row.
- Duplicate or invalid submitted cells reject the complete batch.
- Writes use one database transaction, so invalid input leaves no partial
  changes.
- Successful writes use POST/Redirect/GET and display feedback.
- Instructors and schedules from other organizations cannot be displayed or
  submitted successfully.
- No JavaScript or API is required.

The matrix read service uses bounded instructor and availability queries and
does not create rows for inherited Available cells. Existing explicit Available
rows remain readable and normalize back to the inherited default when saved.

## Read-Only Instructor Assignment Schedule

The schedule-specific **Instructor Assignment Schedule** is the first
operator-facing assignment display. It:

- Recomputes from current activity placement, participation, qualifications,
  and detailed availability every time the page is opened or refreshed.
- Remains read-only and does not persist assignment results.
- Displays default and explicitly participating instructors as rows and every
  canonical schedule slot as a column.
- Gives participants with no assignments a complete empty row.
- Places instructors with assigned work first and complete empty rows last,
  preserving deterministic instructor order within both sections.
- Repeats one multi-slot occurrence visually in every occupied cell while
  retaining one shared occurrence identity.
- Derives each activity-group accent from its schedule-local `group_index`,
  using the same four-color mapping as the activity schedule. The palette
  repeats after four groups and is visual presentation data, not persisted
  business data.
- Displays unstaffed occurrences separately and prominently.
- Adapts structured candidate rejections into expandable operator-safe details
  without exposing raw model objects or internal diagnostic dictionaries.

This is a current planning view, not a historical staffing snapshot. Manual
assignment editing and saved snapshots remain deferred. Maximum-coverage and
group-continuity planning are recomputed whenever the page is loaded.

## Supporting Instructor Management

The regular application provides an organization-scoped instructor list,
name-only create and update forms, delete confirmation, and navigation.

The regular form exposes only first and last name. Legacy `cpr`, `firstaid`,
`ropes_lead`, and `school_lead` fields were made nullable so name-only creation
does not fabricate unknown legacy information. These fields are not exposed in
regular CRUD. Certifications and leadership roles remain separate concepts and
future workflows.

Deleting an instructor uses existing cascade behavior to remove that
instructor's certification relationship rows, leadership-role relationship
rows, and schedule availability. Certification and leadership-role definitions
remain.

## Architectural Boundaries

The implemented layers remain separate:

- Occurrence extraction describes operational activity placement.
- Schedule participation defines the assignment candidate pool.
- Qualification checks certification and organization requirements.
- Resolved availability applies the participation baseline and detailed slot
  exceptions.
- Overlap checks earlier in-memory assignments in the same schedule.
- Daily OFF capacity remains a hard feasibility rule.
- Deterministic backtracking finds exact maximum staffed-occurrence coverage,
  with group continuity and stable primary-key ordering guiding branch order.
- Assignment results remain in memory.

The Activity Scheduling Engine does not query instructor availability and does
not persist instructor assignments. Availability editing does not move
activities or change operational activity-editing behavior.

## Explicitly Deferred

The system does not currently support:

- Persisted instructor assignments
- Manual instructor-assignment editing
- Manual assignment overrides
- Breaking apart multi-slot instructor assignments
- Instructor self-service
- Master date-range availability
- Recurring availability
- Operational calendar dates
- Cross-schedule overlap detection
- Travel or transition feasibility
- Workload rules
- Rest and break rules
- Instructor preferences
- Leadership coverage requirements
- Multi-instructor activity staffing requirements
- Shared leads or shared staff across simultaneous occurrences
- Staffing ratios or pooled staffing by location
- Setup staffing and role-specific staffing positions
- Payroll or timekeeping
- Availability history or audit trail
- Historical instructor-assignment snapshots
- Copying availability between schedules
- Availability or assignment APIs
- Asynchronous availability editing

These deferred capabilities must not be inferred from the existing canonical
slot keys or record-creation timestamps.

## Extension Guidance

A future instructor-assignment constraint should:

- Remain independent from qualification and deterministic selection.
- Receive occurrence, candidate, and operational context explicitly.
- Return stable machine-readable codes and human-readable messages.
- Declare severity explicitly.
- Keep rule-specific diagnostics structured.
- Avoid hidden database queries, writes, and input mutation.
- Preserve deterministic candidate and diagnostic ordering.
- Be tested independently and through assignment integration.

Additional abstraction should be introduced only when implemented constraints
show a shared contract that reduces real duplication.

## Test Coverage

Focused tests in `scheduler_app/tests.py` protect:

- Operational occurrence extraction and manual activity move reflection
- Certification and organization qualification
- Availability model validation and organization isolation
- Pure availability evaluation, failed-slot diagnostics, and input immutability
- One-query availability preloading and zero-query evaluation
- Availability-before-overlap constraint ordering
- Multi-slot availability and overlap footprints
- Deterministic selection and structured unstaffed results
- Atomic availability matrix reads and writes
- Operator availability access and organization isolation
- Name-only instructor CRUD, navigation, and deletion cascades
- In-memory-only assignment behavior

Focused tests in `scheduler_app/test_instructor_off_planning.py` additionally
protect maximum coverage, daily OFF feasibility, continuity across unavoidable
qualification, availability, OFF, and overlap interruptions, unstaffed-gap and
multi-slot semantics, duplicate-label isolation through `group_index`, stable
tie resolution, read-only recomputation, and bounded private search
instrumentation.

Django's test runner creates and destroys a separate test database. Results do
not depend on the contents or migration state of a developer's ignored
`db.sqlite3`.
