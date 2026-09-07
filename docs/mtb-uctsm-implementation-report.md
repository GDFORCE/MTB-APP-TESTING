# MTB / UCTSM — Implementation Report

**Date:** 2026-09-06 · **Scope:** spec sections 3, 4, 5, 6, 14 (partial), 18
**Architecture:** unchanged. One canonical model, one engine (UCTSM), one production
extraction path, one adapter. No new scheduling engine was created.

This report follows the required 12-part structure. Sections of the specification
that were **not** implemented are listed explicitly in part 9; nothing below claims
work that was not done.

---

## 1. CURRENT STATE — what already existed

Confirmed by reading and by execution, not assumed:

* **One live extraction pipeline** — `protocol_extraction.py` + `protocol_agent.py`
  (Gemini, LangGraph: classify → discover → evidence sweep → synthesize → audit →
  refine → finalize), emitting an evidence-linked `CanonicalSchedulePlan` v2.
* **One canonical engine** — `app/domain/schedule/` (evaluator, conditional engine,
  validator, deviation, parity, projection). It already implements anchors,
  dependency modes, recurrence with a rolling horizon, activity-level and intra-day
  timing, confinement episodes, applicability, and immutable versioning with
  `effective_from`.
* **The adapter already existed** — `app/services/canonical_import.py` +
  `canonical_bridge.py`, wired into Add Trial (`add-trial.tsx:294` →
  `POST /trials/{id}/uctsm-schedule`), idempotent on
  `canonical-import:{external_schedule_definition_id}`.
* **The UCTSM review/approval lifecycle existed** — validate → submit → per-field
  review decisions → approve, with immutability and a blocking-issue gate.

Three defects stopped that architecture from completing in production, plus one
validation gap. All four were identified in the prior audit; three are addressed
here.

---

## 2. CHANGES MADE — exact files

| File | Change |
|---|---|
| `backend/schedule_schema.py` | **New**: `ORIGIN_ANCHOR_TYPES`, `BaselineResolution`, `printed_study_day()`, `_AnchorView`/`_plan_view()`, `anchor_usage_counts()`, **`select_baseline_anchor()`**. `project_canonical_plan()` now calls the shared resolver instead of its own local rule, and warns when the origin is unresolved. |
| `backend/app/domain/schedule/models.py` | **New**: `SCHEDULE_ORIGIN_ROLE`, `schedule_origin_anchor()`. Extended `QualifierScope` (+`POPULATION`) and `QualifierCategory` (+`POPULATION_RESTRICTION`, `NOT_APPLICABLE`, `OTHER`). |
| `backend/app/services/canonical_import.py` | Uses `select_baseline_anchor()`; records the chosen origin on the anchor's `derivation_rule`; raises a blocking `AMBIGUOUS_BASELINE` issue when the origin is ambiguous; `default_anchor` is now nullable so an unresolved origin produces `UnresolvedTiming` rather than a guess. |
| `backend/app/services/operational_bridge.py` | `enroll_patient` finds Day 0 via `schedule_origin_anchor()` instead of matching the literal name/type `"BASELINE"`. |
| `backend/app/services/schedule_service.py` | **New**: `ScheduleReviewService.resolve_qualifier()` — the footnote-resolution workflow with `ReviewDecision` + `AuditEvent`. |
| `backend/app/api/uctsm.py` | **New**: `QualifierResolutionIn`; `POST /schedule-versions/{id}/qualifiers/{qid}/resolve`; `GET /schedule-versions/{id}/qualifiers`. |
| `backend/server.py` | Fixed an unreachable accountability guard in `add_patient` and `invite_patient_for_enrollment` (see part 2b). |
| `backend/tests/conftest.py` | **New** — makes the suite runnable as one job. |
| `backend/tests/test_baseline_resolution.py` | **New** — 20 tests. |
| `backend/tests/test_uctsm_qualifier_resolution.py` | **New** — 10 tests. |
| `frontend/src/features/uctsm/api.ts` | **New**: `QualifierRow`, `getScheduleQualifiers()`, `resolveScheduleQualifier()`. |
| `frontend/src/features/uctsm/ProtocolReviewScreen.tsx` | **New**: "Footnotes and Qualifiers" section — marker, source text, target, evidence (page/section/quote), category chooser, mandatory reason, and Accept / Not applicable / Escalate. |

### 2b. A defect found by fixing the harness

`add_patient` and `invite_patient_for_enrollment` back-filled `pi_id` from another
patient in the trial (or any org colleague with role `pi`) **before** checking
`if user['role'] in ('smo','site') and not pi_id: raise 400`. The guard was
therefore unreachable: an SMO/site org admin who named no PI got `200`, and a
colleague was silently assigned clinical responsibility for a patient they had
never accepted. Fixed by not defaulting the PI for org-admin enrollers.
Covered by the pre-existing `test_smo_org_admin_must_select_same_org_pi`, which
had never been able to run.

---

## 3. DATA MODEL CHANGES

Additive only. No migration is required: the new enum values are additive, and
`Anchor.derivation_rule` is an existing nullable JSON column.

| Model | Field | Meaning |
|---|---|---|
| `schedule_schema.BaselineResolution` | *(new type)* | `status` ∈ RESOLVED / UNRESOLVED / ABSENT, plus `anchor_id`, `anchor_type`, `source_label`, `evidence_ids`, `reason`, `alternatives[]`, `requires_review`. An UNRESOLVED result **carries no anchor**, so a caller cannot date a schedule off a guess. |
| `Anchor.derivation_rule` | `{role: "SCHEDULE_ORIGIN", resolver, reason, protocol_anchor_type, source_label}` | Which anchor is Day 0 and why — recorded **with** the protocol's own anchor type, never rewritten to a synthetic `BASELINE`. |
| `QualifierScope` | `+ POPULATION` | A footnote can restrict a population, not only an arm. |
| `QualifierCategory` | `+ POPULATION_RESTRICTION, NOT_APPLICABLE, OTHER` | `NOT_APPLICABLE` records a reviewer's ruling that a marker does not govern this schedule — deliberately distinct from `UNRESOLVED`. |

---

## 4. EXTRACTION CHANGES

**None.** No change was made to the Gemini/LangGraph extraction pipeline, its
prompts, or `CanonicalSchedulePlan`'s field set.

The origin decision now reads two things the extraction **already produced** and
which were previously ignored: `ScheduleAnchor.anchor_type` (semantic) and
`ScheduleAnchor.source_label` (the printed study day). Nothing new is asked of the
model, so no re-extraction or prompt validation is implied.

Spec §5's richer qualifier extraction (marker, scope, per-cell targets from the
document) is **not** implemented — see part 9.

---

## 5. ADAPTER CHANGES — canonical → UCTSM mappings

| Canonical | UCTSM | Transformation | Status |
|---|---|---|---|
| `plan.anchors[]` + `plan.events[].timing.anchor_id` | `Anchor.derivation_rule.role = SCHEDULE_ORIGIN` | `select_baseline_anchor()` | **new** |
| `anchor.anchor_type` | `derivation_rule.protocol_anchor_type` | preserved verbatim | **new** |
| ambiguous origin | `ValidationIssue(AMBIGUOUS_BASELINE, blocking)` + every anchor `status="AMBIGUOUS"` | refuse, list alternatives | **new** |
| unanchored `offset` timing with unresolved origin | `UnresolvedTiming` | previously defaulted to `anchors[0]` | **changed** |
| plan with no anchors | synthetic `BASELINE` anchor, `protocol_anchor_type: "none"` | unchanged behaviour, now explicitly recorded | **clarified** |

**The origin rule**, in decreasing order of authority — all of it read from the
document, never assumed:

1. only anchors typed `randomization`, `first_dose`, `cycle_start`, `period_start`
   are candidates (consent, screening, last dose, discharge, progression measure
   something else and are never promoted to Day 0);
2. among candidates, the **study day the protocol prints** on the anchor
   ("Day 0 of each period" → 0) wins — an origin is the lowest study day by
   definition, and this module already treats printed day text as ground truth;
3. then the position of the first event that counts from it (the schedule's own
   column order);
4. then anchor-type precedence;
5. if two candidates are still identical on all of the above → **UNRESOLVED**.

If no anchor is origin-typed: a single anchor resolves (nothing to choose between);
two or more → UNRESOLVED.

---

## 6. UI CHANGES

`frontend/src/features/uctsm/ProtocolReviewScreen.tsx` — a new **Footnotes and
Qualifiers** section, on the screen that already exists for "the decisions that
block approval". Per unresolved qualifier it shows the marker as printed, the
source text, what it is attached to, its scope, and its evidence (page, section,
quoted text — answering "why does MTB think this applies here?"). The reviewer
picks what kind of rule it is, must type a reason, and chooses **Accept this
meaning** / **Not applicable to this trial** / **Escalate — leave unresolved**.

No new screens. `frontend/src/features/uctsm/api.ts` gained the two calls.

---

## 7. TESTS ADDED — 30 backend tests

**`tests/test_baseline_resolution.py` (20)**

* origin is independent of the anchor's printed name — parametrised over
  `Baseline`, `First Dose`, `Randomisation`, `Day 1`
* each origin type resolves — `randomization`, `first_dose`, `cycle_start`,
  `period_start`
* a non-origin anchor (consent) is never promoted to Day 0
* a single `Enrollment` anchor resolves even though it is not origin-typed
* two equally plausible origins → UNRESOLVED, carries no anchor, lists alternatives
* an ambiguous origin leaves the flat projection undated and says why
* an ambiguous origin raises a blocking `AMBIGUOUS_BASELINE` on the canonical draft
* **legacy Day 0 == canonical Day 0** — parametrised over 5 protocol shapes
* the canonical origin records how it was chosen, and `first_dose` survives as
  `FIRST_DOSE` rather than being flattened to `BASELINE`
* a plan with no anchors states its origin explicitly

**`tests/test_uctsm_qualifier_resolution.py` (10)**

* an extracted footnote starts unresolved and blocks
* accepting clears the block and records `ReviewDecision` + `AuditEvent` with
  before/after, reviewer and reason
* editing replaces the reviewer's own reading
* marking not-applicable is a decision, not a deletion (the protocol's words survive)
* escalating deliberately keeps the block
* a resolution must state a meaning (category ≠ UNRESOLVED)
* a resolution must record why
* an unknown decision is refused
* an approved version cannot have its footnotes rewritten
* **a protocol with a footnote can now be approved** — previously impossible

---

## 8. TEST RESULTS — before vs after

Same command both times: `python -m pytest tests/ -q -p no:randomly`.

| | Before | After |
|---|---|---|
| Passed | 589 | **861** |
| Failed | 68 | **26** |
| Errors | 233 | **12** |
| Skipped | 1 | 1 |
| Runtime | 2m 26s | 9m 56s (261 more tests actually execute) |

Frontend: `npm run test:uctsm` → **87 passed, 0 failed**.

### Infrastructure vs clinical, as §3 requires

**Infrastructure failures (before): 233 errors + ~45 failures.** Every Mongo test
module opened its own event loop at import (`LOOP = asyncio.new_event_loop()`) and
closed it at teardown; there was no `conftest.py`. Motor binds its client to the
first loop it is used on, so the first module to touch Mongo won and every later
module errored at setup — while the same files passed when run alone (verified:
`test_visit_instances.py` alone → 41 passed; `test_password_recovery.py` alone →
3 passed). `tests/conftest.py` now shares one session loop and neutralises the
per-module close. **No assertion was weakened, nothing was skipped or xfailed.**

**Clinical/logic failures fixed: 1.** The SMO PI-accountability guard (part 2b).

**Remaining 38, all pre-existing and none introduced by this work:**

| Count | File | Category | Note |
|---|---|---|---|
| 23 | `test_mtb_backend.py` | **infrastructure** | Targets a dead deployment: `BASE = …code-viewer-87.preview.emergentagent.com`. Every failure is a 404. Left untouched — §3 forbids skipping, and converting it to in-process ASGI is a rewrite of a legacy smoke suite (3 of its tests need a real WebSocket server). |
| 12 | `test_audit_scoping.py` | **pre-existing, needs investigation** | All 12 error in the fixture with `403 You do not have access to enroll patients in this trial`, raised by `_can_access_trial` with a `pi`-role fixture. Untouched by any change here. Previously masked as loop errors. |
| 2 | `test_clinical_dashboards.py` | **pre-existing** | Dashboard count mismatches; reproduce standalone. Likely shared-Atlas data pollution — the Mongo suite writes to a live shared database with no per-run isolation. |
| 1 | `test_sponsor_dashboard.py` | **pre-existing** | Site persistence/scoping; reproduces standalone. |

---

## 9. REMAINING GAPS — specification sections NOT implemented

Stated plainly. None of the following was started.

| § | Requirement | Status |
|---|---|---|
| 5 | Richer qualifier **extraction** — marker, scope, per-cell/row/column targets, orphan-marker and orphan-footnote detection on the live path | **not done.** The resolution workflow (§6) is complete, but markers still arrive `null` because `CanonicalSchedulePlan` has no footnote-marker field and the Gemini prompts do not emit one. `app/extraction/completeness.py` implements orphan detection and is still wired only to the unreachable Claude pipeline. |
| 7 | Window semantics — validity / lookback / min-gap / max-gap preserved distinctly | **not done.** `_window_for` still maps every stated window to a tolerance `Window`. "Lab within 28 days" still becomes ±28 days. |
| 8 | Cycle-specific applicability (`occurrence_numbers`) | **not done.** Still read by the flat projection and ignored by the adapter. |
| 9 | Arm/cohort/branch applicability (`applies_to_branch_ids`) | **not done.** Only `event.arm_id`/`period_id` map across. |
| 10 | Conditional definitions/actions extraction → UCTSM | **not done.** Engine + UI + persistence exist; nothing produces them. |
| 11 | Confinement extraction → UCTSM | **not done.** Same shape. |
| 12 | Intra-day activity timing into the patient/CRC view | **not done.** `bridge_visit_documents` still projects activity names only. |
| 13 | One canonical event-type vocabulary | **not done.** Three vocabularies still coexist (`canonical_import` / `validator` agree; `schedule_projection` differs). It fails safe today. |
| 14 | Human review beyond footnotes; explicit bulk-confirm labelling | **partial.** Footnote review is now genuine and audited. `ProtocolScheduleScreen.confirmFields` still bulk-confirms every field of every event on one press and is **not** yet labelled or audited as a bulk action. |
| 15 | Reminder delivery consumer | **not done.** No scheduler or worker exists; `project_notification_candidate` still has only a test as its caller. |
| 16 | Calendar consuming the canonical schedule | **partial, pre-existing.** `/calendar/team` reads `visit_instances`, which carry canonical dates for linked patients. No change made. |
| 17 | Evidence page numbers on imported schedules | **not done.** `_EvidenceIndex` still leaves `Evidence.page_number` null, so the new qualifier panel shows section/quote but usually no page. |
| 26 | Dynamic enrolment fields | **not done.** |
| 29 | Adapter information-loss validation | **not done.** |
| 31/32 | Real-protocol benchmark and the A–Z test protocols | **not done.** See part 11. |

---

## 10. CLINICAL SAFETY RISKS

**Closed by this work**

* Patients could be enrolled onto a schedule dated from the wrong anchor, or fail
  to enrol at all (the canonical path refuses to fall back to legacy, so the
  patient got **no schedule**). Both paths now agree on Day 0 by construction.
* An ambiguous origin previously produced dates silently; it now refuses and lists
  the alternatives.
* Clinical responsibility could be assigned to a PI who never accepted it.

**Still open**

1. **Window semantics (§7) — highest remaining risk.** A validity window becomes a
   visit tolerance. That is a materially wrong permission about when a procedure
   may occur, and it is silent.
2. **Cycle and arm restrictions (§8/§9) are dropped at the adapter.** "MRI every
   second cycle" and "MRI only in Arm B" reach UCTSM as unrestricted. Silent.
3. **Confinement (§11) imports as ordinary visits**, so an admitted patient can in
   principle be reminded to travel on an internal study day.
4. **Bulk confirmation (§14)** still lets a reviewer satisfy the per-field review
   gate with one press, and the audit trail records it as individual confirmations.
5. **Extraction accuracy is unmeasured against real protocols** (§31).
6. **`ApproximateTiming`** imports and can be approved, then renders `UNRESOLVED`
   for every patient.

---

## 11. REAL PROTOCOL VALIDATION

**Zero real protocols were evaluated.** No benchmark was built.

This is unchanged from the audit: `eval/FINDINGS.md` states the Tier-3 corpus run
is blocked on API billing, and `eval/judge.py:6` records that no per-file expected
output is hand-authored. Building the §31 gold standard needs protocol PDFs and
manual clinical review — procurement and human time, not engineering time. It
should start in parallel with the remaining engineering, because every accuracy
claim depends on it.

Offline extraction tests (27 expansion + 29 invariants) pass, but they are
structural-coherence checks on synthetic plans and must not be reported as
real-world accuracy.

---

## 12. FINAL READINESS

### NOT READY

Justification. Two of the three production blockers are closed and the Add
Trial → approve → enrol path can now complete for a protocol whose origin is
resolvable and whose footnotes are reviewed. But:

* clinically meaningful information is still dropped silently at the adapter —
  window semantics, cycle restrictions, arm restrictions, confinement (§7–§11);
* there is no information-loss gate to catch that (§29);
* no reminder is ever delivered (§15);
* extraction has never been measured against a real Schedule of Assessments (§31);
* 38 tests remain red, 12 of them a pre-existing failure that has not been
  diagnosed.

Recommended next order: §29 (loss detection first, so the remaining mappings
cannot regress silently) → §7 → §8/§9 → §17 → §13 → §14 → §15, with the §31
benchmark running in parallel from now.
