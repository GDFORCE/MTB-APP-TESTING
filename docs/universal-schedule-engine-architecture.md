# Universal Clinical Trial Schedule Engine — Architecture v1

Status: implementation baseline  
Schema contract: `uctsm.v1`  
Authority: this domain replaces the legacy visit-gap scheduler; it does not extend it.

## 1. Architectural outcome

The authoritative schedule is an immutable, evidence-backed graph of event definitions. Each event is governed by a typed timing expression, anchors, applicability, conditions, dependencies, recurrence, and activities. AI reconstructs a draft protocol schedule; deterministic application code validates it and, only after human approval, evaluates it against a patient context.

The pipeline boundary is:

```text
private protocol document
  -> document map and evidence candidates
  -> evidence-backed claims
  -> normalized UniversalSchedule draft
  -> deterministic validation
  -> field-level human review
  -> immutable approved ScheduleVersion
  -> deterministic patient evaluation
  -> versioned PatientScheduleEvaluation and PatientEvents
  -> display/notification projections
```

The extraction package cannot import patient scheduling services. Both depend only on `UniversalSchedule`, which is the anti-corruption boundary between probabilistic interpretation and deterministic execution.

## 2. Bounded components

| Component | Responsibility | Must not do |
| --- | --- | --- |
| Protocol ingestion | Hash, privately store, parse, and map document structure | Interpret or calculate patient dates |
| Extraction graph | Discover evidence, extract claims, normalize candidates, detect omissions/conflicts | Write final relational entities directly |
| Universal schedule domain | Typed canonical schedule and invariant enforcement | Depend on PDF/LLM provider or UI row order |
| Persistence | Relational identity/versioning plus JSONB value objects | Treat JSON as unvalidated arbitrary data |
| Validation | Structural, semantic, graph, evidence, and approval-gate checks | Resolve ambiguity by preference/confidence |
| Review | Field-level corrections and attributable decisions | Mutate an approved version |
| Patient evaluator | Pure deterministic evaluation from approved rules plus a patient snapshot | Call an LLM or infer unknown inputs |
| Projection | Human-readable schedule and legacy-compatible display shapes | Become a source of scheduling truth |
| Notifications | Apply notification policy to resolved patient events | Reinterpret protocol semantics |

Legacy MongoDB authentication, organizations, messaging, and unrelated patient features remain outside this boundary. New schedule persistence uses PostgreSQL through SQLAlchemy 2.x. During rollout, the existing application may read projections from the new APIs, but no new-domain service may read legacy `visits`, gap fields, or prior row dates.

## 3. Domain model

### 3.1 Aggregate and versioning

- `Trial` is tenant-scoped study identity.
- `Protocol` is the normalized protocol identity for a trial.
- `ProtocolVersion` identifies an immutable uploaded document by hash and amendment metadata.
- `ScheduleDefinition` names one logical schedule within a protocol version (primary, treatment, follow-up, sub-study, and so on).
- `ScheduleVersion` is the aggregate root for one canonical schedule revision. Draft revisions are editable through reviewed commands; approval freezes the aggregate. Amendments create a new protocol version and schedule version, optionally linked by `based_on_schedule_version_id`.
- `UniversalSchedule` is the serialized aggregate returned across service boundaries. It contains schedule metadata, day-numbering policy, epochs, dimensions, anchors, events, evidence, and issues.

Status transitions are command-checked:

```text
DRAFT -> EXTRACTED -> VALIDATION_REQUIRED -> IN_REVIEW -> APPROVED
                                                  |-> REJECTED
APPROVED -> SUPERSEDED
```

There is no transition out of `APPROVED` to an editable state. Corrections clone to a new version.

### 3.2 Event graph

`EventDefinition` is the schedulable unit; a visit is only one `event_type`. Stable `code` values are unique within a schedule version and are used by references, while UUIDs provide database identity. Events own activities and applicability rules and refer to other events only through validated references/dependencies.

Every clinically meaningful field can link to one or more `Evidence` records through `ClaimEvidence`, with a field path such as `events[SAFETY_FOLLOW_UP].timing`. `EXTRACTED` means machine-interpreted, never approved. Ambiguous, conflicting, unresolved, and unsupported constructs remain representable and set `requires_review`.

### 3.3 Typed value objects

Timing is a Pydantic discriminated union keyed by `type`, not a bag of optional fields:

- `ABSOLUTE`: explicit date/datetime stated by protocol.
- `OFFSET`: duration after/before an anchor or event.
- `RANGE`: two independently meaningful relative bounds.
- `NOMINAL_WITH_WINDOW`: nominal timing plus non-negative asymmetric bounds.
- `WITHIN` and `NO_LATER_THAN`: constraints without an invented nominal date.
- `APPROXIMATE`: retains approximate precision; it is not silently made exact.
- `TRIGGERED`: timing relative to a trigger.
- `CYCLE_DAY`: cycle definition plus cycle/day semantics.
- `PROTOCOL_DEFINED`: preserved structured extension that is not executable without an installed deterministic handler.
- `UNRESOLVED`: evidence-backed safe failure requiring review.

Durations retain units (`MINUTE`, `HOUR`, `DAY`, `WEEK`, `MONTH`, `YEAR`). Calendar months/years use calendar arithmetic with end-of-month clamping; they are never converted to fixed days. A schedule-level `DayNumbering` declares its Day 1 anchor and counting convention. For `CLINICAL_DAY`, Day 1 is offset zero, positive protocol Day N is `N - 1` calendar days, and negative days are relative offsets with no Day 0. Plain duration offsets remain elapsed durations and are not altered by clinical-day numbering.

Conditions are recursive discriminated expression trees and evaluate with Kleene three-valued logic: `TRUE`, `FALSE`, `UNKNOWN`. Supported core nodes are comparison, membership, existence, `AND`, `OR`, and `NOT`. Missing patient data produces `UNKNOWN`, never false. Applicability uses the same result type against arm/cohort/population/patient attributes.

Dependencies are explicit directed edges (`TEMPORAL`, `TRIGGER`, `PRECONDITION`, `SEQUENCE`, `ANCHOR`). Cycles block approval. Recurrence is a typed rule with a positive interval, start reference, bounded count/date/horizon, or a deterministically evaluable termination condition. Unbounded recurrence is never materialized without an evaluation horizon.

## 4. Database schema

PostgreSQL is authoritative. UUID keys use `gen_random_uuid()`, timestamps are `TIMESTAMPTZ`, and flexible semantic value objects use JSONB only after Pydantic validation. Tenant-owned roots carry `organization_id`; child tenancy is enforced through their root relationships and repository scoping. Database constraints protect uniqueness and approved-version immutability where practical, while services enforce cross-row clinical invariants.

### 4.1 Tables

| Area | Tables | Key rules |
| --- | --- | --- |
| Protocol identity | `trials`, `protocols`, `protocol_versions` | document hash retained; protocol version immutable after schedule approval |
| Schedule identity | `schedule_definitions`, `schedule_versions` | unique definition/version; lineage link; approval identity/time |
| Canonical graph | `epochs`, `arms`, `cohorts`, `populations`, `anchors`, `events`, `event_applicability`, `event_dependencies`, `event_recurrence`, `activities` | all children bound to exactly one schedule version; codes unique per version |
| Provenance | `evidence`, `claim_evidence`, `extraction_runs` | claim path plus confidence; provider/model/prompt/schema/config/input/output trace |
| Validation/review | `validation_issues`, `review_decisions`, `audit_events` | open blocking issues prevent approval; before/after/reason attributable |
| Patient context | `patients`, `patient_anchors`, `patient_states` | anchor definitions belong to patient’s pinned schedule; state is append-only by effective time |
| Evaluation | `patient_schedules`, `schedule_generation_runs`, `schedule_evaluations`, `patient_events`, `patient_event_occurrences` | evaluation snapshots are append-only; actual dates never overwrite planned dates |

`extraction_runs` is deliberately separate from `schedule_generation_runs`. The former records probabilistic document interpretation, including model and prompt versions. The latter records deterministic patient evaluation/idempotency. This resolves the handoff’s otherwise ambiguous `extraction_run_id` relationship.

Important constraints/indexes:

- unique tenant protocol identity and patient code;
- unique schedule child code within its version;
- unique dependency edge and self-edge check;
- unique idempotency key per extraction/evaluation operation;
- indexes on all foreign keys, patient state effective time, validation status, and patient event dates;
- no blanket JSONB indexes; add query-driven GIN/expression indexes only;
- an approved schedule’s canonical child rows reject update/delete through service checks and a PostgreSQL trigger in the production migration.

The ORM keeps JSON value objects as JSON/JSONB-compatible dictionaries and validates on repository ingress/egress. The migration uses PostgreSQL JSONB and UUID defaults; SQLite is permitted only for isolated automated tests, with equivalent application invariants.

## 5. Extraction pipeline

The LangGraph state is an append-only interpretation workspace containing document hash/map, candidate evidence, claims, normalized candidates, issues, and trace—not patient data.

```text
INGEST_DOCUMENT
-> DOCUMENT_STRUCTURE
-> FIND_SCHEDULE_SECTIONS
-> EXTRACT_PROTOCOL_METADATA
-> EXTRACT_EVIDENCE_AND_CLAIMS (events, timing, conditions, recurrence, activities)
-> BUILD_RELATIONSHIPS
-> EVIDENCE_LINKING
-> COMPLETENESS_CHECK
-> CONSISTENCY_CHECK
-> SCHEDULE_ASSEMBLY
-> PYDANTIC_VALIDATION
-> DETERMINISTIC_VALIDATION
-> PERSIST_DRAFT
```

Nodes use provider interfaces, allowing real LLM adapters or test fixtures. Each node records input evidence IDs and output claims. Claim normalization resolves only mechanically equivalent forms; contradictory sources create `CONFLICTING_EVIDENCE`. A node that cannot represent a construct emits `UNRESOLVED`/`PROTOCOL_DEFINED`, retains source text/location, and opens a blocking `UNSUPPORTED_PROTOCOL_CONSTRUCT` issue. No node writes schedule child tables; only the orchestration service persists the fully parsed and validated draft in one transaction.

Reruns always create a new `extraction_run` and draft version. An idempotency key returns the prior operation result only for an identical tenant, document hash, prompt/schema/model configuration, and request key; it never destroys trace history.

## 6. Validation pipeline

Validation is deterministic and rerunnable. Existing open issues generated by the same validator version are reconciled, while reviewer resolutions remain in audit history.

1. Structural: discriminated-union parsing, required fields, supported units, UUID/code uniqueness.
2. Semantic: positive recurrence interval, valid ranges/windows, executable versus review-required timing, coherent requiredness.
3. Referential: anchors/events/dimensions exist and belong to the same schedule version.
4. Graph: self-reference, duplicate edge, and cycle detection; event evaluation obtains a stable topological order.
5. Evidence: required event identity/timing/condition/activity claim paths have evidence; conflicts remain blocking.
6. Approval gate: no open blocking issue, no required unresolved timing, no invalid graph, required field-level reviews recorded, and authorized human reviewer.

AI completeness/consistency findings are inputs to validation, not substitutes for it. Confidence affects review priority only and can never approve a rule.

## 7. Deterministic schedule engine

The evaluator is pure at its core: `(approved schedule, immutable patient snapshot, horizon) -> EvaluationResult`. Persistence wraps that function.

For each event in dependency order:

1. evaluate applicability;
2. evaluate conditions;
3. resolve dependency results;
4. resolve the referenced patient anchor or prior event occurrence;
5. evaluate the typed timing expression;
6. expand recurrence only to the supplied bounded horizon/termination;
7. apply windows/constraints;
8. create status and a complete explanation object.

Status precedence is explicit:

- applicability/condition `FALSE` -> `NOT_APPLICABLE`;
- applicability/condition `UNKNOWN` -> `WAITING_FOR_CONDITION`;
- unmet blocking dependency -> `BLOCKED`;
- missing reference value -> `WAITING_FOR_ANCHOR`;
- unsupported/unresolved rule -> `UNRESOLVED`;
- safely calculated rule -> `RESOLVED`.

Evaluation never computes from the previous display row. `WITHIN` returns a permitted interval without nominal date. `APPROXIMATE` remains approximate and is `UNRESOLVED` for exact appointment generation unless an approved protocol-defined precision policy makes it executable. Trigger dates that have not occurred remain pending. Each output includes the rule, resolved inputs, arithmetic policy, dates/constraints, evidence references, and reason for inclusion or absence.

Regeneration appends a `schedule_evaluation` with its complete input snapshot and evaluator version. It does not delete earlier patient events. A patient is pinned to an approved schedule version; adopting an amendment is an explicit audited command, never an automatic replacement.

## 8. API boundaries

New endpoints live under `/api/uctsm` so legacy routes cannot be mistaken for authoritative APIs.

- `POST /protocols/{protocol_id}/versions/{version_id}/extract-schedule` queues/idempotently returns an extraction run.
- `GET /extraction-runs/{id}` returns state and trace summary.
- `GET /schedule-versions/{id}` returns the full canonical aggregate plus evidence/issues/review state.
- `POST /schedule-versions/{id}/validate` reruns deterministic validation.
- `POST /schedule-versions/{id}/review-decisions` records a field/entity decision or draft correction with before/after/reason.
- `POST /schedule-versions/{id}/submit-review` performs the state transition.
- `POST /schedule-versions/{id}/approve` and `/reject` apply the independent approval gate.
- `GET /schedule-versions/{id}/projection` returns UI-safe display data.
- `GET /schedule-versions/{left}/diff/{right}` returns semantic changes.
- `POST /patients/{id}/anchors` and `/states` append patient context.
- `POST /patients/{id}/schedule/evaluate` resolves the server-selected pinned approved version and persists an idempotent evaluation.
- `GET /patients/{id}/schedule` and `/patient-events/{id}` return current results and explanations.

Repositories require `organization_id` context and never accept tenant selection from an untrusted body. Document access uses authorization plus short-lived signed access; no public protocol URLs are returned.

## 9. Review workflow

Review is field-level. A review decision addresses an entity and JSON path, stores previous/new values, reviewer, reason, and linked evidence. Corrections are allowed only on editable versions and immediately reopen validation. The review projection orders items by risk: conflicts/unsupported timing, missing evidence, unknown conditions, recurrence/custom anchors, then lower-severity warnings.

Approval is a transaction: lock version, revalidate current persisted aggregate, verify reviewer authorization and required decisions, ensure zero blocking issues, store approval decision/audit event, and set immutable approval identity/time. A validation result obtained before the latest edit cannot be reused.

## 10. Migration, cutover, and deletion plan

1. Introduce the isolated PostgreSQL schema/package and `/api/uctsm` APIs without changing legacy scheduling behavior.
2. Run golden fixtures and deterministic evaluator tests; import existing protocols only as new extraction inputs, never column-map legacy gaps into approved clinical rules.
3. Add projection adapters to sponsor review, patient creation, calendars, and notifications. A feature flag selects the new read path per tenant/trial.
4. Require new schedules to pass review/approval and pin new patients to the approved new version.
5. For existing patients, retain the legacy snapshot read-only. Migration requires an explicitly reviewed new schedule plus an audited per-patient adoption policy; historical events stay linked to their original source/version.
6. Compare dual-read display outputs during a monitored period, but never dual-write calculated clinical dates.
7. Disable legacy schedule creation/edit/evaluation endpoints after every consumer uses the new API. Return an explicit `410 Gone`/migration message rather than silently falling back.
8. Remove legacy `VisitIn`, visit-gap calculation helpers, legacy schedule extraction/persistence, and frontend gap/day assumptions only after reference search and all old/new tests prove there are no consumers. Drop old schedule collections/tables in a later, separately approved destructive migration after a verified export/retention period.

Retain reusable infrastructure: FastAPI host/authentication, organization authorization concepts, private file storage, protocol document parsing primitives, audit transport, and frontend design system. Replace or isolate: legacy schedule Pydantic types, all gap/previous-row date calculations, direct Mongo schedule definitions, AI prompts that emit final visits, schedule review routes that edit fixed rows, and UI types that require every event to have a day.

## 11. Safety invariants and definition of done

- No LLM code is reachable from patient evaluation.
- Only an approved immutable version can generate patient events.
- Every executable clinical rule has evidence and no unresolved conflict.
- Unknown input remains unknown/pending; it is never coerced to false or a date.
- Every evaluation is reproducible from schedule version, patient snapshot, evaluator version, and horizon.
- Month/year arithmetic is calendar-based and documented in explanations.
- Infinite recurrence cannot be materialized.
- Actual occurrences never overwrite planned dates.
- Amendments and patient adoption are explicit and audited.
- Unsupported constructs are retained with evidence and block approval until human-safe representation exists.

This document is the implementation gate. Code added after it must conform to these boundaries; protocol-specific conditionals in the core engine are defects.
