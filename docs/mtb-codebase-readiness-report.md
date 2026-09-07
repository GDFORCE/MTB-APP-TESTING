# MTB Codebase Understanding and Requirements Readiness Report

Date: 31 August 2026  
Repository: `MTB-APP`  
Review scope: current working tree, including pre-existing uncommitted changes

## 1. Executive finding

MTB is an Expo/React Native client with a FastAPI backend. The live clinical workflow uses MongoDB visit templates and per-patient visit instances. A newer Universal Clinical Trial Schedule Model (UCTSM) exists in PostgreSQL with typed timing, evidence, validation, immutable schedule versions, and deterministic patient evaluation, but it is a parallel subsystem: it is not linked to the MongoDB trial/patient identities used by the product, and its primary screens are currently unreachable or replaced by the legacy editor/review screens.

The smallest safe path is therefore not a rewrite. It is a staged convergence:

1. extend the UCTSM canonical domain for the missing clinical concepts;
2. preserve and improve the current MongoDB path as a compatibility projection while real trial identity and patient operations are bridged;
3. make the tabular UCTSM projection the shared display model;
4. switch active schedule operations only after parity tests prove that ordinary baseline-relative schedules and existing patient history remain unchanged.

This report began as the pre-implementation audit. The first safety/foundation batch
completed afterward is summarized in `MTB_IMPLEMENTATION_HANDOFF.txt`; the detailed
findings below intentionally remain the baseline used for traceability.

## 2. Repository architecture

| Layer | Stack / storage | Primary locations | Current role |
|---|---|---|---|
| Mobile/web client | Expo 54, React Native 0.81, React 19, Expo Router, TypeScript | `frontend/app/`, `frontend/src/` | Active product UI for sponsor, PI/CRC, patient, org admin, and platform admin |
| Main API | FastAPI, Pydantic, Motor | `backend/server.py`, `backend/admin_routes.py`, `backend/org_routes.py` | Active operational API |
| Operational store | MongoDB | collections accessed from `backend/server.py` | Active source for users, trials, templates, patients, visit instances, notifications, audit, messaging, files |
| Existing extraction | provider abstraction plus Gemini LangGraph; Claude/OpenRouter/Ollama single-shot paths | `backend/protocol_agent.py`, `backend/protocol_extraction.py`, `backend/protocol_document_index.py`, `backend/schedule_schema.py` | Active protocol-to-editor pipeline |
| Universal schedule domain | Pydantic typed graph and deterministic evaluator | `backend/app/domain/schedule/` | Implemented and tested, but isolated from real product records |
| Universal schedule persistence/API | SQLAlchemy 2, PostgreSQL/SQLite tests, Alembic | `backend/app/db/`, `backend/app/services/`, `backend/app/api/uctsm.py` | Parallel UCTSM API and demo/workbench |

There is no repository-level `AGENTS.md`.

## 3. Current protocol extraction path

The active sponsor flow uploads a protocol through `POST /protocols/extract` before trial creation or `POST /trials/{trial_id}/extract-schedule` for an existing trial.

The Gemini path:

1. validates and indexes the full PDF;
2. maps document structure;
3. sweeps overlapping page chunks so all pages are covered;
4. extracts timing and visit/activity evidence;
5. synthesizes a canonical version-2 plan and compatibility visits;
6. independently confirms and audits the schedule;
7. performs bounded repair passes;
8. expands the canonical plan deterministically;
9. returns draft rows plus evidence, assumptions, verification status, and issues.

`_schedule_extraction_payload()` converts this rich result into the current editor row contract. Unknown timing remains undated and pending; it is not discarded. However, recurrence and conditions lose executable structure at this boundary: open-ended repeats are preview-expanded and conditional rows can appear like ordinary visits.

Provider limitation: Claude, OpenRouter, and Ollama do not run the complete Gemini evidence/audit/refinement graph.

The isolated UCTSM extraction graph in `backend/app/extraction/graph.py` has a good claim/evidence boundary, but no production provider is wired to the operational upload path.

## 4. Current master schedule model

There are three representations:

1. `CanonicalSchedulePlan` in `backend/schedule_schema.py` is the active extraction graph. It has anchors, phases, branches, events, activities, recurrences, transitions, conditions, conflicts, evidence IDs, and typed timing/window objects. Conditions are descriptive and recurrence is projected to finite rows for the editor.
2. MongoDB `visits` are the active approved/operational templates. They are flat rows with baseline offsets, calendar offsets, ranges, a single relative event reference, visit type, arm/substudy strings, activities, procedures, evidence, and review state.
3. `UniversalSchedule` in `backend/app/domain/schedule/models.py` is the stricter UCTSM aggregate. It has immutable schedule versions, typed temporal expressions, conditions, applicability, dependencies, recurrence, activities, evidence, and deterministic validation.

This duplication is the central risk. The richest model is not the one driving active patient care.

## 5. Current approval and versioning flow

The active MongoDB flow snapshots schedule rows when shared and creates `schedule_versions`/`schedule_reviews` for PI review. Review is assigned per site; approval/rejection updates the review and trial aggregate status. Existing snapshots protect what a reviewer saw, but patient records are not pinned to an immutable approved schedule version and later engines do not consistently execute by a patient-assigned version.

UCTSM provides immutable `ProtocolVersion` and `ScheduleVersion` records, field-level review decisions, approval gates, and a patient `current_schedule_version_id`. Approved SQL schedule children are intended to be immutable. The initial trigger migration omits some child tables (notably activities, recurrence, and applicability), so database-level immutability is incomplete.

The UCTSM enrolment selector currently relies on `Protocol.current_version_id` and latest approved version. It lacks the finalized effective-date designation and a durable version-assignment timestamp.

## 6. Current patient enrolment and schedule generation

The active flow is `POST /patients` -> `materialize_visit_instances()`:

- captures baseline plus optional free-text arm/substudy;
- filters templates by exact arm/substudy strings;
- computes every calculable row from a single base datetime;
- stores per-patient visit-instance copies of dates, windows, tasks, comments, and timing snapshot fields;
- preserves patient isolation because later writes target the instance, not the template.

Safety gaps:

- `_patient_visit_anchor()` falls back from baseline to enrolment and then to the current time. This conflicts with the explicit no-current-date fallback rule.
- Future patient-specific anchors are not represented in the active materializer.
- Patient schedule version is not pinned.
- Applicability supports only arm/substudy equality strings, not hierarchical or activity-level rules.
- Adding/editing/deleting a master template can retroactively materialize, recalculate, or delete future patient instances. That conflicts with the finalized Phase 1 version rule once a new amendment is involved.

UCTSM patient evaluation correctly keeps missing anchors distinct from missing conditions and is horizon-bounded for recurrences. It is not connected to operational patient IDs, reminders, calendars, task completion, or real visit history. Re-evaluation creates a new set of `PatientEvent` rows and does not yet reconcile protected actual execution into the new plan.

## 7. Current patient schedule and review UI

The active sponsor editor is `frontend/app/(app)/sponsor/visit-schedule.tsx`; the active PI review is `frontend/app/(app)/clinical/schedule-review.tsx`; patient/staff operational views use `clinical/visit-detail.tsx`, `patient/my-visits.tsx`, `patient/visit-detail.tsx`, and calendars.

The active editor/review presents readable rows and rich detail sheets, but the main table is not yet the specified seven-column model. It lacks first-class Applies To, explicit event type in the primary layout, separate conditional requirements, repeat-rule rows, qualifier indicators, activity timing expansion, and confinement hierarchy.

`frontend/src/features/uctsm/ScheduleTable.tsx` already implements a responsive tabular projection with Visit, Timing/Date, Window, Type, Activities, and Status. It is orphaned from the active sponsor flow, does not yet show Applies To or Actual Date, and flattens conditions into timing text rather than a separate conditional table.

## 8. Current audit, reminder, calendar, and deviation integration

MongoDB audit logging covers schedule review, visit-instance changes, task completion, comments, enrolment, and unscheduled visit changes. It does not yet provide impact-preview/confirmation records for anchor, condition, assignment, or dependency-chain changes.

The active visit calendar and PI/CRC task queue read directly from dated MongoDB visit instances. Undated/manual-review items are skipped, which safely prevents overdue status. There is no dedicated visit-reminder projection/synchronization service; the `/reminders` API is for medications. Calendar entries do not model one multi-day confinement event or event-type-specific wording.

UCTSM has `AuditEvent` and a notification candidate projection that only emits for resolved events, but it is not synchronized with active calendars or patient notifications.

Deviation support is partial: current planned window and actual visit time are stored, but there is no first-class protocol nominal date versus current planned date and no version-aware or activity-level deviation engine.

## 9. Legacy versus newer schedule components

| Capability | Active Mongo path | UCTSM path | Assessment |
|---|---|---|---|
| Real trial/patient linkage | Yes | No operational ID bridge | Mongo wins operationally |
| Evidence-backed typed schedule | Partly in stored canonical definition; flattened for use | Yes | UCTSM stronger |
| Immutable approved versions | Snapshot-like review versions, incomplete patient pinning | Yes at service level, incomplete DB triggers | UCTSM stronger but incomplete |
| Multi-anchor patient evaluation | No | Yes for typed anchors/event refs | UCTSM stronger |
| Conditions | Descriptive only | Three-valued gating only | Neither supports actions/state workflow fully |
| Open-ended recurrence | Preview flattened/capped | Horizon-bounded rule retained | UCTSM stronger |
| Intra-day activities | Procedure text only | Activity timing representable, not evaluated/persisted | Partial |
| Confinement | Flat/range visit | No explicit episode/day hierarchy | Missing |
| Qualifiers | Evidence fields, no formal marker/scope model | Claim evidence, no qualifier entity | Missing |
| Applicability | arm/substudy strings | arm/cohort/population/custom event rules | UCTSM stronger; activity-level missing |
| Patient execution | Mature visit-instance workflows | Occurrence record prototype | Mongo stronger |
| Main tabular UI | Active but compact legacy table | Better table component but orphaned | Converge on shared projection |

## 10. Existing test coverage and baseline

Backend tests cover authorization, active schedule APIs, patient visit instances, extraction, schema projection, review/sharing, calendars, and UCTSM domain/persistence/workflow/API behavior. The seven UCTSM suites passed at audit baseline: 18 tests passed.

Frontend unit scripts cover visit timing and UCTSM presentation. `test:visit-timing` passed 4/4. `test:uctsm-presentation` had one pre-existing failure (`V6` rendered instead of expected recurring label `V6+`). `expo lint` could not start because `yarnpkg` is unavailable in the environment. A full backend run was stopped after widespread environment/fixture errors; targeted UCTSM tests are healthy and targeted active-flow tests will be used for each implementation batch.

The working tree already contained large uncommitted changes in `backend/server.py`, two backend test files, and three active frontend schedule/patient screens, plus an untracked system dossier. Those changes are treated as user-owned and will be preserved.

## 11. Requirement documents found and read completely

1. `D:\MTB -app.pdf` — 272 pages. Contains finalized requirements for:
   - Anchor-Based Patient Visit Scheduling
   - Conditional / Triggered Visit & Schedule Handling
   - Open-Ended / Repeating Schedule Handling
   - Nominal vs Actual Previous Event Dependency
   - Activity-Level / Intra-Day Timing
   - Multi-Day / Inpatient / Confinement Schedule Handling
   - Schedule Table Footnote & Qualifier Resolution
   - Multi-Arm / Cohort / Part / Substudy Applicability
   - Protocol Amendment / Schedule Version Handling
   - Different Event Types / Visit Mode Handling
2. `D:\MTB_Tabular_Schedule_UI_Specification.pdf` — 6 pages, read completely.
3. `C:\Users\hp\.codex\attachments\035b7d49-e7fc-4562-be95-a8485d39b84c\pasted-text.txt` — master implementation process and safety requirements.

No substantive implementation will be treated as complete until all requirements in the companion traceability matrix have an explicit status, test, and disposition.

## 12. Traceability matrix structure

The companion matrix uses these required fields:

`Requirement ID | Document | Section | Requirement Summary | Clinical/Business Intent | Current Code Location | Current Status | Required Change | Database Impact | API Impact | AI/Extraction Impact | Patient-Scheduling Impact | UI Impact | Reminder/Calendar Impact | Audit Impact | Testing Required | Risk | Notes`

Statuses are `Implemented`, `Partially Implemented`, `Missing`, `Conflicting`, or `Unsafe`.
