# Operational Move Severity Policy

This policy defines how schedule move validation treats conflicts in the
operational editing workflow. Persistence exists, but save decisions remain
separate from raw validator severities.

## Principles

- Keep operators informed instead of silently changing or rejecting their work.
- Block only moves that would create structurally unusable schedule data.
- Allow operational judgment when a move is questionable but still representable.
- Re-run validation before saving and display all resulting conflicts.
- Treat generated schedules as operational drafts. Manual adjustment is an
  expected part of the scheduling workflow.

## Severity Behavior

### Error: block save

Use `error` when saving the move would create an invalid or ambiguous operational schedule representation.

Examples:

- A multi-block occurrence is missing a required block, is non-adjacent, or has mismatched occurrence metadata.
- Submitted block, group, slot, activity, or occurrence identifiers are invalid or stale.

Required behavior:

- Do not save the move.
- Preserve the proposed values in the response where practical.
- Show the blocking conflicts and involved blocks clearly.

### Warning: allow save with visible warning

Use `warning` when the move is structurally valid but may require operator review.

Examples:

- A daytime activity is placed in a night slot, or a night activity is placed in a daytime slot.
- Two activities occupy the same group and time slot after an explicit overlap
  operational move proposal.
- Location eligibility or capacity cannot be fully verified from available operational data.
- A source configuration change makes an existing move questionable but not structurally ambiguous.
- A persisted override fails replay but the generated operational schedule can
  still be displayed safely.

Required behavior:

- Allow save after clearly presenting the warning.
- Keep warnings visible on the schedule after saving.
- Never auto-correct or silently relocate the activity.

### Info: allow save

Use `info` for useful context that does not indicate an invalid or risky move.

Examples:

- A move changes the original generated placement.
- A selected location is optional or remains unknown.
- An override references a regenerated base schedule but still applies cleanly.

Required behavior:

- Allow save without confirmation.
- Display the information where it helps explain the resulting schedule.

## Initial Conflict Mapping

| Conflict type | Move severity |
| --- | --- |
| `duplicate_group_slot` | `warning` |
| `broken_multi_block` | `error` |
| `invalid_time_slot` | `warning` |
| `persisted_override_replay` | `warning` |

The current read-only validator reports these conflicts as `error`. `evaluate_move_proposal_for_save()` applies this move-specific mapping rather than assuming the validator severity is the save decision.

## Implementation Boundary

Move code should keep these steps separate:

1. Apply the proposed move to an in-memory copy of normalized operational blocks.
2. Run operational validation.
3. Map detected conflicts to move-policy severities.
4. Block saves containing `error`; allow `warning` and `info`.
5. Persist only after the policy decision.

No validation severity should auto-fix schedule data.

## Save-Readiness Contract

`evaluate_move_proposal_for_save(proposal_result)` returns:

- `can_save`
- `blocking_conflicts`
- `warning_conflicts`
- `informational_conflicts`
- `operator_message`

The GET proposal workflow displays this decision as read-only feedback. The
POST confirmation and save workflows recompute the proposal, validation, and
save-readiness decision from the regenerated base schedule, saved overrides,
and submitted identifiers. They do not trust client-submitted readiness fields.

Source identity verification is a blocking prerequisite for save readiness. See
`docs/manual-schedule-overrides.md` for the persisted override shape and
stale-write protection contract.

## Current Validation Coverage

Operational validation currently covers:

- `invalid_time_slot`
- `broken_multi_block`
- `duplicate_group_slot`
- `persisted_override_replay`

Two-block activities are validated as occurrences. Partial occurrence placement,
orphaned occurrence cells, non-adjacent cells, and mismatched occurrence
metadata are blocking structural errors.

Location-aware constraints remain intentionally deferred because generated
schedule output does not yet preserve the scheduling engine's selected location
assignments.
