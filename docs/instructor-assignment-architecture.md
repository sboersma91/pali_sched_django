# Instructor Assignment Architecture

## Status

The first Instructor Assignment Constraints milestone is implemented. The
current assignment pipeline can extract operational activity occurrences,
evaluate instructor qualifications, reject qualified instructors who already
have an overlapping assignment, and select an eligible instructor
deterministically.

Instructor assignment remains separate from the Activity Scheduling Engine.
It does not move activities, modify generated schedules, or change operational
activity-editing behavior.

## Current Pipeline

```text
Activity Schedule
→ Operational Schedule
→ Occurrence Extraction
→ Qualification Evaluation
→ Constraint Evaluation
→ Assignment Strategy
→ Assignment Results
```

The pipeline is implemented in `scheduler_app/instructor_assignment.py`.

### Activity and operational schedules

`TheSched.sched_data.generated_schedule` contains the generated Activity
Schedule. `TheSched.get_display_schedule_result()` constructs the Operational
Schedule by building normalized schedule blocks and replaying persisted manual
activity moves. Instructor assignment reads that operational result; it does
not modify it.

### Occurrence extraction

`extract_operational_occurrences(schedule)` identifies the activity
occurrences in the Operational Schedule. It groups all blocks belonging to a
multi-block activity into one occurrence and returns occurrences in stable
group and slot order.

Each occurrence is an in-memory dictionary containing schedule, organization,
activity, group, and occurrence identity plus a `slot_footprint`. Each footprint
entry identifies an occupied schedule slot. Extraction does not create
instructor assignments.

### Qualification evaluation

`evaluate_occurrence_qualifications(...)` determines whether supplied
instructors satisfy an activity's certification requirements and belong to the
occurrence's organization. It receives certification data explicitly and does
not query the database.

Qualification answers whether an instructor is qualified to teach the
activity. It does not answer whether that instructor is operationally free for
this occurrence.

### Constraint evaluation

`evaluate_occurrence_constraints(...)` receives one occurrence, the already
qualified instructors, and assignment results produced earlier in the same
run. It preserves qualified-candidate order while separating candidates into:

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

The `reasons` list allows a candidate to have multiple structured rejection
reasons when independent constraints are added later. The current evaluator
calls only the overlap rule; there is no generic rule registry, protocol, or
plugin abstraction.

### Assignment strategy

`assign_occurrences_deterministically(...)` coordinates the layers. For each
occurrence it evaluates qualification, passes qualified instructors and prior
results to constraint evaluation, and selects the first eligible instructor.

Qualification sorts candidates by instructor primary key. Constraint
evaluation preserves that order, so selection remains deterministic regardless
of input candidate order. The strategy coordinates constraint evaluation but
does not contain the overlap rule itself.

### Assignment results

Each in-memory assignment result contains:

```python
{
    "occurrence": occurrence,
    "assigned_instructor": instructor_or_none,
    "status": "assigned" or "unstaffed",
    "reason": reason_or_none,
    "constraint_rejections": [...],
}
```

No assignment model or persistence layer exists. Running assignment does not
write to `TheSched`, `Instructor`, or any other database record.

An unstaffed result distinguishes two cases:

- `No qualified instructors available.` means certification or organization
  qualification rejected every candidate.
- `No eligible instructors available.` means at least one instructor was
  qualified, but operational constraints rejected every qualified candidate.

Constraint diagnostics are retained in `constraint_rejections` for the second
case.

## Current Overlap Constraint

`evaluate_instructor_assignment_overlap(...)` is the first operational
constraint rule.

Within one `TheSched`, identical slot keys represent simultaneous operational
time across every school, group, and activity occurrence. Each occurrence
currently needs exactly one instructor, and each simultaneous occurrence needs
a distinct instructor.

The rule applies these behaviors:

- An instructor cannot staff two occurrences whose `slot_footprint` values
  share one or more slot keys.
- A multi-block occurrence occupies every slot in its footprint.
- Successful assignments earlier in the same deterministic run become
  explicit context for later occurrence evaluations.
- Unstaffed results and assignments belonging to other instructors do not
  create a conflict.
- Assignments associated with another schedule do not create a conflict.
- Non-overlapping occurrences may reuse the same instructor.
- The rule is blocking; no manual instructor override currently exists.

A rejection uses the following structure:

```python
{
    "passes": False,
    "code": "overlapping_assignment",
    "message": "Instructor is already assigned during mon_pm1.",
    "severity": "blocking",
    "rule": "no_overlapping_assignments",
    "details": {
        "conflicting_occurrence_id": "occurrence:0:mon_pm1",
        "overlapping_slot_keys": ("mon_pm1",),
    },
}
```

The reason code and rule name are stable machine-readable values. The message
is intended for people, severity makes blocking behavior explicit, and
rule-specific diagnostics remain under `details`. Overlapping slot keys are
sorted for deterministic output.

If multiple earlier assignments conflict, the rule currently reports only the
first conflicting prior occurrence. This is sufficient to reject the candidate
and preserves the order of the explicit assignment context. It is not a claim
that no other conflicts exist.

## Architectural Boundaries

The implemented layers preserve these separations:

- Occurrence extraction describes what is operationally scheduled.
- Qualification evaluation checks certification and organization requirements.
- Constraint evaluation checks operational eligibility.
- Assignment strategy chooses from eligible candidates.
- Assignment results describe the in-memory outcome.

All assignment inputs and prior-result context are passed explicitly. The
qualification evaluator, overlap rule, constraint evaluator, and assignment
strategy perform no hidden database queries or writes. Occurrence extraction
reads the operational schedule through the existing read-only display path.

The implementation uses small functions and plain dictionaries consistent
with the existing codebase. It does not introduce a dataclass hierarchy,
formal rule protocol, registry, optimizer, or speculative extension system.
Adding another independent rule will require the evaluator to call it and
compose its result, but will not require changing qualification or moving rule
logic into deterministic selection.

## Current Limitations

The system does not currently support:

- Instructor availability.
- Persisted instructor assignments.
- Cross-schedule date or time conflict checking.
- Travel or transition feasibility.
- Assigned occurrence location data.
- Workload, rest, or break rules.
- Leadership coverage requirements.
- Group-continuity preferences.
- Instructor preferences.
- Activities requiring more than one instructor.
- Optimization or backtracking.
- Manual instructor-assignment overrides.
- Instructor-assignment UI, views, forms, or APIs.

These capabilities are intentionally not partially implemented. The required
operational data or confirmed product policies do not yet exist. Their eventual
semantics also differ: some may be blocking constraints, some may be warnings,
and some may be assignment preferences rather than reasons to reject a
candidate. They should not all be treated as hard prohibitions by default.

Exact-slot overlap is currently evaluated only within one in-memory assignment
run. `TheSched.timestamp_og` is a record-creation date, not an operational
calendar date, and must not be used to infer cross-schedule conflicts.

## Extension Guidance

A future instructor-assignment constraint should:

- Remain independent from qualification evaluation.
- Remain independent from deterministic candidate selection.
- Receive its occurrence, candidate, and required operational context
  explicitly.
- Return a stable machine-readable reason code.
- Return an understandable human-readable message.
- Declare severity explicitly.
- Keep rule-specific diagnostics structured.
- Avoid hidden database queries, database writes, and input mutation.
- Preserve deterministic candidate and diagnostic ordering.
- Be tested as an independent rule and through assignment integration.

Introduce additional abstraction only when multiple implemented rules reveal a
shared contract that reduces real duplication. Do not embed future constraint
logic inside `assign_occurrences_deterministically(...)`.

## Test Coverage

Focused tests in `scheduler_app/tests.py` protect:

- Operational occurrence extraction, including multi-block footprints and
  manual activity moves.
- Pure certification and organization qualification.
- Exact-slot overlap passing and rejection behavior.
- Structured, deterministic rejection diagnostics.
- Input immutability and zero-query constraint evaluation.
- Eligible-candidate order preservation.
- Sequential assignment context.
- Distinct instructors for simultaneous occurrences.
- Instructor reuse for non-overlapping occurrences.
- Qualification-specific and eligibility-specific unstaffed outcomes.
- In-memory-only assignment behavior and repeatable results.

