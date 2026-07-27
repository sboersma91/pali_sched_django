# Canonical Demo Presenter Walkthrough

Use this walkthrough only after an operator has completed and verified the
[Canonical Demo Environment Operator Runbook](demo-environment-runbook.md).

## Before the audience arrives

1. Log in at `/login/` using the normal demo operator account.
2. Open **Schedules → Demo Program Week**.
3. Confirm the generated workspace shows four groups and no persisted or
   proposed moves.
4. Open **View Instructor Assignment Schedule** and confirm current staffing
   shows no unstaffed occurrences or saved manual assignments.
5. Return to the schedule detail page.

Generated placements are produced by the current scheduler. Do not memorize
database IDs, occurrence IDs, or fixed source and destination slots.

## Product story

### 1. Establish the operational foundation

From the navigation bar, briefly open:

- **Locations**: point out Demo Commons, Demo Field, Demo Studio, and Demo
  Workshop.
- **Activities**: point out one-block, two-block, and evening activity shapes.
- **Schools**: show Demo Cohort North and Demo Cohort South, each with two
  activity groups.
- **Instructors**: show the five fictional instructors.

Keep this short. The story is that schedule generation starts from explicit
operational records rather than disconnected sample cells.

### 2. Show stored generated output

Open **Schedules → Demo Program Week**.

Explain:

- the most recent generated output is stored and can be revisited
- four activity groups are visible across the multi-day workspace
- `/////` means a group is unavailable or absent
- `****` means an available but unassigned block
- manual changes are layered over generated output and visibly marked

Do not click **Generate Schedule** during the walkthrough. Setup already
generated and validated the starting state, and intentional regeneration clears
manual activity and instructor override history.

### 3. Make one safe operational move

This action must be selected from the schedule visible at runtime:

1. Choose a visible one-block occurrence of `Demo Navigation`, `Demo Creative
   Lab`, or `Demo Team Challenge`.
2. Choose a `****` target for the same activity group. Avoid `/////`, an
   occupied cell, and the continuation half of a two-block activity.
3. Drag the activity to that target, or select the activity and target through
   the workspace controls.
4. Review the temporary proposal.
5. Use **Confirm Proposal Server-Side**.
6. Verify there are no blocking conflicts.
7. Use **Save Move**.

Explain that the application recomputes the proposal on the server and stores
the change as a visible operational override rather than silently rewriting the
generated schedule.

If no suitable `****` target is visible for the first activity, cancel and
choose another visible one-block occurrence. Do not force a fixed slot from this
document.

### 4. Show conflict feedback without saving it

Select another visible one-block activity and propose moving it to an occupied
cell or a visibly unavailable `/////` cell.

Use the runtime proposal to explain the resulting blocking conflict or overlap
warning. Do not save this proposal. Use **Cancel / Choose Different Activity**
to return to the stored operational schedule.

The point is that adjustments are reviewed with consequences visible before
they become operational state.

### 5. Show participation

From Demo Program Week, open **Manage Instructor Participation**.

Point out:

- Alex Demo, Blair Demo, Casey Demo, and Devon Demo participate normally
- Emery Demo is explicitly opted out

Explain that an opted-out instructor is excluded from this schedule's automatic
staffing pool without deleting the instructor.

### 6. Show schedule-specific availability

Open **Manage Detailed Availability Exceptions**.

Show that `Casey Demo` is unavailable for `tue_am1`. Explain that this exception
belongs to Demo Program Week and does not claim to be a recurring calendar or
organization-wide rule.

### 7. Show automatic instructor assignments

Open **View Instructor Assignment Schedule**.

Explain:

- assignments are recalculated from the current operational activity schedule
- participation, qualifications, availability, overlap, and required days off
  constrain the result
- `Demo Technical Course` requires `Demo Technical Skills`
- only qualified instructors may staff that activity
- Emery receives no assignments
- Casey is not assigned during `tue_am1`

This is a current planning view; the complete automatic plan is calculated
rather than stored as a separate assignment record.

### 8. Apply one valid manual instructor correction

The exact occurrence must be selected at runtime:

1. In **Set a Manual Instructor Assignment**, choose a `Demo Technical Course`
   occurrence where `Blair Demo` is not already the displayed instructor.
2. Choose `Blair Demo`.
3. Select **Recalculate and save assignment**.
4. Review any coverage warning before confirming. Do not confirm a reduction in
   staffing coverage during the standard walkthrough.

The canonical setup validation proves that Blair is a qualified, hard-eligible
alternate for at least one technical-course occurrence. If the selected
occurrence is rejected because the live operational move changed its footprint
or overlap context, cancel and select another technical-course occurrence.

Explain that manual control remains subject to participation, qualification,
availability, overlap, and coverage safeguards.

### 9. Demonstrate staffing reset

After saving the manual assignment, use one of the supported controls:

- **Return to automatic assignment** for the saved occurrence, or
- expand **Reset all instructor assignments** and use **Confirm reset all
  instructor assignments**

Explain that this recalculates staffing without changing the generated activity
schedule or its saved activity move.

### 10. Close the story

Summarize the demonstrated value:

- generated schedules become a revisit-able operational workspace
- conflicts and consequences are visible before changes are saved
- manual activity adjustments remain identifiable
- staffing respects qualifications, participation, and availability
- operators retain manual control without bypassing hard constraints
- the technical operator can return the canonical environment to a validated
  baseline after the presentation

## Claims to avoid

Do not describe the current product as providing:

- perfect or optimal scheduling
- workload balancing or instructor preferences
- multi-instructor activity staffing
- payroll, timekeeping, or inventory
- public-demo readiness
- anonymous or per-visitor isolation
- automatic customer onboarding

## After the presentation

Do not attempt to rebuild the state manually. Follow the runbook:

1. Run read-only inspection.
2. Review detected drift.
3. Run `reset_demo_environment --confirm` with the exact organization.
4. Inspect again.
5. Reload Demo Program Week and its instructor-assignment page.

