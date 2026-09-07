# MTB Universal Schedule Implementation Plan

Date: 31 August 2026

## Outcome

Converge the live MTB workflow on one evidence-backed schedule contract without rewriting the operational application. UCTSM remains the canonical scheduling domain. The MongoDB template/visit-instance flow becomes a backward-compatible operational projection during cutover, so existing baseline-relative trials and current patient execution continue to work.

## Architecture decision

### Canonical layer

Extend `backend/app/domain/schedule/` rather than create another engine. The canonical aggregate will own:

- event types and allowed modes;
- explicit dependency mode;
- generic applicability dimensions;
- conditional rules, typed actions, state, and resolution;
- repeating blocks and patient block state;
- activity timing and patient activity occurrences;
- confinement episode/day hierarchy;
- schedule-table markers, qualifiers, targets, and completeness validation;
- immutable protocol/schedule version context and evidence links.

All date/time calculation stays deterministic. Unknown or unsupported meaning remains unresolved and blocks approval/execution.

### Operational layer

Keep MongoDB patient operations active while adding a versioned projection adapter:

- each real Mongo trial/patient receives a stable UCTSM link;
- approved UCTSM schedule versions project to legacy visit templates only for compatibility consumers;
- each patient is pinned to the schedule version assigned at enrolment;
- patient planning changes reconcile stable logical event/occurrence IDs instead of replacing completed history;
- current Mongo visit/task/comment screens continue to operate while reading enriched projected fields.

No new scheduling semantics will be authored directly in the flat row model.

### Impact workflow

Anchor changes, condition activation/reversal, assignment changes, dependency-driven shifts, and confinement extensions use one proposal mechanism:

```text
patient command -> deterministic proposed evaluation -> diff protected history/current plan
                -> persist expiring proposal -> PI/CRC reviews
                -> confirm with proposal version -> transactionally apply plan
                -> synchronize calendar/reminders -> audit before/after/actor/reason
```

Preview is read-only. Confirmation rejects a stale proposal if the patient schedule changed after it was generated.

## Schema impact

Non-destructive additive migrations:

1. schedule definition:
   - dependency mode;
   - conditional rule/action/resolution;
   - repeating block identity;
   - qualifier/marker/target;
   - generic dimension and hierarchy;
   - confinement episode/study day;
   - event allowed modes and explicit review state.
2. patient definition:
   - assigned protocol/schedule version and assignment timestamp;
   - generic assignment snapshot;
   - patient branch/repeating-block/condition state;
   - stable patient event plan and planning revisions;
   - patient activity occurrence;
   - confinement actual admission/discharge;
   - impact proposal and projection synchronization state.
3. database integrity:
   - complete approved-version immutability triggers for every schedule child table;
   - uniqueness for stable patient event occurrences and idempotency keys;
   - indexes for patient/version/status/date and unresolved qualifier review.

Legacy rows remain readable. Backfill version links only where an unambiguous approved snapshot exists; otherwise mark the patient/schedule for controlled review rather than guess.

## API impact

Preserve current endpoints while introducing/enriching:

- master schedule tabular projection;
- enrolment assignment schema and automatic effective-version selection;
- patient schedule tabular projection;
- condition list/occur/resolve/reverse;
- anchor/actual-event impact preview and confirmation;
- assignment correction preview and confirmation;
- day-wise activity schedule and activity occurrence commands;
- confinement admission/discharge/extension commands;
- protocol-defined unscheduled-event activation;
- qualifier/evidence detail;
- version history/diff.

All commands are tenant/role/patient scoped and idempotent where repeat submission is plausible.

## Extraction impact

Add explicit claim categories and prompts for:

- dependency modes;
- conditional trigger/action/resolution/schedule effect;
- repeat block/pause/stop/resume;
- activity anchors and timing windows;
- confinement hierarchy;
- table markers/qualifier meaning/scope/target;
- applicability dimensions/hierarchy;
- event mode/allowed modes;
- protocol version/effective metadata.

The LLM interprets meaning. Deterministic validators check reference integrity, loops, qualifier completeness, confinement consistency, evidence coverage, and executable safety.

## UI impact

Use `frontend/src/features/uctsm/ScheduleTable.tsx` as the shared responsive table foundation, extended to the finalized columns:

- Master: Visit, Type, Timing, Window, Key Activities, Applies To, Status.
- Patient: Visit, Type, Planned Date, Window, Actual Date, Key Activities, Status.

Add separate conditional table, repeat summary, impact preview, qualifier details, activity expansion, confinement day expansion, and compact version metadata. Mobile cards remain a responsive rendering of the same row DTO.

## Implementation sequence

1. Safety fixes and domain vocabulary: remove unsafe fallback; add event/dependency/condition/qualifier/confinement types and validators.
2. Patient evaluator: activity evaluation, dependency modes, rolling recurrence, inactive conditional behavior, protected reconciliation.
3. Persistence and migration: new entities/fields, complete immutability triggers, version selection.
4. Impact proposal service and audit contract.
5. Operational bridge and compatibility projection for real Mongo trial/patient IDs.
6. API contracts.
7. Shared tabular UI and conditional/expanded rows.
8. Reminder/calendar projection synchronization.
9. Extraction claim/prompt additions and completeness checks.
10. Full scenario, regression, type, lint, and build validation.

## Blast radius and controls

| Area | Risk | Control |
|---|---|---|
| Baseline schedule arithmetic | High | Golden Day 1/8/15/29 tests before and after adapter |
| Existing patient history | Critical | Stable occurrence identity; never update actual/completed fields during plan reconciliation |
| Approved schedules | Critical | Service guards plus complete DB triggers; amendment creates a new version |
| Notifications/calendar | Critical | Outbox/projection idempotency; only confirmed active resolved events |
| Extraction contracts | High | Preserve old payload fields during additive transition; evidence-chain tests |
| Active uncommitted user work | High | Patch narrowly; never reset/checkout; review diffs after each batch |

## Batch verification gates

After every major batch:

- relevant UCTSM unit/persistence/workflow tests;
- active Mongo visit-instance regressions;
- frontend presentation tests;
- import/type/syntax checks;
- working-tree diff review to ensure user-owned edits are preserved.

