# Protocol Processing and Schedule Formation — Detailed Technical Report

**System:** My Trial Board (MTB)  
**Code reviewed:** backend extraction, canonical schedule schema, API persistence, sponsor schedule editor, and patient visit materialization  
**Report date:** 25 August 2026  
**Primary implementation:** `backend/protocol_extraction.py`, `backend/protocol_agent.py`, `backend/protocol_document_index.py`, `backend/schedule_schema.py`, `backend/server.py`, and `frontend/app/(app)/sponsor/*.tsx`

## 1. Executive summary

The application does not turn a PDF directly into patient appointments in one step. It deliberately separates the process into four layers:

1. **Protocol understanding:** the PDF is validated, indexed, classified, and read by a multi-stage structured AI workflow.
2. **Schedule definition:** the AI produces an evidence-backed canonical schedule graph. Deterministic Python code validates it and projects it into flat visit rows.
3. **Human-reviewed visit templates:** the sponsor/authorized reviewer edits, acknowledges warnings, and saves the flat rows into the `visits` collection. These rows are the operational trial schedule templates.
4. **Patient-specific visit instances:** when a patient is enrolled, each matching template is calculated from that patient’s baseline date (or a fallback anchor) and copied into `visit_instances` with actual dates and windows.

This separation is the most important architectural fact. The AI output is a **draft**, not a published schedule and not a patient calendar. Human saving publishes operational templates. Patient enrollment materializes dates.

The system supports linear, cyclic, crossover, multi-arm, multi-phase, event-driven, intra-day, extension, and mixed schedules. If one PDF contains genuinely independent Schedule-of-Assessments tables (for example separate substudies), they are extracted as separate schedule variants, not merged.

Unknown or ambiguous timing is intentionally retained as an undated, manual-review row. It is not silently changed to baseline/Day 0.

## 2. End-to-end flow

```text
Protocol ID entered
  |
  +-- Found in registry/accessible organization trial
  |     -> prefill trial details -> create trial -> manually load/build schedule
  |
  +-- Not found -> upload PDF
        -> validate PDF and size
        -> build deterministic page index (best effort)
        -> classify document and detect independent schedule options
        -> discover metadata and schedule locations
        -> extract timing evidence
        -> extract visit/activity evidence
        -> synthesize canonical schedule
        -> independent confirmation
        -> deterministic comparison + model audit
        -> repair/re-audit up to configured limit
        -> deterministic canonical projection/expansion
        -> cache prepared extraction for 2 hours
        -> create trial
        -> consume prepared extraction without a second AI call
        -> reviewer edits/acknowledges/saves visit templates
        -> patient enrollment selects arm/substudy and baseline
        -> calculate patient dates/windows
        -> create patient-specific visit instances
```

There is also a direct route from an existing trial’s Visit Schedule screen: upload the PDF to `POST /trials/{trial_id}/extract-schedule`. It runs the same extraction logic and returns an editable draft without automatically writing visit templates.

## 3. Entry path A — Add Trial and process the protocol before trial creation

### 3.1 Protocol lookup

The Add Trial screen first calls `GET /protocols/lookup/{protocol_id}`. The backend:

- trims and validates the ID;
- searches the shared `protocol_registry` case-insensitively;
- otherwise searches existing trials with the same protocol ID;
- returns an organization trial only if the caller can access it;
- derives `total_visits` from saved visit templates if it is absent on the trial;
- returns `found: false` when no safe scoped match exists.

Code: `backend/server.py:2090-2147`; UI: `frontend/app/(app)/sponsor/add-trial.tsx:165-186`.

### 3.2 PDF upload and validation

When lookup does not resolve the protocol, the UI opens a PDF picker and sends multipart form data to `POST /protocols/extract`. The client timeout is 30 minutes because a complex, multi-substudy extraction can require multiple sequential model calls.

The endpoint accepts Sponsor, CRO, or PI roles. It checks:

- MIME type or `.pdf` filename;
- non-empty content;
- maximum size of 25 MiB (`MAX_PDF_BYTES`);
- configured extraction provider and provider availability.

Errors are deliberately separated:

- HTTP 400: wrong type or empty PDF;
- HTTP 413: over 25 MiB;
- HTTP 503: provider not configured, quota/billing/temporary availability problem;
- HTTP 502: extraction ran but failed.

Code: `backend/server.py:2182-2216`, `backend/protocol_extraction.py:84`; UI: `add-trial.tsx:188-243`.

### 3.3 One analysis produces details and every independent schedule

`extract_protocol_bundle_all()` selects the configured provider. With Gemini, trial metadata and schedules share the decomposed agent workflow and the same PDF/page context. If the classifier finds multiple independent schedule tables, the pipeline fans out once per option with a default concurrency limit of 3.

For each result, the endpoint creates a UUID and stores a temporary `protocol_extractions` record containing:

- authenticated `user_id`;
- source filename;
- extracted trial details;
- complete serialized `ExtractedSchedule`;
- schedule option ID, label, and description;
- creation time;
- expiry time, two hours after creation.

Expired temporary records are removed opportunistically at the start of the request. The response contains either the legacy single `extraction_id` shape or an `extractions[]` array for multiple independent schedules. Verification status, confidence, refinement count, issues, accuracy scores, and visit counts are included, but the full draft stays server-side until consumed.

Code: `backend/server.py:2218-2275`.

### 3.4 Trial details populated from the protocol

The discovery stage supplies:

- CTRI number;
- official title;
- phase;
- indications;
- investigational drug;
- planned duration;
- target enrollment;
- stated total visits, or the length of the extracted schedule as fallback;
- normalized status (`active`, `completed`, or `terminated`; otherwise `active`).

The user can review/correct these fields. Required Add Trial fields are resolved protocol ID, title, phase, and at least one indication. The app then calls `POST /trials`. Sponsor/CRO ownership is derived from the authenticated user’s organization; it is not trusted from the request body.

Code: `backend/protocol_agent.py:2066-2082`, `backend/server.py:2340-2375`; UI: `add-trial.tsx:246-288`.

### 3.5 Consuming the prepared schedule

After trial creation, the UI navigates to the schedule editor with one or more extraction IDs. Only when no visit templates are already saved does the editor consume the prepared draft(s) through:

`POST /trials/{trial_id}/protocol-extractions/{extraction_id}/consume`

The backend verifies:

- the trial exists;
- the caller owns/can manage it;
- the extraction belongs to the same authenticated user;
- it has not expired;
- if previously linked, it belongs to this trial.

It validates the stored JSON back into `ExtractedSchedule`, marks it with `trial_id` and `consumed_at`, persists the immutable schedule definition, and returns editor-compatible rows. Consumption causes **no second AI call**.

Code: `backend/server.py:3940-3990`; UI: `visit-schedule.tsx:406-476`.

## 4. Entry path B — extract a schedule for an existing trial

From the Visit Schedule editor, a reviewer can upload a PDF directly to `POST /trials/{trial_id}/extract-schedule`.

This route additionally verifies the first five bytes are `%PDF-`, accepts PDF/octet-stream/blank content types, and enforces Sponsor/CRO organization ownership or PI ownership. It calls `extract_all()` and returns:

- a normal single-schedule payload with `visits`; or
- `schedule_variants[]`, one object per independent schedule.

Each result is persisted as a draft `schedule_definition`, but the endpoint explicitly does **not** create or replace operational `visits`. The reviewer must still save in the editor. For multiple variants, the first definition becomes the current definition pointer only to avoid the accidental “last persisted wins” behavior.

Code: `backend/server.py:3843-3937`; UI: `visit-schedule.tsx:705-805`.

## 5. Provider selection and runtime configuration

`PROTOCOL_EXTRACTION_PROVIDER` selects one of:

- `gemini`/`google`;
- `claude`/`anthropic`;
- `openrouter`/`deepseek`;
- `ollama`/`qwen`/`local`.

The sample environment selects Gemini. Provider-specific API keys and model overrides are supported. Ollama is the local option. Important controls are:

- `PROTOCOL_EXTRACTION_MAX_REFINEMENTS` — default 2;
- `PROTOCOL_EXTRACTION_MIN_CONFIDENCE` — sample value 0.75; this is a review-priority threshold, not objective truth;
- `PROTOCOL_EXTRACTION_VARIANT_CONCURRENCY` — default 3;
- Gemini confirmation model override;
- Gemini PDF context cache toggle and TTL, default 1800 seconds;
- optional private `PROTOCOL_PAGE_INDEX_CACHE_DIR`.

One extraction normally uses classify, discover, timing, visit-evidence, synthesize, confirm, and audit calls, plus repair/confirm/audit rounds when needed. The PDF cache is a cost/context optimization; if it fails, Gemini falls back to attaching the PDF. Configuration: `backend/.env.example:59-106`; factory: `backend/protocol_extraction.py:2126-2140`.

## 6. Deterministic PDF indexing and page retrieval

Before model stages, the backend tries to build a text index using `pypdfium2`:

1. SHA-256 hashes the entire PDF.
2. Extracts embedded text page by page.
3. Normalizes Unicode, ligatures, nulls, whitespace, and line endings.
4. Detects frequently repeated header/footer boilerplate (on at least 45% of pages, minimum 3) and excludes it from searchable text.
5. Assigns every page a stable evidence ID: `page-{page_number}-{first 12 chars of page text SHA}`.
6. Classifies text as `text`, `sparse_text`, or `image_or_empty`.
7. Detects schedule/design section markers.

For each agent stage, deterministic weighted phrase scoring selects relevant pages for classification, discovery, timing, activities, or review. Adjacent pages are added because tables and footnotes often cross page boundaries. Pages remain in original PDF order. The default selection cap is 24 pages with a one-page neighbor radius; rendered context has an 80,000-character budget and never truncates a page halfway.

If no schedule keywords score high enough, opening pages are used and a warning is recorded. Scanned/image pages are explicitly flagged for vision/OCR review. Failure to index never blocks extraction; the provider can still use the attached PDF. The optional disk cache is content-addressed and atomically written, but its contents are protocol data and require the same retention controls as the PDF.

Code: `backend/protocol_document_index.py:1-321, 441-638`; integration: `backend/protocol_agent.py:2123-2157`.

## 7. The multi-stage extraction agent

The extraction workflow is a state graph, not one free-form prompt.

### 7.1 Stage retry and checkpoints

Each stage produces a Pydantic-validated structured response. Completed stage JSON can be checkpointed. A checkpoint is bound to the PDF SHA-256 and a format version, preventing reuse with a different document or incompatible schema. A failed stage retries up to 3 attempts by default with exponential backoff starting at 0.25 seconds. Missing credentials fail immediately.

### 7.2 Classify

Produces `DocumentTaskClassification`:

- document type: protocol, amendment, synopsis, schedule-only, reference, mixed, unrelated;
- analysis task: full schedule, amendment comparison, schedule table only, no schedule;
- archetypes and complexity;
- protocol/version/amendment/jurisdiction;
- whether a schedule exists;
- independent schedule options;
- evidence, reasoning, and confidence.

Several arms sharing one table remain one schedule. `schedule_options` is only for genuinely separate tables/timelines.

### 7.3 Independent-schedule detection and fan-out

If more than one option is detected and no option is selected, a single graph run stops after classification with `requires_schedule_selection=true`. The public “extract all” wrappers use that classification to start a full graph for every option. The classification and page index are reused, but every option has its own stage checkpoint so one substudy cannot restore another’s synthesis.

One option failing does not fail the whole batch: it becomes an empty `needs_review` schedule with an explicit failure assumption. Fan-out is semaphore-bounded.

Code: `backend/protocol_agent.py:1491-1544, 1916-2032`.

### 7.4 Discover

Builds `ScheduleDocumentMap`, locating schedule tables, treatment-plan sections, window/footnote sections, continuation pages, and metadata. This stage also supplies trial-level details used by Add Trial.

### 7.5 No-schedule decision

Schedule synthesis is skipped only when **both** classification and discovery agree that there is no schedule. This avoids accepting a single false-negative. The result is a valid empty `schedule_kind="none"` draft.

### 7.6 Timing evidence

Extracts anchors, day-numbering convention, cycle/period cadence, ranges, windows, relative rules, event-driven rules, recurrences, and temporal facts. Evidence is kept separate from visit/activity extraction to reduce cross-task omissions.

### 7.7 Visit and activity evidence

Inventories schedule columns/visits, their types, activities/procedures, constraints, arms/periods, and field-level evidence. This inventory later supports coverage checks: a schedule cannot be considered complete merely because its own internal structure looks valid.

### 7.8 Synthesize

Combines classification, document map, timing evidence, and visit evidence into `ExtractedSchedule`, preferably with a v2 `canonical_plan`. The model authors one canonical schedule; flat compatibility rows are generated deterministically later.

### 7.9 Independent confirmation

A second structured generation builds another schedule from the same evidence. It can use a separately configured model. Deterministic comparison checks deep schedule semantics, visit coverage, evidence links, and structural problems. If confirmation cannot run, the builder draft is retained but cannot be marked verified.

### 7.10 Audit and refinement

The audit receives expanded builder and confirmer schedules, deterministic disagreements, and the visit inventory. It scores visit coverage, timing, windows, visit types, procedure mapping, and the overall schedule. If rejected or divergent, the repair stage produces a corrected schedule, which is confirmed and audited again. The loop stops when accepted without confirmation issues, the configured refinement limit is reached, or a stage error requires safe finalization.

### 7.11 Finalize

Finalization:

- attaches classification;
- deduplicates collected atomic evidence by evidence ID;
- adds major/critical audit findings and stage warnings to assumptions;
- deterministically expands/projects the schedule;
- attaches confidence, refinement count, accuracy scores, audit findings, confirmation issues, and deterministic issues;
- marks `verified` only if the audit accepted, confirmation found no differences, and deterministic expansion/validation did not require review; otherwise `needs_review`.

Graph code: `backend/protocol_agent.py:1431-1888`.

## 8. Canonical schedule schema (version 2.0)

The canonical graph exists so important protocol meaning is not lost when the mobile editor needs a flat table.

### 8.1 Evidence

`SourceEvidence` holds an evidence ID, page evidence ID, claim, source location, source quote, and confidence. Required text fields cannot be blank. Schedule and field objects refer back to these IDs.

### 8.2 Temporal representation

`TemporalAmount` supports minute, hour, day, week, month, and year; common plurals/abbreviations normalize to these units.

`TimingExpression` supports:

- fixed elapsed offsets;
- calendar offsets;
- ranges;
- timing relative to another anchor/event;
- event-driven timing;
- constraints;
- recurrence;
- unresolved timing.

It preserves source labels, alternative labels, relations, qualifiers, calendar mode, weekday rules, notes, and evidence IDs. Under-specified model output is downgraded to `unresolved` instead of causing the whole schedule to be discarded.

### 8.3 Windows

`WindowSpec` separates visit-level from activity-level windows and distinguishes tolerance, validity, lookback, minimum/maximum gaps, and other rules. State is stated, not stated, unclear, or conflicting. A claimed window with no magnitudes becomes unclear; no default window is invented. Negative magnitudes are rejected.

### 8.4 Graph objects

The plan contains:

- **anchors** — consent, screening, randomization, dose, cycle/period start, last dose, end of treatment, discharge, progression, etc.;
- **phases** — screening, run-in, treatment, washout, follow-up, extension;
- **branches** — arm, cohort, period, sequence;
- **events** — visit name/type, phase/arm/period, timing, window, activity IDs, conditionality, constraints, evidence;
- **activities** — procedure/task plus its own timing, window, conditions, and constraints;
- **recurrences** — repeated events, cadence, occurrence bounds or until-event;
- **transitions** — before/after/same-day/minimum/maximum gap relationships;
- **conditions** — applicability by object, occurrence number, or branch;
- **conflicts** — unresolved/resolved contradictory source claims.

Code: `backend/schedule_schema.py:18-367`.

## 9. Deterministic schedule formation

### 9.1 Canonical plan has priority

If `canonical_plan` exists, `expand_schedule()` ignores any duplicate model-authored flat rows and calls `project_canonical_plan()`. This prevents conflicting dual representations.

Legacy/flat input remains supported. It is normalized, converted into a fallback canonical graph, expanded, and relative references are resolved.

### 9.2 Day numbering

The stored `day_offset` is a zero-based calendar displacement from baseline, independent of the printed protocol label.

- Day 0 anchor: printed Day N maps to offset N.
- Day 1 anchor: positive Day N maps to `N - 1`.
- Day 1 with explicit Day 0: non-positive labels also map using `N - 1`.
- Day 1 without Day 0: negative labels remain negative; printed Day 0 is invalid.
- If the convention is incomplete, the system does not infer risky non-positive conversions.

Only exact simple `Day N` and `Day A-B` labels are deterministically converted. Week, cycle, and prose labels are not guessed. Conflicting AI arithmetic is corrected and flagged for review.

Code: `backend/protocol_extraction.py:474-626`.

### 9.3 Hours and ranges

Extracted hour timing defaults to `absolute`. Therefore Hour 26 means 26 elapsed hours from the anchor, not `day_offset + 26 hours`. Legacy rows without a basis retain additive day-plus-hour behavior for backward compatibility. `day_end` and `hour_end` preserve multi-day/hour ranges; an end before the start is invalid.

### 9.4 Calendar months and years

The flat editor can carry an approximate `day_offset` for ordering/review plus `calendar_offset_value` and `calendar_offset_unit`. When a real patient baseline is available, the patient date calculator uses actual calendar-month/year arithmetic, including variable month lengths and leap years, instead of the approximation.

### 9.5 Recurrences and collapsed cycles

Canonical recurrences and legacy `RepeatingBlock` structures expand in deterministic Python. For a legacy cycle block:

`cycle_start = first_cycle_start_day + (cycle - from_cycle) * cycle_length_days`

Each member’s day-within-cycle is added to that start. Conditional activities are included only for their listed cycles. A missing `{cycle}` placeholder is made unique by appending `(Cycle N)`.

Open-ended cycles are bounded to 12 preview cycles unless the protocol provides a total cycle count. The assumption is recorded and review is required. Non-positive cycle lengths, missing members, or inverted ranges are skipped with warnings.

### 9.6 Relative visits

Visits such as “28 days after last dose” reference another visit by exact name and carry `relative_offset_days`. Resolution runs to a fixed point, so chains can resolve. Matching prefers the same arm and period; a globally unique match is the fallback. Missing, ambiguous, undated, or circular targets leave the visit undated and flagged.

When an operational template is later renamed, moved, added, or deleted, all relative templates in that trial are recalculated. A stale absolute offset is never retained when the target can no longer resolve.

### 9.7 Deduplication, ordering, and caps

Exact duplicates are removed using normalized name, day/hour start and end, hour basis, arm, period, and source label. Same-named visits on different days or arms remain distinct. Visits are sorted by true elapsed time; undated ET/Unscheduled rows are kept and sorted last. Final output is capped at 400 visits, with a warning if truncated.

### 9.8 Validation

Canonical validation checks, among other things:

- duplicate and globally non-unique IDs;
- broken phase, branch, anchor, event, activity, recurrence, transition, and condition references;
- evidence IDs that do not exist;
- recurrence bounds and unsupported/open-ended projection;
- unresolved source conflicts;
- timing/window shapes that require review.

Any issue is added to verification issues and forces `needs_review`.

Code: `backend/protocol_extraction.py:629-861`; `backend/schedule_schema.py:466-1287`.

## 10. Flat visit template contract

The editor/API compatibility row carries:

| Category | Fields |
|---|---|
| Identity/order | `id`, `trial_id`, `visit_number`, `name` |
| Main timing | `day_offset`, `day_end`, `source_day_label` |
| Calendar timing | `calendar_offset_value`, `calendar_offset_unit` |
| Intra-day timing | `hour_offset`, `hour_offset_basis`, `hour_end` |
| Visit window | `window_days`, `window_before`, `window_after` |
| Relative timing | `relative_to`, `relative_offset_days` |
| Structure | `arm`, `arm_label`, `period`, `substudy_label`, `visit_type` |
| Protocol work | `activities`, structured `procedures`, `operational_constraints` |
| Operational task split | `clinical_tasks`, `admin_tasks`, `checklist`, `location` |
| Review/audit | `comments`, `extraction_warning`, `review_status`, `extracted_from_protocol`, `field_evidence` |
| Day convention | `anchor_study_day`, `includes_day_zero` |

While converting an extracted schedule to the editor payload, the backend:

- copies schedule-level day convention onto every row;
- maps `arm` to `arm_label`;
- separates activities into clinical and administrative task lists;
- flags a row when the global verification needs review, the row already has a warning, or no calculable time exists;
- preserves undated rows instead of defaulting them to zero;
- stamps `extracted_from_protocol=true`.

An absolute hour (including Hour 0) is calculable without a day offset. Other missing offsets require review.

Code: `backend/server.py:3783-3840`; model: `backend/server.py:349-434`.

## 11. Immutable draft definition versus operational templates

When extraction is consumed or run directly, `_persist_schedule_definition()` writes `schedule_definitions` with:

- schema version 2.0;
- status `draft_review`;
- classification;
- canonical graph;
- evidence facts;
- validation issues;
- compatibility visits;
- verification metadata;
- source extraction ID;
- author/time.

The `(trial_id, source_extraction_id)` pair is idempotent. The trial points to `current_schedule_definition_id`. This record is described as the immutable AI draft and does not replace an approved operational schedule.

The editable/published operational schedule is the separate `visits` collection. This means later human edits do not rewrite the original evidence-backed AI draft. `GET /trials/{trial_id}/schedule-definition` returns the current canonical definition to authorized Sponsor/CRO/PI/CRC users.

Code: `backend/server.py:3739-3780, 3993-4008`.

## 12. Human review and saving

The frontend turns API visits into editable rows while preserving null timing, advanced timing, evidence, tasks, constraints, and warnings. A row is “Needs review” when `extraction_warning` is true or `review_status` is pending. Reviewers can acknowledge an intentionally undated or corrected row by clearing the warning and setting status to OK.

Client validation requires:

- at least one visit;
- nonblank visit names;
- if present, a whole-number day offset;
- if present, a nonnegative whole-day symmetric window;
- finite advanced numeric values;
- nonnegative asymmetric window sides.

Saving is a row-level synchronization, not one transaction:

1. delete previously saved rows removed in the editor;
2. assign `visit_number = index + 1`;
3. PUT changed existing rows;
4. POST new rows;
5. store newly returned IDs so another save updates rather than duplicates them.

If warnings remain, the UI requires an explicit confirmation before saving. In multi-substudy mode, each schedule is saved independently and every row is stamped with that schedule’s `substudy_label`. Reload groups saved templates by label into the same schedule cards.

Important consequence: because saving uses multiple HTTP operations, a mid-save network failure can leave a partially applied schedule. There is no batch transaction/rollback endpoint in the current editor flow.

Code: `frontend/app/(app)/sponsor/visit-schedule.tsx:236-349, 808-938`.

## 13. Server-side template safety during save/edit/delete

Creating or updating a visit repeats deterministic simple-Day normalization on the server. A conflicting source label can correct `day_offset`/`day_end` and adds a warning. A row without calculable timing is set to pending unless the reviewer explicitly acknowledged it (`review_status=ok` and `extraction_warning=false`).

CRUD access is trial-scoped. On changes:

- a newly created template is materialized for already enrolled, already materialized matching patients;
- update recalculates eligible future instances and dependent relative templates;
- delete removes only eligible future pending instances;
- completed, missed, past, rescheduled, noted, or otherwise touched instances remain as history;
- relative dependents are recomputed after create/update/delete.

Code: `backend/server.py:4011-4200, 4617-4703, 4882-5049`.

## 14. Patient-specific schedule formation

### 14.1 Anchor selection

The patient’s schedule anchor is:

1. `baseline_date`, if valid;
2. otherwise `enrolled_date`, if valid;
3. otherwise current time.

Naive datetimes are made UTC-aware.

### 14.2 Template filtering

Before calculation, templates are filtered by selected arm and/or substudy where appropriate. Blank template arm/substudy means “shared” and matches everyone. A patient assigned to a substudy receives only that substudy’s templates plus shared templates.

### 14.3 Date and window calculation

For each template:

- exact calendar month/year metadata uses real calendar arithmetic when possible;
- otherwise `canonical_elapsed_time()` applies day/hour semantics;
- start/end ranges are calculated;
- asymmetric window sides override the symmetric `window_days` side individually;
- negative window magnitudes are rejected.

If calculation fails, all dates/windows are null and the instance becomes `manual_review` with the exception message. No guessed baseline appointment is created.

The read-only `POST /trials/{trial_id}/schedule-preview` endpoint runs the same calculations before enrollment for a supplied ISO baseline and optional arm/substudy.

### 14.4 Instance creation

`materialize_visit_instances(patient)` is idempotent per patient: if any instances already exist, it returns without duplicating them. Each instance snapshots:

- patient, trial, and source template IDs;
- visit name/order/type/location;
- activities and structured procedures;
- all relevant protocol timing fields;
- scheduled start/end and window start/end;
- operational status/manual review reason;
- stable per-instance clinical/admin task rows;
- comments and audit timestamps.

Task IDs are deterministic UUIDv5 values derived from template ID, task kind, original position, and label. Task completion is patient-specific and never mutates the shared template or another patient’s work.

Code: `backend/server.py:4418-4614, 4706-4879`.

## 15. Status behavior

Materialization does not infer completion or a missed visit merely because time passed. New calculable instances start as `planned`; failures are `manual_review`; migrated completed template IDs remain completed.

For display, untouched planned/scheduled/upcoming instances are shown as:

- `overdue` when the window end (or scheduled date) is in the past;
- `due` when the window start (or scheduled date) has arrived;
- otherwise `planned`.

Explicit historical states are preserved.

Code: `backend/server.py:4751-4778, 4831-4877`.

## 16. Multi-arm versus multi-substudy behavior

These concepts must not be confused:

- **Multi-arm:** one shared schedule graph can contain arm-specific events. `arm_label` filters patient/preview rows while blank-arm events are shared.
- **Multi-substudy:** the PDF has multiple independent Schedule-of-Assessments tables. Each becomes its own extraction, canonical definition, editor card, and set of saved rows tagged by `substudy_label`.

The enrollment-side `GET /trials/{trial_id}/substudies` returns distinct nonblank saved labels. Patient materialization then applies the selected label. This prevents a patient in one substudy from receiving visits from another.

Code: `backend/server.py:4108-4121, 4551-4565, 4817-4827`.

## 17. Security, privacy, and audit controls

- Pre-creation extraction requires Sponsor/CRO/PI.
- Direct schedule extraction requires both an allowed role and trial ownership.
- Temporary extraction consumption is bound to the authenticated user and trial.
- Visit-template reads/writes are trial-access scoped; ownership checks fail closed.
- Sponsor ownership comes from the authenticated organization, not client JSON.
- Extraction, trial creation, consumption, schedule extraction, update, and delete operations write audit events.
- Page-index disk cache is optional and explicitly treated as protocol content.
- The PDF bytes are passed to the selected external provider unless the local Ollama provider is used; deployments must align provider use and caches with protocol-data policy.

## 18. Failure modes and exact safe behavior

| Condition | Behavior |
|---|---|
| Non-PDF/empty/too-large upload | Reject before extraction |
| Missing provider credentials | 503, no retry loop for configuration error |
| Provider quota/billing/unavailable | 503, document is not blamed |
| Stage transient error | Retry only that stage with backoff |
| Page indexing fails | Log and continue with attached PDF |
| Scanned pages | Mark as requiring vision/OCR; provider PDF input remains available |
| Classifier and discovery agree no schedule | Return a correct empty schedule |
| Only one says no schedule | Continue extraction |
| Independent confirmation fails | Retain builder draft, force review |
| Audit/repair fails | Retain last valid draft, force review |
| Invalid/unknown timing | Keep row undated/manual review |
| Open-ended recurrence | Bound preview, record assumption, force review |
| Broken relative target | Clear stale date, force review |
| Over 400 expanded visits | Keep first 400 and warn |
| Prepared extraction older than 2 hours | 404; upload again |
| Existing saved schedule present on initial editor load | Saved templates take priority; prepared draft is not automatically consumed |

## 19. Important implementation limitations and review points

1. **Human review remains mandatory.** “Verified” means the automated builder, confirmer, audit, evidence, and deterministic checks agreed; it is not regulatory approval.
2. **Provider confidence is not ground truth.** The environment file explicitly describes the threshold as a prioritization control.
3. **Save is not atomic.** Multiple delete/PUT/POST requests can partially succeed.
4. **Canonical definition and edited templates can diverge.** This is intentional for immutable provenance, but the current code does not create a new canonical revision representing human edits.
5. **The trial’s current definition pointer is draft-oriented.** Direct re-extraction can update the pointer even before operational rows are saved; multi-variant mode selects the first definition as the pointer.
6. **Temporary extraction cleanup is opportunistic.** Expired rows are deleted when a new pre-creation extraction runs; consumption also excludes expired rows.
7. **Fallback anchor can be “now.”** A patient with neither a valid baseline nor enrollment date will receive dates anchored to current time. Operational workflows should require/review baseline dates before relying on the calendar.
8. **Initial materialization idempotency is coarse.** If any instance exists for a patient, the full materializer stops; separate new-template retrofit logic handles templates added later.
9. **Activity splitting is heuristic.** The extracted activity list is classified into clinical versus administrative tasks by deterministic name patterns; reviewers should verify the split.
10. **Flat compatibility loses some graph richness operationally.** The immutable canonical definition preserves conditions, transitions, conflicts, and activity-level timing, while the daily operational calendar uses flattened fields and constraints.

## 20. Verification coverage in the repository

The test suite explicitly covers:

- protocol lookup and pre-creation extraction;
- day-zero/day-one conventions and exact day ranges;
- absolute-hour semantics and chronological sorting;
- collapsed cycles, conditional assessments, cadence changes, open-ended limits;
- relative chains, ambiguity, missing targets, and cycles;
- canonical month/year preservation, leap-year calendar arithmetic, event-driven visits, windows, conflicts, and projection;
- classification selection and multi-option flow;
- agent confirmation, refinement, evidence completeness, checkpoint/retry behavior;
- editor/API review-field persistence and authorization;
- future-instance propagation while preserving history;
- patient instance idempotency, task isolation, comments, status, and access controls.

Primary test files are `backend/tests/test_protocol_creation.py`, `test_protocol_agent.py`, `test_protocol_expansion.py`, `test_protocol_invariants.py`, `test_protocol_timing_contract.py`, `test_schedule_schema_v2.py`, `test_schedule_selection.py`, `test_schedule_edit.py`, and `test_visit_instances.py`.

## 21. Concise lifecycle example

Assume a protocol says:

- baseline is Day 1 and there is no Day 0;
- screening is Day -7;
- treatment Cycle 2 onward repeats every 21 days;
- Cycle Day 1 has labs and drug dispensing;
- follow-up is 28 days after last dose with `-2/+3` days.

The system will:

1. classify the PDF and locate the assessment table plus the treatment-plan cadence;
2. store evidence for the Day 1 convention, cycle duration, procedure marks, and follow-up rule;
3. represent baseline, cycle starts/visits, last-dose anchor, activities, recurrence, and relative follow-up in the canonical graph;
4. project screening to offset -7 and baseline to 0;
5. deterministically generate each bounded cycle occurrence using 21-day arithmetic;
6. resolve follow-up against the last-dose visit when uniquely dated;
7. preserve the asymmetric follow-up window;
8. flag any open-ended cycle count or unresolved last-dose link;
9. show all rows to the reviewer;
10. save approved rows as trial templates;
11. for a patient with baseline 2026-09-10, compute real visit dates and windows and create isolated patient instances.

## 22. Source map

| Responsibility | Main source |
|---|---|
| PDF/model contracts and deterministic expansion | `backend/protocol_extraction.py` |
| Multi-stage extraction, confirmation, audit, fan-out | `backend/protocol_agent.py` |
| PDF text index and task-aware retrieval | `backend/protocol_document_index.py` |
| Canonical graph, validation, projection, calendar math | `backend/schedule_schema.py` |
| API, persistence, template CRUD, patient materialization | `backend/server.py` |
| Trial lookup/upload/create flow | `frontend/app/(app)/sponsor/add-trial.tsx` |
| Review/edit/save/export flow | `frontend/app/(app)/sponsor/visit-schedule.tsx` |
| Display/parse helpers for timing and procedures | `frontend/src/lib/visit-timing.ts` |

## 23. Final interpretation

In this application, “the schedule” exists in three related forms:

1. **Canonical extracted definition** — rich evidence-backed graph, immutable draft/provenance.
2. **Trial visit templates** — human-reviewed operational schedule saved in `visits`.
3. **Patient visit instances** — calculated, isolated appointments/tasks saved in `visit_instances`.

Protocol processing forms the first, deterministic projection and reviewer action form the second, and patient baseline/date calculation forms the third. Keeping these separate is what prevents uncertain AI extraction from directly scheduling patients and prevents one patient’s visit state from affecting another patient or the shared protocol template.
