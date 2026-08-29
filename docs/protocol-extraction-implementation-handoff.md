# Protocol Extraction Upgrade — Implementation Handoff

Last updated: 2026-08-17 (follow-up session)

## Objective

Build an evidence-backed protocol schedule extractor that handles the schedule
patterns observed across the 11 supplied protocols without inventing missing
facts. The system must preserve exact protocol meaning, produce reviewable
evidence, and continue supporting the current Expo mobile schedule editor.

This document is the continuation point for another coding agent. Read it
together with `docs/protocol-schedule-schema-analysis.md` before changing code.

## Non-negotiable behavior

1. `canonical_plan` is the schedule source of truth for AI extraction.
2. The AI must not independently author a second flat schedule. Legacy/mobile
   rows must be generated deterministically from `canonical_plan`.
3. Unknown or contradictory facts remain unresolved and require review; never
   manufacture a value such as a default `±3 days` window.
4. Keep these concepts separate:
   - source timing expression;
   - calculated day/date, when calculable;
   - visit-level early/late tolerance;
   - activity/procedure timing and tolerance;
   - operational constraints such as housing, washout, infusion duration, dose
     holds, minimum gaps, and conditional rules.
5. Calendar months and years must not be converted to 30/365 days. Resolve them
   against a real patient anchor date so month lengths and leap years are correct.
6. Every AI-populated fact must link to source evidence. Evidence confidence is
   not the same thing as measured extraction accuracy.
7. A failed AI stage must not discard successful earlier stages.
8. Do not claim 95% accuracy until the 11 real PDFs are evaluated against
   manually verified expected schedules.

## Completed before this upgrade

- Multi-stage Gemini graph: classification, discovery, timing evidence, visit
  evidence, builder, confirmer, audit, bounded repair.
- Evidence IDs and verification metadata.
- Version-2 canonical schema with anchors, phases, branches, events, activities,
  recurrence, transitions, conditions, conflicts, calendar amounts and windows.
- Canonical draft persistence in the `schedule_definitions` collection.
- Backward-compatible extraction API response containing current flat `visits`.
- Calendar date helper covering 28/29/30/31-day months and leap years.
- Schema and agent unit tests. Earlier focused run passed 81 tests; five
  integration setup errors were caused by MongoDB not running.

## Changes completed in the current upgrade

- Added `project_canonical_plan()` in `backend/schedule_schema.py`.
  It deterministically produces mobile-compatible visit rows from canonical
  events, recurrence, branches, activities, windows and transitions.
- Procedure timing/windows are emitted as structured `procedures` and readable
  `operational_constraints`, not copied into the visit-tolerance field.
- Added `CanonicalScheduleResponse` in `backend/protocol_extraction.py` so Gemini
  can return one compact canonical schedule instead of two schedules.
- Gemini uses `CanonicalScheduleResponse` when an agent stage requests
  `ExtractedSchedule`.
- `expand_schedule()` treats an existing canonical plan as authoritative and
  ignores conflicting model-authored flat rows. Historical flat-only callers
  still receive a canonical fallback.
- Added `procedures`, `operational_constraints`, `canonical_event_id`, and
  row-level review metadata to extracted visits.
- Added `operational_constraints` to visit create/update API models and schedule
  version diff fields.
- Extraction API now preserves projection warnings when deciding review status.
- Added regression cases for procedure versus visit windows, calendar-month
  timing, and canonical precedence.

## Work completed in the follow-up session (2026-08-17)

The three parallel tasks landed and their integration gaps are closed.

### Deterministic PDF retrieval is wired into the agent graph

- `protocol_agent` builds one `ProtocolDocumentIndex` per extraction and keeps
  it in graph state; every stage renders its own task-scoped page selection
  (`classification`, `schedule_discovery`, `timing`, `activities`, `review`).
- Each stage prompt now opens with a `RETRIEVED SOURCE PAGES` packet carrying
  page numbers, `evidence_id` values, `document_sha256`, the omitted-page count
  and retrieval warnings, followed by an explicit statement that the attached
  PDF remains authoritative for anything the selection left out.
- Rendered packets are cached per task inside a run, so the review selection is
  built once for builder, confirmer, auditor and repair.
- `PROTOCOL_PAGE_INDEX_CACHE_DIR` (documented in `backend/.env.example`) enables
  the content-addressed on-disk cache. Unset, the index is rebuilt per request.
- Failure is never fatal: an unindexable PDF, an unwritable cache directory or a
  retrieval error logs a warning and the graph continues from the attached PDF.
- `run_schedule_extraction_agent` / `run_protocol_extraction_agent` accept a
  prebuilt `page_index`; a mismatched `document_sha256` is rejected.

### Classification changes the workflow, not only the prompt text

- `_classification_guidance()` turns the classification into stage rules for
  document type (amendment, mixed bundle, synopsis, schedule-only, reference,
  unrelated), analysis task (amendment comparison, table-only, no schedule) and
  every archetype (cyclic, crossover, multi-arm, multi-phase, intra-day,
  event-driven, long-term extension, linear, mixed), plus attached-reference,
  version-comparison and complexity rules.
- The guidance reaches discovery, timing, visit evidence, builder, confirmer,
  auditor and repair — deliberately NOT the classifier itself.
- New `no_schedule` graph node: when the classifier AND the discovery map both
  report no schedule, timing/visit/builder/confirmer/audit/repair are skipped
  and an empty `schedule_kind: none` result is returned with an explicit
  assumption. If only one of the two says "no schedule", the full pipeline runs,
  so a real schedule is never discarded on one stage's misread.

### Projection edge cases corrected

- A qualified single day (`within 28 days prior`, `at least 21 days after`)
  keeps its boundary for ordering but is flagged `review_status: pending` with
  an explanatory operational constraint — it can no longer read as a confirmed
  appointment.
- A resolved range keeps both ends (`day_offset` + `day_end`), stays `ok`, and
  still records that it is a range rather than an exact day.
- An `unclear`/`conflicting` visit window no longer disappears: its source text
  is emitted as an operational constraint while the numeric window stays null.
- Relative `before` offsets remain negative and calendar recurrence stays
  undated (verified by regression tests, unchanged behaviour).

### Test coverage added

- `backend/tests/test_protocol_agent_routing.py` (14 tests) — page packet reaches
  every stage, retrieval/caching failure modes, foreign-index rejection,
  no-schedule routing, disagreement fallback, per-archetype guidance.
- `backend/tests/test_schedule_definition_api.py` (7 tests) — procedure timing
  never becomes a visit window, `operational_constraints` and `procedures`
  round-trip through visit create/update, canonical draft persistence is
  idempotent per source, and `GET /trials/{id}/schedule-definition` returns the
  canonical plan, evidence facts and compatibility rows.
- `backend/tests/test_protocol_pattern_regressions.py` grew from 7 to 16
  protocol-shaped fixtures, adding: bounded screening window, multi-day range,
  relative-before offset, per-visit window widening (±3/±5/±7) with a telephone
  visit, undated ET/unscheduled visits, bounded daily diary recurrence,
  independent overlapping cadences, same-day merge + lab-gated dose hold, and an
  amendment/version-lineage window conflict.
- Gemini structured-output tests already assert `CanonicalScheduleResponse`.

### Verification results

- Focused protocol suite: 151 passed.
- Schedule/projection suite after the projection changes: 109 passed.
- `test_schedule_definition_api.py` + `test_protocol_creation.py` +
  `test_schedule_edit.py`: 7 + 4 + others passed against a live MongoDB.
- Frontend: `npx tsc --noEmit` clean; `npm run test:visit-timing` 4/4 passed.
- `npx expo lint` cannot run in this environment — it shells out to `yarnpkg`,
  which is not installed. Not a code defect.
- `backend/tests/test_visit_instances.py` fails at `HEAD` as well: it expects the
  old instance-status vocabulary (`missed`/`upcoming`) while the server returns
  `overdue`/`due`/`planned`. Pre-existing and unrelated to protocol extraction.

## Remaining work

1. Real-PDF evaluation. Run the 11 protocols through the pipeline and score
   field-level accuracy and unresolved-rate against manually verified expected
   schedules. Until that exists, no accuracy percentage may be published.
2. Retrieval recall measurement. Confirm the selected pages actually contain the
   schedule evidence for all 11 protocols before lowering `max_pages`.
3. Scanned/mixed PDF route. `image_or_empty_pages` is surfaced in the packet but
   nothing yet renders only those pages to the vision model.
4. Retrieval/pipeline instrumentation: cache hit rate, pages per stage,
   scanned-page rate, provider retries, citation-validation failures.
5. Fix or retire `backend/tests/test_visit_instances.py` (status vocabulary).
6. Restart the backend and Expo Go, confirming the phone uses the current Metro
   port rather than a stale 8081/8082 process.

## Important current environment issue

The project is on `D:` with ample free space, but Windows `C:` reached 0 bytes.
Generated project caches and about 79 MB of disposable Windows Temp caches/logs
were removed. If commands fail with OS error 112, free more disposable `C:`
cache space or point test/runtime temp directories to a folder on `D:`. Do not
delete source files, PDFs, databases, user documents, or the virtual environment.

## Key files

- `backend/schedule_schema.py`
- `backend/protocol_document_index.py`
- `backend/protocol_extraction.py`
- `backend/protocol_agent.py`
- `backend/server.py`
- `backend/tests/test_schedule_schema_v2.py`
- `backend/tests/test_protocol_agent.py`
- `backend/tests/test_protocol_agent_routing.py`
- `backend/tests/test_protocol_pattern_regressions.py`
- `backend/tests/test_schedule_definition_api.py`
- `backend/tests/test_protocol_json_response.py`
- `frontend/app/(app)/sponsor/visit-schedule.tsx`
- `frontend/src/lib/visit-timing.ts`
- `docs/protocol-schedule-schema-analysis.md`

## Verification commands

Run from the repository root. Prefer a temporary directory on `D:` while `C:`
is constrained.

```powershell
$env:TEMP = 'D:\MTB_Intern_work_folder\UI-Demo\MTB-APP\.tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
.\backend\.venv\Scripts\python.exe -m pytest -q `
  backend/tests/test_schedule_schema_v2.py `
  backend/tests/test_protocol_agent.py `
  backend/tests/test_protocol_agent_routing.py `
  backend/tests/test_protocol_json_response.py `
  backend/tests/test_protocol_expansion.py `
  backend/tests/test_protocol_timing_contract.py `
  backend/tests/test_protocol_document_index.py `
  backend/tests/test_protocol_pattern_regressions.py `
  backend/tests/test_protocol_invariants.py
```

Then, with MongoDB available:

```powershell
.\backend\.venv\Scripts\python.exe -m pytest -q `
  backend/tests/test_protocol_creation.py `
  backend/tests/test_schedule_definition_api.py `
  backend/tests/test_schedule_edit.py
```

Frontend verification:

```powershell
cd frontend
npx tsc --noEmit
npm run test:visit-timing
```

`npx expo lint` currently fails in this environment because it invokes
`yarnpkg`, which is not installed. Install Yarn or run ESLint directly.

## Definition of done

- [x] One canonical AI schedule is deterministically projected everywhere.
- [x] Timing, visit tolerance and operational/procedure constraints display in
      the correct fields.
- [x] A transient provider failure retries/resumes only the failed stage.
- [x] Builder/confirmer canonical disagreements are detected field-by-field.
- [x] Classification changes the extraction behavior.
- [x] The supplied protocol pattern fixtures pass (16 fixtures).
- [ ] Real-PDF evaluation reports measured field-level accuracy and
      unresolved-rate. Not started; no accuracy claim may be made until it is.
