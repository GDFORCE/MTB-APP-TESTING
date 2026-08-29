# CTRI Study-Design Support: Current Gaps and Recommended Implementation

**System:** My Trial Board (MTB)  
**Assessment date:** 26 August 2026  
**Dataset:** `clinicaltrial.trials_Nov_2025.json`  
**Generated coverage artifact:** `backend/eval/ctri_nov_2025_study_design_cases.json`  
**Decision:** the protocol PDF is the only runtime source for study-design and schedule extraction. Do not add the CTRI JSON to the upload flow. Use the offline CTRI corpus only to define test coverage, while preserving exact protocol wording, normalizing its independent meanings, retaining schedule branch identity, and assigning each participant or cluster to the correct published schedule branch.

## 1. Executive conclusion

The current system does **not** fully support all 12 CTRI `Study Design` labels end to end.

It already has a strong schedule-extraction layer. The canonical graph can represent arms, cohorts, periods and treatment sequences, and it has explicit guidance and regression fixtures for multi-arm, crossover and factorial schedules. That means the system can usually describe the **visit topology** behind the named designs.

However, four separate concepts are currently conflated or disconnected:

1. the study-design evidence extracted from the protocol;
2. the protocol's schedule topology;
3. the human-approved operational visit templates;
4. the participant's arm, sequence, cohort or cluster assignment.

The most important operational defect is that arm-specific templates can be previewed with an `arm_label`, but `PatientIn` has no arm/sequence assignment and `materialize_visit_instances()` does not filter by arm. A participant can therefore receive templates for every arm or sequence. The current design label itself is also not stored in the trial or extracted trial-details models.

The recommended implementation has five parts:

1. add a first-class `StudyDesignClassification` backed only by protocol evidence and preserve the protocol's exact wording;
2. model operational assignment axes and options using stable IDs, not display labels;
3. carry assignment applicability from the canonical graph into published templates;
4. require and validate participant or cluster assignment before schedule preview/materialization;
5. publish a complete schedule revision atomically and use the offline corpus to test the 12 known labels plus unfamiliar future wording.

## 2. Review scope and method

This recommendation was produced after:

- inventorying the complete repository: 254 source/tracked files excluding dependency and cache directories;
- repository-wide searches across Python, TypeScript and TSX for trial design, allocation, randomization, arm, cohort, sequence, cluster, schedule, enrollment and materialization behavior;
- reading every code path that participates in this feature:
  - protocol lookup and PDF upload;
  - provider selection and metadata extraction;
  - document classification, discovery, evidence sweep, synthesis, audit and refinement;
  - canonical schedule validation and projection;
  - trial creation/update and schedule-definition persistence;
  - visit-template review, saving, preview and mutation;
  - direct enrollment, invitation acceptance and visit-instance materialization;
  - frontend Add Trial, Visit Schedule and Add Patient workflows;
  - relevant protocol, schedule, enrollment and visit-instance tests;
  - the existing extraction and schedule architecture documents;
- checking the current uncommitted changes so this plan remains additive and does not overwrite ongoing extraction work.

Unrelated modules such as chat, medication adherence, generic organization analytics and support-ticket presentation were inventoried and searched for shared contracts, but they do not determine study-design handling and are not used as evidence for the proposed model.

### Primary code reviewed

| Concern | Current source |
|---|---|
| Trial API model and persistence | `backend/server.py:294-329`, `2340-2424` |
| Patient and preview contracts | `backend/server.py:436-480` |
| Schedule persistence and editor response | `backend/server.py:3741-3839`, `3940-4048` |
| Template preview/filtering | `backend/server.py:4551-4614` |
| Patient materialization | `backend/server.py:4805-4880` |
| Direct enrollment and invitation flow | `backend/server.py:5290-5620`, `1460-1605` |
| Extracted trial details | `backend/protocol_extraction.py:441-451` |
| Document discovery metadata | `backend/protocol_agent.py:181-211` |
| Classification and schedule archetypes | `backend/schedule_schema.py:72-105` |
| Canonical branches and plan | `backend/schedule_schema.py:262-367` |
| Canonical projection | `backend/schedule_schema.py:732-1287` |
| Crossover and factorial instructions | `backend/protocol_agent.py:509-567` |
| Add Trial UI | `frontend/app/(app)/sponsor/add-trial.tsx` |
| Schedule editor | `frontend/app/(app)/sponsor/visit-schedule.tsx` |
| Enrollment and preview UI | `frontend/app/(app)/clinical/add-patient.tsx` |
| Structural schedule fixtures | `backend/tests/test_protocol_pattern_regressions.py` |
| Patient materialization tests | `backend/tests/test_visit_instances.py` |
| Schedule-edit propagation tests | `backend/tests/test_schedule_edit.py` |

## 3. What the supplied data actually contains

The source has 1,494 records and all CTRI numbers are November 2025 registrations. It is not a December-2025-to-present dataset. `Last Modified On` ranges through 19 December 2025, with some older anomalous values and 57 blanks.

**This JSON is not uploaded to MTB, is not queried during trial creation, and must not be used to generate a schedule.** It is an offline requirements/evaluation corpus. Its purpose is to reveal the design terminology that protocol extraction and downstream scheduling should be tested against.

The production runtime remains:

```text
protocol PDF
  -> evidence-backed design extraction
  -> evidence-backed canonical schedule extraction
  -> human review
  -> published schedule
  -> participant/cluster assignment
  -> patient visit instances
```

The three registry dimensions are not interchangeable:

- `Type of Trial`: 4 values (`Interventional`, `Observational`, `PMS`, `BA/BE`);
- `Type of Study`: 230 raw values, including modalities, observational designs, free text, blanks and concatenated multi-select values;
- `Study Design`: 12 labels.

There are 391 distinct combinations of those three fields. The generated JSON preserves all 391 combinations and accounts for all 1,494 CTRI identifiers exactly once.

### Distribution of the 12 labels

| CTRI `Study Design` label | Records |
|---|---:|
| Other | 383 |
| Single Arm Study | 329 |
| Randomized, Parallel Group Trial | 311 |
| Randomized, Parallel Group, Active Controlled Trial | 224 |
| Randomized, Parallel Group, Placebo Controlled Trial | 95 |
| Randomized, Parallel Group, Multiple Arm Trial | 61 |
| Non-randomized, Active Controlled Trial | 41 |
| Randomized, Crossover Trial | 22 |
| Non-randomized, Multiple Arm Trial | 13 |
| Cluster Randomized Trial | 8 |
| Non-randomized, Placebo Controlled Trial | 4 |
| Randomized Factorial Trial | 3 |

Three useful operational groups follow from these counts:

- 329 explicitly single-arm records are least dependent on assignment filtering;
- 782 records have a named arm, sequence, factorial or cluster-sensitive design;
- 383 records are labelled `Other` and cannot be classified safely from that label alone.

### Why `Other` must not be treated as a thirteenth topology

Among the 383 `Other` records:

- 243 are observational, 138 interventional and 2 BA/BE;
- the largest `Type of Study` values include 129 cross-sectional, 39 cohort, 22 follow-up and 12 case-control records;
- simple title cues include randomized, parallel and crossover wording in some records.

Those title matches are only offline diagnostic signals, not runtime classifications. They demonstrate why the JSON cannot substitute for reading the protocol. The production system must derive design and schedule information from protocol evidence and require review when the protocol is ambiguous or internally inconsistent.

## 4. Current capability by label

“Structural” below means the canonical schedule can describe the visit/branch shape. “End to end” also requires storing the design, publishing assignment-aware templates, assigning a participant correctly, and materializing only that participant's applicable visits.

| CTRI label | Structural capability | End-to-end verdict | Main reason |
|---|---|---|---|
| Cluster Randomized Trial | Partial | Not supported | No allocation-unit model, cluster entity or cluster-to-arm assignment |
| Non-randomized, Active Controlled Trial | Multi-arm topology exists | Partial | Non-randomization and active-control semantics are not typed; patient arm is absent |
| Non-randomized, Multiple Arm Trial | Multi-arm topology exists | Partial | Patient arm is absent; allocation method is not stored |
| Non-randomized, Placebo Controlled Trial | Multi-arm topology exists | Partial | Placebo/control role and patient arm are not first class |
| Other | Depends on actual protocol | Cannot guarantee | The label provides no topology; deeper classification is mandatory |
| Randomized Factorial Trial | Factorial canonical guidance and fixture exist | Partial | Factor-combination assignment is not connected to enrollment/materialization |
| Randomized, Crossover Trial | Sequence/period/washout model and fixtures exist | Partial | Sequence is flattened into `arm_label`; participant sequence is absent |
| Randomized, Parallel Group Trial | Multi-arm topology exists | Partial | Randomization and patient assignment are not stored |
| Randomized, Parallel Group, Active Controlled Trial | Multi-arm topology exists | Partial | Control role and assignment are display text only |
| Randomized, Parallel Group, Multiple Arm Trial | Multi-arm topology exists | Partial | Stable option identity and participant assignment are missing |
| Randomized, Parallel Group, Placebo Controlled Trial | Multi-arm topology exists | Partial | Placebo/control role and participant assignment are missing |
| Single Arm Study | Linear/shared scheduling works | Partial as a design; operationally safest | The schedule works, but the exact study-design classification is still not persisted |

Therefore:

- the current system has useful structural coverage;
- it has **zero first-class implementations of the 12 observed labels as protocol-derived design metadata**;
- it cannot guarantee correct end-to-end behavior for assignment-sensitive schedules;
- `Other` cannot receive a truthful yes/no answer without record/protocol-level classification.

## 5. Detailed findings in the current code

### Finding 1 — the study design is discarded at trial creation

`TrialIn` and `TrialPatchIn` contain title, protocol ID, phase, condition, drug, duration, enrollment and CTRI fields, but no study design, trial type, study type, allocation, comparator or allocation-unit fields (`backend/server.py:294-329`). Pydantic's normal handling means an unrecognized frontend field would not become durable trial metadata.

The Add Trial UI has the same omission. Its local `Details` type, normalization function and `/trials` payload do not carry design data (`frontend/app/(app)/sponsor/add-trial.tsx:38-58`, `121-135`, `246-279`).

Impact:

- exact CTRI labels cannot be displayed, searched, audited or reported;
- randomized/non-randomized and active/placebo distinctions are lost;
- a future “new design” cannot be compared against an authoritative stored taxonomy;
- extracted protocol design cannot currently be persisted and cross-checked against the schedule extracted from that same protocol.

### Finding 2 — extraction classifies schedule shape, not clinical study design

`DocumentTaskClassification.schedule_archetypes` supports:

`linear`, `cyclic`, `crossover`, `factorial`, `multi_arm`, `multi_phase`, `event_driven`, `intra_day`, `long_term_extension`, and `mixed` (`backend/schedule_schema.py:83-87`).

These are schedule archetypes, not replacements for:

- randomized versus non-randomized allocation;
- active versus placebo comparator;
- individual versus cluster allocation;
- interventional versus observational trial type;
- cross-sectional, cohort, case-control or other observational design.

`ScheduleDocumentMap` and `ExtractedTrialDetails` also omit a structured study-design result (`backend/protocol_agent.py:181-211`; `backend/protocol_extraction.py:441-451`). The discovery prompt locates the design section, but the returned metadata cannot retain its meaning.

### Finding 3 — the canonical layer is richer than the operational layer

`CanonicalSchedulePlan` preserves branches, events, activities, conditions, transitions and conflicts. `ScheduleBranch` has stable IDs and supports `arm`, `cohort`, `period` and `sequence` (`backend/schedule_schema.py:262-267`, `354-367`).

During `project_canonical_plan()`, this identity is reduced to strings:

- event `arm_id` becomes the branch's display name;
- period becomes a display name;
- a crossover's parent sequence name is folded into the flat `arm` string;
- the output row contains `arm` and `period`, but not the canonical arm/sequence/branch IDs (`backend/schedule_schema.py:1169-1234`).

This is sufficient for a reviewer to read but insufficient for safe assignment. Display labels can change, collide or be localized; they are not durable foreign keys.

### Finding 4 — preview supports an arm that enrollment cannot save

`SchedulePreviewIn` accepts `arm_label`, and `_build_schedule_preview()` calls `_template_matches_arm()` (`backend/server.py:476-480`, `4551-4580`).

But:

- `PatientIn` has no `arm_label`, sequence ID, cohort ID or assignment object (`backend/server.py:436-458`);
- the Add Patient UI sends `baseline_date` and `substudy_label`, but no arm/sequence (`frontend/app/(app)/clinical/add-patient.tsx:259-304`);
- `materialize_visit_instances()` filters only by `substudy_label`, not by arm (`backend/server.py:4805-4827`);
- new-template retrofit likewise filters only by substudy (`backend/server.py:4882-4910`).

This makes preview and actual enrollment semantically inconsistent. The helper required for correct filtering exists, but the durable participant assignment feeding it does not.

The existing `docs/protocol-processing-and-schedule-formation-report.md` currently says patient templates are filtered by selected arm and that enrollment selects an arm. That statement is not true for the present materialization path and should be corrected now, then updated again when assignment-aware materialization is implemented.

### Finding 5 — multi-arm, crossover and factorial schedules can over-materialize

When published visits carry different `arm_label` values, a newly enrolled patient receives every matching substudy template because arm filtering is absent. This can create visits for multiple treatment arms or crossover sequences in one participant's calendar.

The risk covers the 782 records with explicitly assignment-sensitive CTRI labels, although a particular protocol with one completely shared schedule may not exhibit the defect.

This is a correctness and safety issue, not only missing analytics.

### Finding 6 — factorial authoring and projection need one consistent contract

The synthesis guidance says a factorial protocol should author the shared visit timeline once and scope factor-specific work with `ScheduleCondition.applies_to_branch_ids` (`backend/protocol_agent.py:553-567`).

The regression fixture creates one same-day event per combination arm so projection has a branch context in which to evaluate those conditions (`backend/tests/test_protocol_pattern_regressions.py:314-365`).

If a truly shared event has no `arm_id`, `event_branch_ids()` is empty and branch-scoped conditions cannot be evaluated for a participant during current projection. The implementation therefore lacks a durable “this shared event has different applicability after assignment” representation.

Applicability must survive projection instead of being resolved irreversibly before a participant assignment exists.

### Finding 7 — cluster randomization is not modeled

The canonical graph can use arms and cohorts, but the system has no typed concept of:

- allocation unit (`participant` versus `cluster`);
- a trial cluster such as a site, school, ward or village;
- cluster-to-arm assignment;
- participant-to-cluster membership;
- validation that every participant in a cluster inherits the cluster's arm.

Treating a cluster trial as ordinary `multi_arm` may draw the visit table, but it does not preserve the defining design semantics.

### Finding 8 — randomization and comparator semantics are free text

The schema has a `randomization` anchor and conditions can contain expressions, but there is no trial-level allocation method. Active control and placebo can appear in branch names or titles, but there is no controlled vocabulary or control-role field.

Consequences include:

- no reliable validation that one branch is placebo or active control;
- no query for randomized trials independent of participant recruitment status;
- no safe distinction between “patient has status randomized” and “trial allocation method is randomized”;
- no structured reconciliation of protocol design statements versus the extracted schedule topology.

### Finding 9 — `Type of Study` cannot safely be a strict enum

There are 230 raw values, including blanks, free-text `Other (Specify)` values and concatenated selections such as `DrugSurgical/Anesthesia`. Rejecting values outside a fixed frontend list would lose source data and break future imports.

The correct pattern is:

- always preserve `raw_value`;
- optionally produce normalized categories;
- keep normalization status/evidence;
- route unrecognized or ambiguous values to review without rejecting the record.

### Finding 10 — schedule publication is not atomic

The schedule editor deletes removed rows and then issues sequential PUT/POST requests for each row (`frontend/app/(app)/sponsor/visit-schedule.tsx:837-915`). A network or validation failure in the middle can leave only part of a schedule published.

Assignment-aware templates make partial publication more dangerous: an option can exist without all its visits, or a visit can reference an option not yet saved. A batch/revision publication boundary is needed before branch assignment becomes operational.

### Finding 11 — the immutable definition and operational schedule can diverge without a revision link

`schedule_definitions` stores the extracted canonical draft and compatibility visits. Human-edited `visits` are separate. This separation is intentional, but there is no published operational revision containing:

- the approved design classification;
- assignment axes/options;
- the exact approved templates;
- a revision number and activation timestamp.

The current `current_schedule_definition_id` points to extraction provenance, not necessarily to the schedule currently used for patient materialization.

### Finding 12 — tests prove topology but not assignment-aware operation

Existing fixtures strongly cover:

- 2-way and 3-way crossover timing;
- washout transitions;
- factorial branch-scoped activities;
- multi-arm projection;
- timing, recurrence, windows and evidence behavior.

However, repository-wide test searches show no patient enrollment/materialization test that assigns an arm or sequence and asserts that other branches are excluded. There is also no cluster-allocation test and no parameterized contract test for the 12 CTRI labels.

## 6. Design principles for the fix

### 6.1 Preserve protocol source truth and normalized meaning separately

Never replace exact protocol wording with a normalized enum. Store both:

- the exact protocol wording and locations;
- normalized dimensions used by application logic;
- who/what produced the normalization;
- evidence and confirmation status.

The offline CTRI strings belong in tests, not production trial records unless the protocol itself uses the same wording. This separation is essential for detecting unfamiliar design wording without inventing a classification.

### 6.2 Do not make offline registry labels drive runtime behavior

Even when a protocol says `Randomized, Parallel Group, Placebo Controlled Trial`, that phrase alone does not say whether both arms share every visit or differ in timing. The protocol's complete design and Schedule of Assessments remain authoritative.

The extracted design candidate can guide validation and review, but it must not manufacture branches or visits absent protocol schedule evidence.

### 6.3 Use IDs for logic and labels for display

Branch labels belong in the UI. Assignment and applicability must use stable IDs scoped to a published schedule revision.

### 6.4 Separate allocation from schedule assignment

MTB does not need to become a validated randomization engine in order to schedule correctly. It can record an assignment made by an external IRT/RTSM system or authorized staff.

The initial implementation should:

- store the externally determined assignment;
- require an audit reason/source;
- prevent casual reassignment after visits start;
- avoid claiming to generate randomization allocations.

### 6.5 Unknown values should enter review, not fail ingestion

Unfamiliar or unclear wording extracted from a future protocol must be preserved with `classification_status=review_required`. The offline evaluation can then show whether that wording falls outside the known coverage set, without making the CTRI JSON a runtime dependency.

## 7. Recommended domain model

Create `backend/study_design.py` for pure Pydantic models, normalization and validation. Do not add another large block directly to the already large `server.py`.

### 7.1 Study-design classification

Recommended JSON shape:

```json
{
  "study_design": {
    "protocol_source": {
      "trial_type_wording": "Interventional clinical trial",
      "study_type_wording": "Drug study",
      "study_design_wording": "Randomized, parallel-group, placebo-controlled trial",
      "known_design_match": "randomized_parallel_placebo_controlled",
      "source_locations": ["Study Design, page 12"]
    },
    "normalized": {
      "study_kind": "interventional",
      "allocation_method": "randomized",
      "assignment_structure": "parallel",
      "control_type": "placebo",
      "allocation_unit": "participant",
      "observational_model": "not_applicable",
      "masking": "not_stated",
      "planned_arm_count": null
    },
    "classification_status": "confirmed",
    "source": "protocol",
    "evidence_ids": ["design-p12-01"],
    "review_notes": []
  }
}
```

Recommended controlled values:

- `study_kind`: `interventional`, `observational`, `pms`, `ba_be`, `other`, `unclear`;
- `allocation_method`: `randomized`, `non_randomized`, `not_applicable`, `unclear`;
- `assignment_structure`: `single_arm`, `parallel`, `crossover`, `factorial`, `cluster`, `other`, `unclear`;
- `control_type`: `none`, `active`, `placebo`, `multiple_or_mixed`, `other`, `not_stated`;
- `allocation_unit`: `participant`, `cluster`, `not_applicable`, `unclear`;
- `observational_model`: `cross_sectional`, `cohort`, `case_control`, `case_series`, `longitudinal`, `follow_up`, `qualitative`, `other`, `not_applicable`, `unclear`;
- `classification_status`: `extracted`, `confirmed`, `review_required`, `conflicting`;
- `source`: `protocol`, `manual`.

Do not make `study_design_wording` a strict enum. `known_design_match` can be null for unfamiliar wording while the exact protocol text remains intact.

### 7.2 Offline coverage mapping for the 12 observed labels

| Raw label | Allocation | Structure | Control | Allocation unit |
|---|---|---|---|---|
| Cluster Randomized Trial | randomized | cluster | not stated | cluster |
| Non-randomized, Active Controlled Trial | non-randomized | parallel | active | participant unless protocol says otherwise |
| Non-randomized, Multiple Arm Trial | non-randomized | parallel | multiple/mixed | participant unless protocol says otherwise |
| Non-randomized, Placebo Controlled Trial | non-randomized | parallel | placebo | participant unless protocol says otherwise |
| Other | unclear | other | not stated | unclear |
| Randomized Factorial Trial | randomized | factorial | not stated | participant unless protocol says otherwise |
| Randomized, Crossover Trial | randomized | crossover | not stated | participant |
| Randomized, Parallel Group Trial | randomized | parallel | not stated | participant unless protocol says otherwise |
| Randomized, Parallel Group, Active Controlled Trial | randomized | parallel | active | participant unless protocol says otherwise |
| Randomized, Parallel Group, Multiple Arm Trial | randomized | parallel | multiple/mixed | participant unless protocol says otherwise |
| Randomized, Parallel Group, Placebo Controlled Trial | randomized | parallel | placebo | participant unless protocol says otherwise |
| Single Arm Study | not applicable | single arm | none | not applicable |

This table is a test oracle for terminology observed in the offline corpus. It is not a production data feed. Mappings marked “unless protocol says otherwise” are candidates, not facts; the full protocol evidence determines the actual classification and schedule.

### 7.3 Operational assignment axes

Add assignment definitions to the approved schedule revision rather than overloading canonical branch types:

```json
{
  "assignment_axes": [
    {
      "axis_id": "treatment-arm",
      "name": "Treatment arm",
      "kind": "arm",
      "assignment_level": "participant",
      "required": true,
      "selection_mode": "exactly_one",
      "options": [
        {"option_id": "arm-test", "label": "Test", "canonical_branch_ids": ["arm-test"]},
        {"option_id": "arm-placebo", "label": "Placebo", "canonical_branch_ids": ["arm-placebo"]}
      ]
    }
  ]
}
```

Axis kinds should initially support:

- `arm` for parallel and factorial combination arms;
- `sequence` for crossover treatment order;
- `cohort` where cohort changes the schedule;
- `cluster_arm` for cluster-level assignment.

Period is structural and should not normally be selected at enrollment. A participant assigned to Sequence AB receives all periods nested under Sequence AB.

### 7.4 Template applicability

Replace string-only filtering with applicability constraints:

```json
{
  "visit_template_id": "visit-week-4-placebo",
  "display_arm_label": "Placebo",
  "applicability": [
    {"axis_id": "treatment-arm", "option_ids": ["arm-placebo"]}
  ]
}
```

Rules:

- empty `applicability` means shared by everyone in that schedule/substudy;
- multiple options within one constraint mean OR;
- constraints across axes mean AND;
- canonical IDs remain in provenance fields;
- display labels can be edited without changing identity.

This also fixes factorial behavior: one shared event can remain shared while factor-specific activities carry applicability that is resolved only after assignment.

### 7.5 Participant schedule assignment

Add to patient/invitation data:

```json
{
  "schedule_assignment": {
    "schedule_revision_id": "rev-2026-08-26-01",
    "status": "assigned",
    "selections": [
      {"axis_id": "treatment-arm", "option_id": "arm-placebo"}
    ],
    "cluster_id": null,
    "source": "external_randomization_system",
    "source_reference": "RTSM-12345",
    "assigned_by": "user-id",
    "assigned_at": "2026-08-26T10:00:00Z"
  }
}
```

Allowed assignment sources should include `external_randomization_system`, `import`, and `authorized_manual_entry`. The API should audit every assignment and reassignment.

### 7.6 Cluster model

Add a small `trial_clusters` collection:

```json
{
  "id": "cluster-school-17",
  "trial_id": "trial-id",
  "schedule_revision_id": "revision-id",
  "label": "School 17",
  "cluster_type": "school",
  "assignment_option_id": "arm-intervention",
  "assignment_source": "external_randomization_system",
  "source_reference": "RTSM-C017",
  "assigned_at": "2026-08-26T10:00:00Z"
}
```

A cluster-trial participant stores `cluster_id`; the server derives the effective schedule option from the cluster. It must reject a patient-supplied arm that conflicts with the cluster.

## 8. Extraction changes

### 8.1 Extend discovery rather than creating a second unrelated AI call

Add `study_design_candidate` to `ScheduleDocumentMap` and `ExtractedTrialDetails`. The current Gemini flow already reads the discovery/design sections, so the design candidate should come from that evidence-backed pass.

Capture:

- raw protocol wording;
- allocation method;
- assignment structure;
- comparator/control type;
- allocation unit;
- masking if stated;
- named arms/sequences/clusters and expected count;
- evidence IDs and conflict notes.

### 8.2 Reconcile protocol design evidence with protocol schedule evidence

Implement deterministic reconciliation in `backend/study_design.py`:

1. extract exact study-design wording and page evidence from the protocol;
2. normalize only the dimensions directly supported by that evidence;
3. compare the normalized design with canonical schedule branches and applicability;
4. record mismatches explicitly;
5. set `review_required` when a key dimension conflicts or remains unclear;
6. never rewrite or discard the protocol wording.

Examples:

- protocol design section says randomized parallel placebo and its branches agree: confirmed;
- protocol uses nonspecific wording such as `Other`, while later design evidence clearly describes a crossover: preserve both passages, normalize to `crossover`, and require reviewer confirmation;
- protocol synopsis says single arm but its schedule has two mutually exclusive treatment branches: status `conflicting`; schedule publication is blocked until resolved.

### 8.3 Provider compatibility

Gemini currently uses the full decomposed workflow. Claude, OpenRouter and Ollama use legacy/single-shot paths for parts of extraction. The new fields must exist in the common `ExtractedTrialDetails` schema so every provider returns a compatible shape.

Provider-specific confidence is not enough to mark a design confirmed. Only explicit source evidence plus human confirmation should do that.

### 8.4 Keep schedule archetypes unchanged initially

Do not replace the ten composable schedule archetypes with the 12 labels observed in the offline corpus. They answer different questions and the existing archetype guidance is valuable.

Add a cross-check step:

- normalized `crossover` should normally include sequence/period branches;
- normalized `factorial` should include combination-arm assignment options;
- normalized `single_arm` should not publish multiple mutually exclusive treatment options;
- normalized `cluster` must declare cluster-level assignment;
- mismatch becomes a deterministic audit issue.

## 9. Backend and persistence changes

### 9.1 New modules

Recommended modules:

- `backend/study_design.py`: models, raw-label normalization and reconciliation;
- `backend/schedule_assignment.py`: assignment axes, applicability validation and filtering;
- `backend/study_design_routes.py`: design/options/review endpoints if the route surface grows.

Keeping pure logic outside `server.py` makes it unit-testable without MongoDB and avoids expanding a 9,000-line module further.

### 9.2 Trial contracts

Extend:

- `TrialIn`;
- `TrialPatchIn`;
- `ExtractedTrialDetails`;
- protocol-extraction response;
- trial list/detail serializers;
- organization/admin trial projections where design is relevant.

Use one nested `study_design` object instead of many loosely related top-level fields.

### 9.3 Published schedule revisions

Introduce a human-approved operational revision distinct from the immutable AI draft:

```text
schedule_definition        AI extraction provenance
        |
        v
schedule_revision          reviewer-approved design + axes + templates
        |
        v
trial.published_schedule_revision_id
        |
        v
patient.schedule_assignment.schedule_revision_id
```

A revision should contain or reference:

- source schedule-definition ID;
- normalized and raw study-design snapshot;
- assignment axes/options;
- all approved visit templates and applicability;
- revision number/status;
- creator/approver/timestamps;
- audit/review notes.

### 9.4 Atomic publication endpoint

Replace row-by-row publication with a batch contract such as:

```http
PUT /trials/{trial_id}/schedule-publication
If-Match: <current-revision>
```

The request includes the complete design, axes and visit rows. The server:

1. validates all IDs, timing, applicability and design/topology consistency;
2. creates an immutable staged revision;
3. creates its versioned templates;
4. atomically switches `published_schedule_revision_id` only after validation succeeds;
5. returns the complete saved revision;
6. asynchronously or transactionally propagates eligible future changes to existing participants.

If Mongo transactions are available, use one transaction. If not, immutable staging plus a single pointer swap still prevents a partially written revision from becoming active.

Keep existing visit CRUD temporarily for backward compatibility, but have the updated editor use the publication endpoint.

### 9.5 Assignment endpoints

Add:

- `GET /trials/{id}/schedule-assignment-options`;
- `POST /trials/{id}/schedule-preview` accepting structured selections;
- `PUT /patients/{id}/schedule-assignment` for authorized assignment/reassignment;
- cluster CRUD/assignment endpoints for cluster trials.

Server validation must confirm:

- option belongs to the active revision and axis;
- every required axis has exactly one selection;
- selections are mutually valid;
- a cluster-trial patient belongs to a valid assigned cluster;
- the selected substudy and assignment axes belong to the same revision;
- caller has trial-scoped authorization.

### 9.6 Correct materialization algorithm

Recommended pure selection algorithm:

```text
load the trial's published schedule revision
validate or derive the participant's assignment
for each template in the selected substudy/revision:
    if template.applicability is empty:
        include it
    else if every axis constraint matches the participant's selection:
        include it
    else:
        exclude it
calculate dates/windows for included templates only
snapshot revision ID, assignment selections and applicability on each instance
insert instances idempotently by patient + revision + template
```

Do not filter by mutable display label.

For assignment changes:

- preserve completed, historical, rescheduled or otherwise touched instances;
- remove or supersede only untouched future instances from the old assignment;
- create untouched future instances for the new assignment;
- require an audit reason;
- mark conflicts for manual reconciliation.

## 10. Frontend changes

### 10.1 Add Trial

Update `frontend/app/(app)/sponsor/add-trial.tsx` to display and submit:

- exact protocol design wording and evidence location;
- normalized allocation, structure, control and allocation-unit candidate;
- source and evidence/review status;
- a warning when design wording and schedule topology disagree;
- a review-required state for nonspecific or unfamiliar protocol wording.

The backend should remain authoritative for available values. Prefer a metadata/options endpoint or generated API types over duplicating a 12-value list in several screens.

### 10.2 Schedule review/editor

Update the editor's row and schedule-variant contracts to carry:

- stable revision ID;
- canonical event ID;
- assignment-axis/option IDs;
- applicability constraints;
- display labels separately.

Add a design-and-assignment summary above the visits:

- protocol-derived design classification;
- normalized classification;
- arms/sequences/options;
- shared versus option-specific visit counts;
- unresolved conflicts.

Saving should submit one publication request, not many row requests.

### 10.3 Add Patient

After trial selection:

1. fetch substudies and assignment options for the active revision;
2. show no assignment picker for a single-arm/shared schedule;
3. show an arm picker for parallel/factorial participant-level assignment;
4. show a sequence picker for crossover;
5. show a cluster picker for cluster trials and derive the arm from it;
6. require all necessary selections before preview;
7. submit the same structured selections used for preview in the invitation payload;
8. display the revision/design context in the preview.

Changing trial, substudy, cluster or assignment must invalidate the existing preview, just as changing substudy currently does.

### 10.4 Trial detail and audit visibility

Show the confirmed design and active schedule revision on sponsor/clinical trial detail screens. Show assignment source/reference to authorized staff, but do not expose sensitive randomization information to roles that should remain blinded.

Masking/blinding policy needs a separate authorization decision. The system must not reveal treatment assignment merely because the field exists.

## 11. Migration and backward compatibility

MongoDB is schemaless, so this should be an additive, idempotent migration.

### 11.1 Trial backfill

- set missing `study_design.classification_status` to `review_required`;
- do not infer design from title alone;
- backfill from retained protocol extraction evidence where it exists;
- preserve every imported raw value;
- emit a dry-run report before writes.

### 11.2 Existing template backfill

For each trial with `arm_label` values:

1. match labels to the current canonical definition when unambiguous;
2. create assignment axes/options and applicability IDs;
3. retain original labels as display text;
4. flag duplicate/ambiguous label matches;
5. never guess across ambiguous canonical branches.

### 11.3 Existing patient backfill

- single-arm or fully shared schedules: set assignment status `not_required`;
- one unambiguous option derivable from existing data: propose, but audit, the assignment;
- multiple possible arms/sequences: set `review_required` and do not guess;
- preserve existing visit instances during migration;
- provide a reconciliation screen/report before changing future schedules.

### 11.4 Compatibility window

During rollout:

- keep reading legacy `arm_label` when applicability IDs are absent;
- write both display label and new IDs for updated schedules;
- log every legacy fallback;
- remove fallback only after migration metrics reach zero.

## 12. Implementation phases

### Phase 0 — lock current behavior with failing regression tests

Add tests that demonstrate the current defects before implementation:

- a two-arm patient currently receives both arms;
- a crossover patient currently receives both sequences;
- a preview can filter by arm but the subsequent invitation cannot preserve it;
- design fields sent to the current trial contract are not durable;
- cluster allocation cannot be represented.

No production behavior changes in this phase.

### Phase 1 — study-design metadata and reconciliation

Implement `StudyDesignClassification`, raw-label mapping, extraction fields, trial persistence and Add Trial review UI.

Definition of done:

- all 12 labels round-trip without loss;
- an unknown thirteenth label is accepted as raw data and routed to review;
- protocol design/schedule conflicts are visible and auditable;
- design metadata remains separate from schedule archetypes.

### Phase 2 — stable operational assignment and correct materialization

Implement assignment axes/options, template applicability, patient assignment, preview validation and assignment-aware materialization.

Definition of done:

- shared visits plus only the selected arm/sequence are materialized;
- stable IDs survive label edits;
- factorial activity applicability is evaluated for the selected combination;
- assignment changes preserve historical work;
- the Add Patient preview and final materialization use the same selection object.

This is the highest-priority functional phase.

### Phase 3 — cluster support

Implement allocation unit, clusters, cluster assignment and inherited patient scheduling.

Definition of done:

- every cluster has at most one active assignment per schedule revision;
- a patient cannot select an arm that conflicts with their cluster;
- moving a patient between clusters is audited and reconciles only eligible future visits;
- no built-in randomization algorithm is claimed.

### Phase 4 — atomic schedule publication and revisions

Implement the batch publication endpoint, immutable operational revisions and pointer activation. Update the editor to use it.

Definition of done:

- a failed save never activates a partial schedule;
- design, options and templates are version-consistent;
- patient instances record the revision from which they were created;
- optimistic concurrency prevents one reviewer overwriting another silently.

### Phase 5 — migration, observability and rollout

Run dry-run/backfill reports, reconcile ambiguous trials, enable new behavior behind a feature flag, and monitor legacy fallbacks.

### Estimated engineering effort

These are implementation estimates, not regulatory-validation estimates. They assume one engineer familiar with the current FastAPI, MongoDB and Expo code, an available local test environment, and timely product decisions about masking and reassignment permissions.

| Phase | Estimated focused engineering time |
|---|---:|
| Phase 0 — regression tests | 1–2 developer-days |
| Phase 1 — design metadata/reconciliation | 3–5 developer-days |
| Phase 2 — assignment and materialization | 5–8 developer-days |
| Phase 3 — cluster support | 3–5 developer-days |
| Phase 4 — atomic publication/revisions | 4–7 developer-days |
| Phase 5 — migration/rollout | 3–5 developer-days |
| **Full implementation** | **19–32 developer-days** |

A realistic solo delivery window is approximately **4–7 calendar weeks**, including review and integration fixes. The high-priority safe core—Phases 0 through 2—should take approximately **9–15 developer-days**. Two engineers can parallelize backend domain/persistence work and frontend/workflow work, but the assignment contract and migration still require joint integration; elapsed time will not divide exactly in half.

Real-PDF gold-set evaluation, security review for blinded assignments, stakeholder acceptance and regulated validation are additional work and should not be hidden inside the coding estimate.

## 13. File-by-file implementation map

| File | Recommended change |
|---|---|
| `backend/study_design.py` | New pure models, mapping, reconciliation and validation |
| `backend/schedule_assignment.py` | New axes/options/applicability/filtering logic |
| `backend/schedule_schema.py` | Preserve branch/assignment identity and applicability in projection |
| `backend/protocol_agent.py` | Extract design dimensions/evidence and add design-topology audit checks |
| `backend/protocol_extraction.py` | Extend common extracted-details/provider contracts |
| `backend/server.py` | Wire trial fields, preview, assignment, revision publication and materialization; move new route logic into modules where practical |
| `frontend/app/(app)/sponsor/add-trial.tsx` | Review and submit design classification |
| `frontend/app/(app)/sponsor/visit-schedule.tsx` | Display options/applicability and publish atomically |
| `frontend/app/(app)/clinical/add-patient.tsx` | Select cluster/arm/sequence and send the same selection to preview/invite |
| `frontend/app/(app)/sponsor/trial-detail.tsx` | Display confirmed design and revision |
| `frontend/app/(app)/clinical/trial-summary.tsx` | Display role-appropriate design metadata |
| `docs/protocol-processing-and-schedule-formation-report.md` | Correct the current arm-filtering claim and document the implemented assignment flow |
| `backend/tests/test_study_design.py` | New 12-label/unknown-label normalization tests |
| `backend/tests/test_schedule_assignment.py` | New pure applicability and assignment tests |
| `backend/tests/test_visit_instances.py` | Assignment-aware materialization and reassignment tests |
| `backend/tests/test_protocol_creation.py` | Extraction/create/lookup round-trip tests |
| `backend/tests/test_schedule_definition_api.py` | Design/branch/applicability provenance tests |
| `backend/tests/test_schedule_edit.py` | Revision publication and propagation tests |
| `backend/eval/ctri_nov_2025_study_design_cases.json` | Input corpus for metadata contract coverage, not proof of schedule accuracy |

## 14. Test strategy

### 14.1 Taxonomy contract tests

Parameterize all 12 labels and assert normalized candidate dimensions. Add cases for:

- unknown future label;
- blank raw label;
- leading/trailing/case differences without rewriting the original;
- conflicting protocol design/schedule evidence;
- all 230 raw `Type of Study` values accepted without data loss;
- all 391 three-field combinations parsed and reconciled;
- total test-case record counts still equal 1,494.

### 14.2 Deterministic schedule-assignment fixtures

Create at least one operational fixture for:

- single arm;
- shared two-arm schedule;
- arm-specific parallel schedule;
- active-control and placebo-control roles;
- non-randomized multi-arm;
- 2-way and 3-way crossover sequences;
- 2x2 factorial combination assignment with factor-specific activities;
- cluster-level assignment;
- observational `Other` with no treatment assignment;
- conflicting/unknown design requiring review.

### 14.3 End-to-end API tests

For each applicable fixture:

1. create/extract trial metadata;
2. publish a schedule revision;
3. fetch assignment options;
4. preview with a selection;
5. invite/accept or directly enroll a participant;
6. assert materialized instances exactly match preview;
7. assert no other arm/sequence templates appear;
8. update assignment and confirm only eligible future instances change;
9. verify audit records and authorization.

### 14.4 Cluster tests

- cluster assignment inherited by all members;
- conflicting participant arm rejected;
- cluster reassignment requires authorization/reason;
- unassigned cluster blocks participant materialization;
- cluster identity does not expose unblinded treatment to unauthorized roles.

### 14.5 Publication tests

- invalid option reference rejects entire publication;
- partial database failure does not switch active revision;
- stale `If-Match` revision returns conflict;
- two concurrent reviewers cannot silently overwrite;
- old patient instances retain their source revision.

### 14.6 Real-protocol evaluation

The CTRI JSON contains metadata, titles and registry fields, not full protocol schedules. Because it is never uploaded at runtime, it can test offline terminology/taxonomy coverage only. It cannot prove protocol design extraction or visit-schedule accuracy.

End-to-end claims require representative PDFs or manually authored gold canonical schedules for each topology. Results should report:

- design-dimension accuracy;
- branch/option accuracy;
- visit and activity coverage;
- assignment applicability accuracy;
- unresolved/conflict rate;
- manual corrections required.

## 15. Acceptance criteria for “supports all 12 labels”

The system should claim support only when all of the following are true:

1. every raw label is accepted and preserved;
2. unknown future labels do not break ingestion;
3. normalized dimensions and conflicts are reviewable;
4. trial APIs and UI round-trip the classification;
5. canonical branch identity survives into published templates;
6. preview and materialization use the same stable assignment selections;
7. shared visits and selected option-specific visits are produced exactly once;
8. crossover sequence, factorial combination and cluster assignment are first class;
9. historical participant work survives reassignment/version changes safely;
10. publication is atomic/versioned;
11. permissions respect masking/unblinding requirements;
12. parameterized metadata and end-to-end schedule tests pass.

`Other` support means “preserve, classify from evidence, and route uncertainty safely,” not “pretend every Other record has the same design.”

## 16. Risks and safeguards

| Risk | Safeguard |
|---|---|
| Hardcoding the current 12 labels blocks future CTRI values | Preserve raw text; nullable canonical mapping; review unknowns |
| Protocol sections disagree with each other or with the extracted topology | Preserve each cited statement, deterministic conflict, human confirmation |
| Display-label changes break assignments | Use revision-scoped option IDs |
| Patient gets visits from another arm | Server-side applicability filtering before materialization |
| Cluster participant receives wrong arm | Derive assignment from cluster; reject mismatch |
| Reassignment destroys history | Preserve touched/past instances; reconcile future untouched only |
| UI preview differs from actual calendar | One shared selection object and one pure filtering/calculation path |
| Partial editor save activates incomplete schedule | Immutable staged revision plus atomic pointer swap |
| Treatment assignment leaks through UI/API | Role-aware serialization and explicit masking policy |
| AI asserts unsupported design | Evidence IDs, reconciliation and review-required status |

## 17. What not to do

Do not:

- add only a `Literal` containing the current 12 strings;
- convert `Other` into a single topology;
- infer design from title keywords alone;
- use `arm_label` as a foreign key;
- ask the frontend to enforce assignment without server validation;
- materialize visits before required assignment is present;
- build an unvalidated randomization algorithm as part of this change;
- overwrite immutable extraction provenance with human edits;
- activate branch-aware templates through sequential row writes;
- claim the 1,494-row metadata corpus proves schedule extraction accuracy.

## 18. Recommended delivery order

The safest order is:

1. add failing assignment/materialization tests;
2. implement and persist design metadata;
3. preserve branch/applicability IDs through projection and publication;
4. add participant arm/sequence assignment and fix materialization;
5. add cluster support;
6. introduce atomic published revisions;
7. migrate existing trials and participants;
8. evaluate representative protocols and enable the feature gradually.

If only one production change can be prioritized immediately, implement participant assignment plus server-side arm/sequence filtering first. It closes the most direct patient-schedule correctness gap. The design metadata work should follow in the same release train because it supplies the validation and audit context for that assignment.

## 19. Final recommendation

The current extraction architecture should be extended, not replaced. Its canonical graph, evidence links, deterministic projection and human-review boundary are strong foundations.

The missing layer is a durable bridge from **study design** to **assignment-aware operational scheduling**:

```text
protocol PDF evidence only
        -> normalized, reviewable study design + canonical schedule
        -> canonical schedule branches
        -> approved assignment axes/options + applicable templates
        -> participant or cluster assignment
        -> correctly filtered patient visit instances
```

Building that bridge makes the 12 known labels supportable and, more importantly, makes an unknown future design detectable and safe instead of rejected or silently misclassified.
