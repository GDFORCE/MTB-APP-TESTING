# Protocol Extraction, Schedule Formation, and UI Verification

**Repository:** MTB-APP  
**Verified:** 2026-09-02  
**Purpose:** Describe exactly what protocol extraction reads and creates, what is stored, which screens and tables appear after extraction, and what is implemented but not connected to the current Add Trial flow.

---

## 1. Executive answer

The repository contains two schedule implementations.

### A. Current Add Trial flow

**Status: CURRENT AND WIRED**

This is the flow opened by `Add New Trial`. A PDF can auto-fill trial details and prepare one or more visit schedules. After the reviewed trial is saved, the app opens the `Visit Schedule` editor.

Its main table is:

| # | Visit name | Day | Window |
|---|---|---|---|

Tapping a visit opens a bottom sheet containing task lists, procedure cards, comments, timing, tolerances, and constraints.

It does **not** currently expand into the exact table:

| Activity | Timing | Anchor | Status |
|---|---|---|---|

### B. Universal Clinical Trial Schedule Model (UCTSM)

**Status: IMPLEMENTED BUT SEPARATE**

This is a newer PostgreSQL-backed engine. It has protocol schedule review, evidence, anchors, conditions, recurrence, patient schedule evaluation, activity timing, statuses, deviations, and richer tables.

Its patient schedule can expand a visit into:

| Activity | Timing rule | Planned | Actual | Status |
|---|---|---|---|---|

However, Add Trial does not currently navigate to that screen.

### C. Desired screenshot

The desired `Activity / Timing / Anchor / Status` example is not the present Add Trial result screen. Some necessary data and UI pieces exist, mainly in UCTSM, but the two paths are not connected into that exact experience.

---

## 2. Status labels

| Label | Meaning |
|---|---|
| **CURRENT AND WIRED** | Reachable through the current Add Trial flow. |
| **IMPLEMENTED BUT SEPARATE** | Code exists in UCTSM or development routes, but Add Trial does not open it. |
| **NOT IMPLEMENTED** | The exact behavior/data contract does not exist. |
| **PROVIDER BLOCKED** | Code exists, but the configured AI provider cannot currently complete extraction. |

---

## 3. Current configuration snapshot

This was checked without exposing any secret values.

| Setting | Current state |
|---|---|
| Main protocol extraction provider | `gemini` |
| Gemini key | Set |
| Anthropic key | Set |
| Main maximum repair passes | `2` |
| Main verification threshold | `0.75` |
| UCTSM database URL | Set |
| UCTSM demo mode | `true` |
| UCTSM provider/model override | Not set |

The current error is:

> Protocol extraction is temporarily unavailable: the AI provider account has no available credit

That is a provider billing/credit error, not a bad-PDF error. Until provider credit is available or the provider changes, the successful screens described below will not appear.

Sources:

- `backend/.env`
- `backend/.env.example:59-106`
- `backend/protocol_extraction.py:2031-2043`

---

## 4. Exact Add Trial user flow

### 4.1 Authorization confirmation

Before the form, the app shows `Create New Trial` and asks the user to confirm authorization, training, delegation, data accuracy, applicable regulations, and SOP responsibilities.

- `Cancel` returns to the previous screen.
- `I Confirm & Create Trial` opens the form.

Source: `frontend/app/(app)/sponsor/add-trial.tsx:418-471`.

### 4.2 Find your protocol

The user enters a Protocol ID. The frontend calls:

    GET /api/protocols/lookup/{protocol_id}

The backend checks:

1. `protocol_registry` for a case-insensitive match.
2. Existing `trials` visible to the current user.
3. Otherwise it returns `found: false`.

| Result | UI behavior |
|---|---|
| Registry match | Details fill and Step 2 unlocks. |
| Organization trial match | Details fill and Step 2 unlocks. |
| No match | `Protocol not found` modal opens. |
| Request error | Inline lookup error appears. |

Sources:

- `frontend/app/(app)/sponsor/add-trial.tsx:155-177`
- `backend/server.py:2133-2172`

### 4.3 Protocol-not-found modal

The modal contains:

- document icon;
- `Protocol not found`;
- upload/extraction explanation;
- provider/server error text, if present;
- `Choose protocol PDF`;
- `PDF up to 25 MB`.

Source: `frontend/app/(app)/sponsor/add-trial.tsx:476-514`.

### 4.4 File picker and request

`Choose protocol PDF` opens the operating-system file picker. The selected file is copied to the Expo cache and uploaded as multipart form data.

    POST /api/protocols/extract
    Content-Type: multipart/form-data
    Frontend timeout: 30 minutes

Source: `frontend/app/(app)/sponsor/add-trial.tsx:179-243`.

### 4.5 Processing screen

After file selection, the modal changes to:

- file icon;
- `Reading your protocol...`;
- `Analyzing trial details and preparing the complete visit schedule. Keep this screen open.`;
- progress bar;
- `Processing...`.

The displayed progress is an estimate. It advances to 88% while waiting and becomes 100% when the server returns. It is not a server-reported extraction-stage percentage.

### 4.6 Failure outcomes

| HTTP | Meaning | Typical response |
|---|---|---|
| 400 | Wrong type or empty PDF | Upload a PDF / uploaded PDF is empty |
| 413 | More than 25 MB | Protocol PDF is too large |
| 502 | Unusable provider output/analysis failure | Could not analyse protocol |
| 503 | Missing key, no credit, quota, rate limit, overload | Not configured or temporarily unavailable |

The current screenshot is the `503` provider-credit case.

Backend source: `backend/server.py:2207-2296`.

### 4.7 Successful extraction returns to Add Trial

On success:

1. Progress becomes 100%.
2. Trial details fill the form.
3. Temporary extraction IDs are retained in frontend state.
4. Step 2 unlocks.
5. The modal closes.
6. The banner says `Protocol details extracted. Review them before saving.`

This is a review form, not a schedule table.

| Form field | Required | Auto-filled from PDF |
|---|---:|---:|
| Protocol ID | Yes | Typed lookup value remains |
| CTRI Number | No | Yes |
| Study Title | Yes | Yes |
| Phase | Yes | Yes |
| Disease / Indication | Yes | Yes |
| Study Drug Name | No | Yes |
| Duration | No | Yes |
| Sample Size | No | Yes |
| Total Visits | No | Yes |
| Trial Status | Default active | Yes, normalized |

The action is `Save & build visit schedule`.

Source: `frontend/app/(app)/sponsor/add-trial.tsx:300-410`.

### 4.8 Trial creation and navigation

The reviewed form posts to:

    POST /api/trials

The PDF is not uploaded again. After creation, the frontend navigates to:

    /(app)/sponsor/visit-schedule

with the new trial ID and temporary extraction ID(s).

Source: `frontend/app/(app)/sponsor/add-trial.tsx:245-288`.

### 4.9 Prepared schedule consumption

The Visit Schedule screen calls once per extracted schedule:

    POST /api/trials/{trial_id}/protocol-extractions/{extraction_id}/consume

The backend checks trial access, user ownership, expiry, and cross-trial reuse. The extraction expires after two hours. Consuming it does not invoke AI again.

Sources:

- `backend/server.py:2248-2271`
- `backend/server.py:3965-4019`
- `frontend/app/(app)/sponsor/visit-schedule.tsx:416-526`

---

## 5. Current extraction providers

| Provider setting | Adapter | Behavior |
|---|---|---|
| `gemini` / `google` | `GeminiProtocolExtractor` | Current configured path; native PDF, structured output, multi-stage graph |
| `claude` / `anthropic` | `ClaudeProtocolExtractor` | Legacy fallback |
| `openrouter` / `deepseek` | `OpenRouterProtocolExtractor` | OpenRouter-compatible endpoint; single-shot schedule path |
| `ollama` / `qwen` / `local` | `OllamaProtocolExtractor` | Local page-image batches and checkpoints |

Direct `PROTOCOL_EXTRACTION_PROVIDER=openai` is not supported today.

Source: `backend/protocol_extraction.py:2031-2043`.

---

## 6. Main Gemini extraction pipeline

The configured Gemini implementation uses a bounded LangGraph workflow:

    classify
       |-- multiple independent schedules --> option fan-out/finalize
       v
    discover
       |-- classifier and discovery both say no schedule --> finalize empty
       v
    full-document evidence sweep
       v
    synthesize canonical schedule
       v
    audit + deterministic checks
       |-- accepted --> finalize verified
       |-- repair available --> repair --> audit
       `-- limit reached --> finalize needs_review

Source: `backend/protocol_agent.py:1368-1898`.

### 6.1 Classification

Produces:

- document type;
- analysis task;
- schedule archetype(s);
- complexity;
- whether a schedule exists;
- version/reference flags;
- protocol/amendment metadata;
- independent schedule options;
- confidence, evidence, and reasoning.

Classification is a routing hint. Later evidence can contradict the first archetype guess.

### 6.2 Discovery

Finds document structure, schedule tables, timing sections, cycle rules, footnotes, appendices, and possible independent schedules.

### 6.3 Full-document evidence sweep

Every page belongs to a deterministic core chunk with overlap. It gathers evidence for:

- visit columns;
- timing and windows;
- cycles/recurrence;
- relative and open-ended rules;
- activity assignments;
- footnotes;
- special visits;
- arm/period differences;
- conflicts and unknowns.

Every atomic fact carries an evidence ID, source location, source quote, claim, and confidence.

### 6.4 Synthesis

The model forms a canonical graph containing anchors, phases, branches, visits/events, activities, recurrence, transitions, conditions, conflicts, assumptions, and source notes.

### 6.5 Deterministic checks

Code checks for:

- missing, unknown, or duplicate evidence IDs;
- populated fields without evidence;
- wrong evidence categories;
- below-threshold evidence;
- canonical objects without citations;
- visit-column coverage gaps;
- activity/day gaps;
- malformed timing/graph structures;
- unjustified generic visit types.

### 6.6 Audit and repair

Audit dimensions are:

| Dimension |
|---|
| Visit coverage |
| Timing |
| Windows |
| Visit types |
| Procedure mapping |
| Overall schedule |

Issues include severity, category, finding, protocol evidence, and repair instruction. If needed, the repair stage returns a complete corrected schedule and it is audited again. The configured maximum is two repairs.

### 6.7 Final result

Verification status is one of:

- `verified`;
- `needs_review`;
- `not_run`.

`verified` means automated checks passed the configured threshold; it does not remove the human-review requirement.

---

## 7. Exactly what can be extracted

### 7.1 Trial details

| Field | Type |
|---|---|
| `ctri_number` | string |
| `title` | string |
| `phase` | string |
| `indications` | list of strings |
| `drug` | string |
| `duration` | string |
| `target_enrollment` | integer or null |
| `total_visits` | integer or null |
| `status` | normalized string |

Source: `backend/protocol_extraction.py:455-465`.

### 7.2 Classification capabilities

Document types:

- protocol;
- amendment;
- synopsis;
- schedule-only;
- reference;
- mixed;
- unrelated.

Schedule archetypes:

- linear;
- cyclic;
- crossover;
- factorial;
- multi-arm;
- multi-phase;
- event-driven;
- intra-day;
- long-term extension;
- mixed.

Source: `backend/schedule_schema.py:72-112`.

### 7.3 Canonical schedule graph

| Object | Important data |
|---|---|
| Anchor | Name, type, source label, evidence |
| Phase | Name, phase type, parent phase |
| Branch | Arm/period/cohort/sequence or protocol-specific grouping |
| Event | Type, timing, window, activities, conditions, constraints, evidence |
| Activity | Timing, activity window, condition, constraints, evidence |
| Recurrence | Frequency, occurrence range, until-event |
| Transition | From-event, to-event, relation, amount |
| Condition | Expression and event/activity/branch applicability |
| Conflict | Field, description, evidence, resolution status |

Source: `backend/schedule_schema.py:239-378`.

### 7.4 Timing types

| Kind | Example |
|---|---|
| Offset | Day 8 from baseline |
| Calendar offset | Month 3 using calendar arithmetic |
| Range | Day 14 through Day 17 |
| Relative | 28 days after last dose |
| Event-driven | At disease progression |
| Constraint | No earlier than an event |
| Recurrence | Every 21 days |
| Unresolved | Source exists but cannot be safely computed |

Units: minute, hour, day, week, month, year.

### 7.5 Window types

Windows preserve:

- visit versus activity scope;
- tolerance, validity, lookback, minimum/maximum gap, or other type;
- stated, not stated, unclear, or conflicting state;
- early/late amounts;
- source text and evidence.

No default window should be invented when none is stated.

### 7.6 Flattened rows for the current editor

| Category | Fields |
|---|---|
| Identity/order | Visit number, name |
| Day timing | Day offset/end, source day label |
| Calendar timing | Calendar amount/unit |
| Hour timing | Hour offset/end/basis |
| Visit window | Symmetric and early/late values |
| Relative timing | Relative visit and day offset |
| Structure | Arm, period, substudy, visit type |
| Day convention | Anchor study day, includes Day 0 |
| Work | Activities, clinical tasks, admin tasks |
| Procedures | Structured procedure objects |
| Operations | Constraints and comments |
| Review | Warning, pending/OK, evidence links |

Sources:

- `backend/protocol_extraction.py:212-418`
- `backend/server.py:352-423`
- `frontend/app/(app)/sponsor/visit-schedule.tsx:47-135`

### 7.7 Procedure object in the current editor

| Field | Meaning |
|---|---|
| `name` | Procedure/activity name |
| `timing` | Procedure-level timing text |
| `window` | Procedure tolerance |
| `condition` | Condition/instruction |
| `description` | Compatibility detail |
| `evidence_ids` | Supporting evidence |

There is no dedicated flattened procedure `anchor` or patient operational `status` field.

Source: `frontend/src/lib/visit-timing.ts:26-67`.

### 7.8 Multiple independent schedules

For separate substudies/schedule tables:

1. Options are classified.
2. Bundle extraction runs every option.
3. The API returns multiple extraction IDs.
4. The editor displays a collapsible card per schedule.
5. Each schedule is reviewed and saved separately.

Multi-arm protocols sharing one table remain one schedule with branch/applicability semantics.

---

## 8. Current UI after extraction

### 8.1 Screen header and states

Header:

    BUILD SCHEDULE
    Visit Schedule

States:

| State | UI |
|---|---|
| Loading | Activity indicator |
| Load failure | Error plus `Try again` |
| Loaded | Summary, filters, table, editor, save bar |

### 8.2 Summary and filters

Summary can show:

- `AI Extracted` or `Visit Template`;
- visit count;
- `Agent verified`;
- number needing review.

Optional review notes contain extraction assumptions and verification issues.

Filters are `All`, `Pending`, and `OK`.

### 8.3 Current main table

| Column | Value |
|---|---|
| `#` | Number or warning icon |
| `Visit name` | Extracted/reviewer-edited name |
| `Day` | Protocol label or computed timing |
| `Window` | Visit-level tolerance |

Edit mode adds drag and delete controls.

Source: `frontend/app/(app)/sponsor/visit-schedule.tsx:1139-1270`.

### 8.4 Multiple-schedule cards

Each card contains schedule/substudy label, visit count, active-editing state, saved badge, preview table, and `Edit this schedule` action.

Source: `frontend/app/(app)/sponsor/visit-schedule.tsx:1338-1439`.

### 8.5 Tapping a visit

A slide-up `VisitDetailSheet` opens. It is not a nested activity table.

It contains:

1. Visit name, day, and tolerance
2. Manual-review warning
3. Editable day/window
4. Clinical Tasks
5. Admin Tasks
6. Other Activities
7. Comments
8. Protocol source timing and computed placement
9. Procedure timing/tolerance cards
10. Operational constraints
11. Warning acknowledgment
12. Undated-visit explanation
13. Delete/Done actions

Each procedure card can show name, timing, tolerance, and condition/details.

Source: `frontend/app/(app)/sponsor/visit-schedule.tsx:1531-2040`.

### 8.6 Save behavior

The action is `Save Template`. Pending warnings trigger `Save with pending review?` with `Review visits` and `Save draft` actions.

Success shows the number of visits attached and returns to the trial or continues multi-schedule review.

Source: `frontend/app/(app)/sponsor/visit-schedule.tsx:1441-1525`.

---

## 9. Current CSV/export table

The download action creates the richest table currently produced by the current Visit Schedule screen.

Columns:

1. Substudy, when multiple schedules exist
2. Visit number
3. Visit name
4. Protocol timing label
5. Offset from baseline (days)
6. Offset end (days)
7. Hour offset
8. Hour end
9. Hour offset basis
10. Window days
11. Window before
12. Window after
13. Relative to
14. Relative offset days
15. Arm
16. Period
17. Visit type
18. Anchor study day
19. Includes Day 0
20. Activities
21. Structured procedures
22. Operational constraints
23. Clinical tasks
24. Administrative tasks
25. Comments
26. Review status

Source: `frontend/app/(app)/sponsor/visit-schedule.tsx:955-1029`.

---

## 10. Requested Activity / Timing / Anchor / Status table

### 10.1 Exact current capability

| Desired column | Available from Add Trial extraction? | Current rendering | Limitation |
|---|---:|---|---|
| Activity | Yes | Task lists and procedure cards | Available as activities/procedures |
| Timing | Yes | Procedure card | Text or canonical timing can exist |
| Anchor | Partly | No dedicated procedure column | Canonical anchors/relative visit timing exist, but flattened procedures lack a dedicated operational anchor |
| Status | No patient execution status | Extraction review only | `pending/ok` is review state, not Planned/Awaiting/Completed |

### 10.2 Why the screenshot cannot be formed reliably at Add Trial time

Statuses such as `Planned` and `Awaiting actual rate-change time` are patient execution states. They require:

- a patient;
- an approved or pinned schedule version;
- a patient visit occurrence;
- generated patient activity instances;
- recorded anchor timestamps such as infusion start/end or dose time;
- deterministic status evaluation.

Protocol extraction creates a reusable protocol template. It does not have these patient facts.

The current flattened procedure model also lacks dedicated fields for:

- anchor activity/code;
- offset from an activity anchor;
- planned time;
- actual time;
- operational status.

### 10.3 Truthful table at protocol-review time

A protocol-level table could correctly show:

| Activity | Timing rule | Anchor rule | Extraction review |
|---|---|---|---|
| Pre-dose PK | Before infusion | Infusion start | Needs review / OK |
| PK +1h | +1 hour | Infusion end | Needs review / OK |

This requires wiring canonical activity timing/anchor data into the current editor.

### 10.4 Truthful table after patient evaluation

Once a patient and anchors exist, an operational table can show:

| Activity | Timing rule | Planned | Actual | Status |
|---|---|---|---|---|
| Pre-dose PK | Before infusion | 09:00 | 08:58 | Completed |
| PK +1h | +1 hour from infusion end | 11:00 | - | Waiting for infusion end |

That belongs to UCTSM patient activities, not directly to Add Trial extraction.

---

## 11. Richer UCTSM screens already in the repository

### 11.1 Wiring status

**Status: IMPLEMENTED BUT SEPARATE**

- Add Trial opens `frontend/app/(app)/sponsor/visit-schedule.tsx`.
- It does not open `ProtocolScheduleScreen` or `PatientScheduleScreen`.
- A UCTSM schedule version must be linked before the UCTSM screen can resolve a trial.

### 11.2 Protocol schedule table

Desktop/tablet columns:

| Column |
|---|
| Visit |
| Visit Name |
| Timing |
| Window |
| Type |
| Activities |
| Applies To |
| Status |
| Notes |

The screen also supports validation issues, evidence viewing, review-required indicators, validation, review submission, field confirmation, and immutable approval.

Sources:

- `frontend/src/features/uctsm/ProtocolScheduleScreen.tsx`
- `frontend/src/features/uctsm/ScheduleTable.tsx:37-111`

### 11.3 Mobile UCTSM behavior

Below 760 px width, the wide table becomes visit cards showing:

- visit badge/name;
- patient status, where applicable;
- timing or expected date;
- window;
- type;
- applicability or actual date;
- activities;
- source action;
- show/hide details.

Source: `frontend/src/features/uctsm/ScheduleTable.tsx:427-464`.

### 11.4 Patient schedule table

Desktop/tablet columns:

| Column |
|---|
| Visit |
| Visit Name |
| Expected Date |
| Allowed Window |
| Type |
| Activities |
| Actual Date |
| Status |
| Notes |

The patient screen additionally shows reference dates, schedule-version context, repeat rules, timeline, confinement, and deviations.

Source: `frontend/src/features/uctsm/PatientScheduleScreen.tsx`.

### 11.5 Expanded day-wise activity table

**Status: IMPLEMENTED BUT SEPARATE**

When patient activity instances exist and the visit is expanded:

| Activity | Timing rule | Planned | Actual | Status |
|---|---|---|---|---|

Status can explain a missing anchor, for example `Awaiting Infusion End Time`.

Sources:

- `frontend/src/features/uctsm/ScheduleTable.tsx:335-361`
- `frontend/src/features/uctsm/presentation.ts:964-986`

### 11.6 Other UCTSM tables/views

| View | Information |
|---|---|
| Anchor Status | Anchor, status, date, detail |
| Deviation | Visit, expected, actual, deviation |
| Impact Preview | Visit, proposed change, current value, future value |
| Confinement | Episode, status, stay; expandable study days |
| Patient Timeline | Date, visit, status, detail |
| Continuing Requirements | Event, cadence, continuation, next date |
| Conditional Requirements | Condition, action, timing, status, confirmation |
| Protocol Notes | Marker, applicability, review state, text |
| Evidence Review | Page/table/row source and linked claim |

Source: `frontend/src/features/uctsm/ScheduleTable.tsx`.

---

## 12. Storage used by the current Add Trial flow

The current path uses MongoDB collections, not relational tables.

### 12.1 `protocol_extractions`

Temporary handoff between PDF extraction and trial creation.

Important fields:

- extraction/user ID;
- file name;
- extracted trial details;
- complete extracted schedule;
- option ID/label/description;
- created and two-hour expiry time;
- trial ID/consumption time after use.

### 12.2 `schedule_definitions`

Immutable AI/canonical draft, separate from editable templates.

Important fields:

- definition and trial ID;
- schema version `2.0`;
- `draft_review` status;
- classification;
- canonical plan;
- evidence facts;
- validation findings;
- compatibility visits;
- verification data;
- source extraction, creator, and time.

### 12.3 `trials`

Reviewed trial metadata plus the current schedule-definition pointer.

### 12.4 `visits`

Human-reviewed operational visit templates. AI extraction does not directly publish them; `Save Template` creates/updates them through visit APIs.

### 12.5 `visit_instances`

Per-patient materialized visits derived from templates and patient baseline/arm/substudy. These contain patient dates, window dates, workflow status, comments, and completion information. PDF extraction alone does not create them.

---

## 13. UCTSM relational tables declared in code

**Status: IMPLEMENTED BUT SEPARATE. Declared does not mean Add Trial currently writes these tables.**

There are 36 declared tables.

### 13.1 Trial, protocol, and extraction

| Table | Purpose |
|---|---|
| `uctsm_trials` | Canonical trial and read-mode linkage |
| `uctsm_parity_runs` | Legacy-versus-engine comparison |
| `uctsm_protocols` | Trial protocol identity |
| `uctsm_protocol_versions` | Versioned uploaded documents |
| `uctsm_extraction_runs` | Extraction jobs, trace, errors |

### 13.2 Schedule definition/version

| Table | Purpose |
|---|---|
| `uctsm_schedule_definitions` | Logical schedule identity |
| `uctsm_schedule_versions` | Versioned/reviewable/approvable schedules |
| `uctsm_epochs` | Screening/treatment/follow-up epochs |
| `uctsm_arms` | Treatment arms |
| `uctsm_cohorts` | Cohorts |
| `uctsm_populations` | Populations |
| `uctsm_anchors` | Anchor definitions/derivation |
| `uctsm_events` | Protocol visits/events |
| `uctsm_event_applicability` | Applicability expressions |
| `uctsm_event_dependencies` | Event dependencies |
| `uctsm_event_recurrence` | Recurrence rules |
| `uctsm_activities` | Activities/procedures in events |

### 13.3 Evidence and review

| Table | Purpose |
|---|---|
| `uctsm_evidence` | Page/table/row/source evidence |
| `uctsm_claim_evidence` | Extracted-claim evidence links |
| `uctsm_validation_issues` | Blocking/non-blocking findings |
| `uctsm_review_decisions` | Human confirmation/correction/rejection |

### 13.4 Patient scheduling and operation

| Table | Purpose |
|---|---|
| `uctsm_patients` | Patient and pinned version |
| `uctsm_patient_schedule_assignments` | Assignment history |
| `uctsm_schedule_impact_proposals` | Preview/confirm schedule changes |
| `uctsm_patient_anchors` | Patient anchor dates/times |
| `uctsm_patient_states` | State used by conditions |
| `uctsm_schedule_generation_runs` | Evaluation-run metadata |
| `uctsm_patient_schedules` | Patient schedule container |
| `uctsm_schedule_evaluations` | Immutable input/output snapshots |
| `uctsm_patient_events` | Evaluated visit/event instances |
| `uctsm_patient_unscheduled_visits` | Unscheduled occurrences |
| `uctsm_patient_event_occurrences` | Scheduled/actual occurrences |
| `uctsm_patient_conditions` | Conditional state |
| `uctsm_patient_activities` | Planned/actual activity instances/status |
| `uctsm_patient_activity_records` | Durable activity records |
| `uctsm_audit_events` | Tenant audit history |

Source: `backend/app/db/models.py`.

---

## 14. Separate UCTSM extraction path

UCTSM does not use `POST /api/protocols/extract`. Its request is:

    POST /api/uctsm/protocols/{protocol_id}/versions/{protocol_version_id}/extract-schedule

Behavior:

1. Create an extraction run.
2. Return HTTP 202.
3. Run extraction in a background task.
4. Poll the extraction-run endpoint.
5. Store a schedule version, evidence, issues, and trace on success.

The current UCTSM builder uses `AnthropicClient` and checks `ANTHROPIC_API_KEY`. `UCTSM_EXTRACTION_PROVIDER` is recorded on the run, but the builder is not yet a generic provider factory.

UCTSM graph stages:

1. Document structure
2. Schedule-section discovery
3. Protocol metadata
4. Epochs
5. Arms/cohorts/populations
6. Anchors
7. Events
8. Event types/visit mode
9. Timing
10. Conditions
11. Dependencies/dependency modes
12. Recurrence/repeat blocks
13. Activities
14. Activity timing
15. Qualifiers
16. Conditional actions/resolution
17. Confinement
18. Relationships
19. Evidence linking
20. Completeness check
21. Consistency check
22. Schedule assembly
23. Deterministic final validation

Sources:

- `backend/app/extraction/graph.py`
- `backend/app/extraction/runner.py`
- `backend/app/api/uctsm.py:484-548`

---

## 15. API map

### 15.1 Current Add Trial/Mongo path

| Method and path | Purpose |
|---|---|
| `GET /api/protocols/lookup/{protocol_id}` | Registry/organization lookup |
| `GET /api/protocols/lookup?protocol_id=...` | Query compatibility lookup |
| `POST /api/protocols/extract-details` | Extract form details only |
| `POST /api/protocols/extract` | Extract details and every independent schedule |
| `POST /api/trials` | Create reviewed trial |
| `POST /api/trials/{trial_id}/protocol-extractions/{id}/consume` | Consume prepared schedule without AI |
| `POST /api/trials/{trial_id}/extract-schedule` | Extract for an existing trial |
| `GET /api/trials/{trial_id}/schedule-definition` | Read canonical AI draft |
| `GET /api/trials/{trial_id}/visits` | Load visit templates |
| `POST /api/visits` | Create reviewed visit template |
| `PUT /api/visits/{visit_id}` | Edit template |
| `DELETE /api/visits/{visit_id}` | Delete template/future pending instances safely |
| `POST /api/trials/{trial_id}/schedule-preview` | Read-only baseline-date preview |

### 15.2 UCTSM path

Key capabilities:

- create trial/protocol/version;
- queue/poll extraction;
- read schedule versions;
- validate;
- record field decisions;
- submit and approve/reject review;
- project human-readable schedules;
- list approved schedules;
- create patients and assign versions;
- evaluate/read patient schedules;
- read/record anchors;
- record activity status/time;
- record conditions and preview impact;
- preview/confirm version-assignment impact;
- report deviations;
- add unscheduled visits;
- parity-check and switch read mode.

Complete route source: `backend/app/api/uctsm.py`.

---

## 16. Safety and review rules

### 16.1 Extraction remains a draft

The workflow separates:

1. AI/canonical draft
2. Human-reviewed operational template
3. Patient-specific instances/evaluations

### 16.2 Unknown timing is not Day 0

Uncomputable timing stays undated and is flagged. It is not silently placed at baseline.

### 16.3 Unknown windows are not invented

Unstated remains blank/not stated. A window claim without magnitude becomes unclear.

### 16.4 Provider failures are explicit

Missing configuration, credit/quota, rate limit, overload, malformed output, and bad documents have distinct server behavior. A provider-credit failure should not blame the PDF.

### 16.5 Temporary extraction security

Prepared Add Trial extractions are user-scoped and expire after two hours.

### 16.6 Tenant/trial checks

Extraction consumption, trial reads, visit-template operations, and UCTSM routes check access before returning or changing data.

---

## 17. What protocol extraction does not create

Protocol extraction alone does **not** create:

- patient actual visit dates;
- actual infusion/dose times;
- actual specimen times;
- patient-specific Planned/Waiting/Completed/Missed states;
- confirmed deviations;
- activity completion history;
- reliable activity-anchor statuses where an anchor is not modeled and linked;
- the exact `Activity / Timing / Anchor / Status` table in the current Add Trial editor.

These need approval, patient assignment, anchor/state capture, and deterministic evaluation.

---

## 18. Gap analysis for the requested expanded table

### 18.1 Required protocol activity contract

The current editor would need to receive or reuse:

- activity code/name;
- sequence number;
- timing rule;
- anchor event/activity code;
- offset from anchor;
- activity tolerance/window;
- requiredness;
- evidence references;
- extraction review state.

Patient operation additionally needs:

- planned time;
- earliest/latest time;
- actual time;
- operational status;
- waiting reason.

UCTSM already models most of this in `uctsm_activities` and `uctsm_patient_activities`.

### 18.2 Backend options

1. Connect Add Trial to UCTSM extraction, review, approval, and patient evaluation; or
2. Extend Mongo visit/procedure objects with anchor/status semantics and implement a second patient-activity evaluator.

The first option avoids two competing clinical activity engines.

### 18.3 Frontend change

Replace or supplement `VisitDetailSheet` procedure cards with an expandable nested table/card list.

Recommended protocol review:

| Activity | Timing rule | Anchor rule | Window | Evidence | Review |
|---|---|---|---|---|---|

Recommended patient operation:

| Activity | Timing rule | Planned | Actual | Status |
|---|---|---|---|---|

On phones, use compact activity cards or a horizontally scrollable child table. UCTSM already uses mobile visit cards below 760 px.

### 18.4 Route integration

After trial creation, the app must deliberately choose the current sponsor editor or UCTSM protocol review. After enrollment/evaluation, it should open the UCTSM patient schedule for live activity statuses.

---

## 19. Verification checklist

### Provider/upload

- [ ] Provider has active credit/quota.
- [ ] Backend restarted after key/provider changes.
- [ ] PDF is valid and no larger than 25 MB.
- [ ] User has an allowed extraction role.

### Add Trial success

- [ ] Processing modal appears.
- [ ] Trial details auto-fill.
- [ ] Success banner appears.
- [ ] Step 2 unlocks.
- [ ] Extracted fields remain editable.
- [ ] `Save & build visit schedule` creates the trial.

### Visit Schedule success

- [ ] Prepared schedule is consumed without a second AI call.
- [ ] AI Extracted badge/count appear.
- [ ] Verification warnings appear.
- [ ] Main table shows Visit name, Day, Window.
- [ ] Tapping a row opens the detail sheet.
- [ ] Procedure timing/tolerance/condition appear when extracted.
- [ ] Pending review requires confirmation.
- [ ] Saved templates persist after reload.
- [ ] CSV contains all documented columns.

### Requested activity table

- [ ] Do not claim the current Add Trial screen shows it.
- [ ] Confirm whether the route is legacy Visit Schedule or UCTSM.
- [ ] For UCTSM, verify patient activities exist.
- [ ] Verify anchors exist before expecting planned times/statuses.
- [ ] Verify expanded visit shows `Day-wise schedule`.

---

## 20. Verification commands

Run from `MTB-APP`.

Backend:

    python -m pytest backend/tests/test_schedule_schema_v2.py
    python -m pytest backend/tests/test_protocol_json_response.py
    python -m pytest backend/tests/test_uctsm_extraction.py
    python -m pytest backend/tests/test_uctsm_activity_timing.py
    python -m pytest backend/tests/test_uctsm_persistence.py

Frontend:

    cd frontend
    npm run test:visit-timing
    npm run test:uctsm

Provider check without printing secrets:

    python -c "from dotenv import dotenv_values; c=dotenv_values('backend/.env'); print(c.get('PROTOCOL_EXTRACTION_PROVIDER')); print('key set:', bool(c.get('GEMINI_API_KEY')))"

---

## 21. Source map and final conclusion

### Current Add Trial path

- `frontend/app/(app)/sponsor/add-trial.tsx`
- `frontend/app/(app)/sponsor/visit-schedule.tsx`
- `frontend/src/lib/visit-timing.ts`
- `backend/server.py`
- `backend/protocol_extraction.py`
- `backend/protocol_agent.py`
- `backend/protocol_document_index.py`
- `backend/schedule_schema.py`

### UCTSM path

- `backend/app/api/uctsm.py`
- `backend/app/db/models.py`
- `backend/app/extraction/graph.py`
- `backend/app/extraction/runner.py`
- `backend/app/extraction/llm_client.py`
- `backend/app/domain/schedule/`
- `backend/app/services/`
- `frontend/src/features/uctsm/ProtocolScheduleScreen.tsx`
- `frontend/src/features/uctsm/PatientScheduleScreen.tsx`
- `frontend/src/features/uctsm/ScheduleTable.tsx`
- `frontend/src/features/uctsm/presentation.ts`

### Verified conclusion

The current extraction can produce trial metadata, evidence-backed schedule semantics, visits, timing, windows, recurrence, activities, procedure timing/tolerances, multiple independent schedules, verification findings, and editable templates.

Current successful UI:

    PDF processing modal
      -> unlocked Add Trial review form
      -> Save & build visit schedule
      -> Visit Schedule editor
      -> Visit / Day / Window table
      -> visit bottom sheet with tasks and procedure cards

It is not currently:

    Visit row
      -> Activity / Timing / Anchor / Status table

The closest implemented UCTSM experience is:

    Patient visit
      -> expanded Day-wise schedule
      -> Activity / Timing rule / Planned / Actual / Status

Connecting it requires an explicit bridge from Add Trial extraction to UCTSM version/review workflow, patient evaluation, and frontend route selection.
