# Evaluating OKF (Open Knowledge Format) as the Solution for MTB

**Question asked:** "OKF will be the solution because we are failing in most of the cases."
**Verdict:** **No.** OKF does not address any of MTB's documented failure classes, and adopting it as the schedule representation would be a regression. There *is* a narrow, genuine use for it (§7), and there *is* an open standard that is the right answer to the question you were probably actually asking (§6).

Date: 31 August 2026. Sources are linked in §9.

---

## 1. What OKF actually is

OKF = **Open Knowledge Format**, published by the Google Cloud Data Cloud team on **13 June 2026**, currently at **v0.1**.

It is a convention for writing a knowledge base as **a directory of markdown files with YAML frontmatter**, cross-linked with ordinary markdown links, so that AI agents can read an organization's context without a vendor SDK.

```
sales/
├── index.md
├── tables/
│   ├── orders.md          # ---
│   └── customers.md       # type: BigQuery Table
└── metrics/               # title: Orders
    └── weekly_active_users.md
```

Design principles, per the spec: *minimally opinionated*, *producer/consumer independence*, *format not platform*. The **only always-required frontmatter key is `type`** — "a concept carrying just `type` is fully conformant."

### What the spec explicitly does **not** provide

Taken directly from the OKF specification's own non-goals and field definitions:

| Capability | In OKF? |
|---|---|
| Schema validation / enforcement | **No** |
| A type system with enforced types | **No** |
| Referential integrity between documents | **No** |
| Versioning or immutability semantics | **No** |
| Executable or computable rule representation | **No** |
| A fixed taxonomy of concept types | **No — explicit non-goal** |

The spec states its non-goals as: *"Defining a fixed taxonomy of concept types. Prescribing storage, serving, or query infrastructure. **Replacing domain-specific schemas** (Avro, Protobuf, OpenAPI, and so on)."*

That last clause is decisive. OKF's authors say plainly that it is not meant to replace a domain schema. MTB's core artifact — `UniversalSchedule` — *is* a domain schema.

---

## 2. What MTB is actually failing at

This is drawn from the repo's own audit documents, not from speculation. Counting the finalized requirement set in `docs/mtb-requirements-traceability.md` (89 requirements):

| Status | Count |
|---|---|
| Implemented | **5** |
| Partially Implemented | 41 |
| Missing | 35 |
| Conflicting | 4 |
| Unsafe | 4 |

So the premise "we are failing in most of the cases" is **correct** — 5 of 89 requirements are fully done. But the *shape* of that failure matters enormously, because it determines whether OKF is relevant. Sorting the failures by root cause gives four distinct classes:

### Class A — The canonical model cannot represent the clinical construct

The extractor reads it correctly; there is nowhere to put it.

- **CONF-01…07 (Missing):** no confinement episode → study-day → activity hierarchy. A 4-day inpatient PK stay has no representation as one episode, so it becomes false return visits.
- **QUAL-02, QUAL-03, QUAL-04 (Missing):** no `QualifierScope`/`QualifierTarget` entity. Table footnote markers have no scope model (CELL/ROW/COLUMN/VISIT/ARM/…), no category model, and no completeness validator — so footnotes are silently lost.
- **COND-02, COND-03 (Missing):** conditions are *expression gates only*. There is no `ConditionalRule`/`Action`/`Resolution` model, so "repeat weekly until ANC recovers, then resume" cannot be stored as a rule at all.
- **DEP-01 (Missing):** no `DependencyMode` enum. The system cannot distinguish NOMINAL from ACTUAL_PREVIOUS_EVENT from MANUAL from UNCLEAR.
- **ACT-02…04 (Partial/Missing):** no activity anchor registry, no `PatientActivityOccurrence`. Intra-day timing ("PK draw at infusion end +30 min") is representable but not evaluated or persisted.

### Class B — A lossy compatibility bridge destroys structure that *was* extracted

`_schedule_extraction_payload()` (`backend/server.py:3789`) flattens the rich `ExtractedSchedule` into the legacy editor row contract. `OPEN_ENDED_CYCLE_CAP = 12` (`backend/protocol_extraction.py:105`) expands an open-ended q21d regimen into 12 concrete rows.

The traceability matrix flags this as **REP-01, Conflicting**: *"`OPEN_ENDED_CYCLE_CAP=12` currently reads like finite rows"* — a technical preview cap is being presented as the protocol's schedule. **QUAL-06 is marked Unsafe** for the same reason: *"Evidence is dropped in some flat transformations."*

### Class C — Two competing schedule models, and the weaker one is authoritative

The readiness report names this as the central risk: *"The richest model is not the one driving active patient care."* MongoDB flat `visits` rows run production; the typed `UniversalSchedule` (PostgreSQL/UCTSM) is *"a parallel subsystem… not linked to the MongoDB trial/patient identities used by the product, and its primary screens are currently unreachable."*

### Class D — Actively unsafe date logic

- **ANCH-03 (Unsafe):** `_patient_visit_anchor()` fell back to *now* when the anchor was missing. (The handoff notes this fallback has since been removed.)
- **DEP-02 (Unsafe):** the extractor may infer actual-previous dependency purely from cycle length, producing unjustified date shifts.
- **APP-03 (Unsafe):** arm/substudy applicability is free-text string equality, so invalid Part→Cohort combinations are accepted.

### Now map OKF against those four classes

| Failure class | Does OKF address it? |
|---|---|
| A — model can't represent the construct | **No.** OKF has no type system and explicitly declines to define one. Writing a confinement episode as markdown does not make it evaluable. |
| B — lossy bridge drops structure | **No.** Adding a third representation to a system already broken by having two is strictly worse. |
| C — dual model, wrong one authoritative | **No — actively harmful.** |
| D — unsafe date arithmetic | **No.** This is deterministic evaluator logic; OKF has no execution model. |

**OKF addresses zero of the four.**

---

## 3. Why adopting OKF as the schedule representation would be a regression

MTB's current architecture derives its safety from exactly the properties OKF omits:

| MTB safety invariant | Mechanism today | Under OKF |
|---|---|---|
| Timing is a discriminated union, not a bag of optionals | Pydantic `type`-keyed union (ABSOLUTE/OFFSET/CYCLE_DAY/TRIGGERED/UNRESOLVED/…) | Untyped YAML; `type` is a free string with no validation |
| Unknown stays unknown, never coerced to a date | Kleene three-valued logic (TRUE/FALSE/UNKNOWN) in the evaluator | No execution model at all |
| Approved versions are immutable | PostgreSQL triggers + service locks + `APPROVED` has no edit transition | No versioning or immutability semantics |
| Every clinical rule cites evidence | `ClaimEvidence` with field paths, `_validate_evidence_links()` rejects fabrication | `sources` is an optional, unvalidated frontmatter key |
| Anchors/events referenced must exist in the same version | Referential validation stage + cycle detection | No referential integrity |
| Windows can't be asserted without a magnitude | Pydantic validator downgrades `stated` → `unclear` | No constraint enforcement |

Under a GxP / 21 CFR Part 11 posture, replacing enforced constraints with unvalidated markdown is not a neutral trade — it removes the mechanisms that let you claim a schedule is reproducible and attributable. `docs/universal-schedule-engine-architecture.md` §11 lists these as the definition of done. OKF would invalidate most of that list.

---

## 4. The measurement problem — the actual blocker

This is the most important finding, and it precedes any technology choice.

`docs/protocol-extraction-demo-notes.md` §18 states, verbatim:

> 1. Real-PDF ground-truth accuracy across the supplied corpus has not yet been established for the current design.
> 2. Model-reported audit scores are not calibrated clinical accuracy estimates.

And §17:

> **"Is the reported 0.75 score a measured accuracy?"** No. It is the current model-audit acceptance threshold. Measured accuracy needs a manually labeled real-PDF evaluation set and must be reported separately.

The demo notes even instruct: *"Do not claim '95% proven accuracy' during the demo."*

**Consequence:** "we are failing in most of the cases" is currently a felt impression, not a measured result. You cannot tell which of the four failure classes dominates, you cannot rank the 35 Missing requirements by real-world frequency, and — critically — **you would not be able to tell whether OKF, or anything else, improved matters.** Any large architectural bet made now is unfalsifiable.

A labeled evaluation set of 15–25 real protocols, scored per requirement class, is cheaper than any of the options in §5 and is a precondition for all of them.

---

## 5. What would actually move the numbers

Ranked by (documented failure volume × implementation cost):

1. **Close the Class C split.** Bridge operational Mongo trial/patient identity to UCTSM schedule versions and make the typed graph authoritative. This is the enabling step for ~40 of the 89 requirements; almost nothing else can be finished while two models compete.
2. **Delete the Class B lossy bridge.** Stop consuming the recurrence rule at the editor boundary (REP-01). Keep the rule; render 3–4 cycles as a *labeled preview*. Carry qualifier/evidence IDs through every projection (QUAL-06).
3. **Extend the canonical domain for Class A constructs.** Confinement episode hierarchy, `QualifierScope`/`Target`/category, `ConditionalRule`/`Action`/`Resolution`, `DependencyMode`, activity anchors. These are schema additions to `backend/app/domain/schedule/models.py` plus validators — precisely the work OKF cannot do for you.
4. **Fix Class D unsafely-inferred dates.** DEP-02 (require source wording or mark unclear) and APP-03 (coded dimensions with parent-child constraints).
5. **Extraction improvements last.** The evidence sweep already gives a *structural* coverage guarantee — every page belongs to exactly one chunk. Retrieval is not your bottleneck. The bottleneck is that there is nowhere to put what is read.

---

## 6. The standard you probably want: CDISC USDM

If the intent behind "open knowledge framework" was *"we should adopt an open standard instead of inventing our own schedule model"* — that instinct is sound, but OKF is the wrong standard. The domain-correct one is:

**USDM — Unified Study Definitions Model**, from the CDISC / TransCelerate **Digital Data Flow (DDF)** initiative.

- **Open**, MIT-licensed, on GitHub at `cdisc-org/usdm`, with a pip-installable Python package.
- Models exactly MTB's problem domain: **Arms, Epochs, Encounters, Activities, ScheduleTimeline, ScheduledActivityInstance, Timing (absolute/relative + windows), Elements, Conditions and Decision Points (timeline branching), Populations, Interventions.**
- The `ScheduleTimeline` is explicitly *"the content of the study schedule of activities."*

The mapping to MTB is close to one-to-one: `Encounter`≈`EventDefinition`, `Epoch`≈`Epoch`, `Timing`≈your timing union, `ScheduledDecisionInstance`≈the conditional-branching model you are missing (COND-08). **Class A gaps are largely gaps against USDM.** Using USDM as the design reference for those additions gives you a vetted model instead of a bespoke one, plus downstream interoperability with EDC/CTMS and SDTM.

Two important caveats, so this isn't oversold:

- **USDM is not mature.** The reference package's own README warns: *"This package was, originally, not intended for public use and, consequently, only informal testing has been performed… Formal testing has just begun."* It also needs a CDISC Library API key for controlled terminology.
- **The SOA gap is being worked on right now.** CDISC launched the **Schedule of Activities (SOA) project** on **18 June 2026**, built on USDM, aiming at *"a standardized, computable approach to SOA design"*, explicitly targeting *"reliance on free text and footnotes"* and enabling *"automation capabilities like scheduling and deviation detection."* That is a near-verbatim description of MTB's QUAL-01…06 and ANCH-11 requirements.

**Recommended posture:** treat USDM as an **alignment target and design reference**, not an immediate replacement for `UniversalSchedule`. Name your new entities after USDM concepts, keep an export mapping, and track the SOA project. Do not rewrite onto a v0.x model that admits it is barely tested — your own model currently has stronger validation than the standard does.

---

## 7. Where OKF *does* legitimately fit in MTB

There is one real, if modest, use — and it is worth doing.

Today, the domain knowledge that steers extraction is **hard-coded as Python string constants** in `backend/protocol_agent.py`: `_ARCHETYPE_GUIDANCE` (the ten composable archetype rules, `:813-870`), `_CHUNK_EVIDENCE_PROMPT` (carrying hard-won rules about population-gated activities, confinement day differentiation, triggered visits), `_classification_guidance` (`:873-935`), and the TIMING SHAPE RULE (`:681`).

That knowledge is: clinical, curated, growing from real failure cases, edited by domain people, and versioned only as source diffs inside a large Python file.

OKF is a reasonable format for externalizing it:

```
knowledge/
├── archetypes/{cyclic,crossover,confinement,...}.md
├── qualifiers/footnote-idioms.md
├── glossary/{anc,c1d1,q21d,...}.md
└── corrections/            # reviewer corrections harvested as reusable rules
```

Benefits that are real: clinical reviewers can edit rules without touching Python; each rule gets `sources`/`status`/`stale_after` provenance; rules become individually diffable and reviewable; the corpus can be shared across providers (helping the Claude/OpenRouter/Ollama paths that currently lack the Gemini graph entirely).

**But be clear about the size of the prize.** This improves *prompt-asset management*. It maps to **zero rows** in the traceability matrix. It will not close a single Missing/Unsafe/Conflicting requirement. Do it as hygiene after §5.1–5.3, not instead of them.

---

## 8. Bottom line

| Claim | Assessment |
|---|---|
| "We are failing in most of the cases" | **True** — 5 of 89 finalized requirements fully implemented. |
| "…therefore OKF is the solution" | **False.** OKF addresses none of the four documented failure classes. |
| Root cause of the failures | Canonical model can't represent key clinical constructs; a lossy bridge destroys what *is* extracted; two competing models with the weaker one authoritative. Not a knowledge-retrieval problem. |
| Biggest immediate risk | No ground-truth accuracy measurement exists, so no architectural bet is falsifiable. |
| Right open standard, if one is wanted | **CDISC USDM** (+ the CDISC SOA project, launched June 2026) — as an alignment target, not a rewrite. |
| Legitimate use for OKF | Externalizing hard-coded extraction prompt knowledge from `protocol_agent.py`. Real but small; closes no requirement. |

---

## 9. Sources

- [How the Open Knowledge Format can improve data sharing — Google Cloud Blog](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing)
- [OKF specification — GoogleCloudPlatform/knowledge-catalog](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf) ([SPEC.md](https://raw.githubusercontent.com/GoogleCloudPlatform/knowledge-catalog/main/okf/SPEC.md))
- [What is OKF? Understanding Google's Open Knowledge Format — GitBook](https://www.gitbook.com/blog/what-is-okf-open-knowledge-format)
- [Open Knowledge Format (OKF) — Domo glossary](https://www.domo.com/glossary/open-knowledge-format)
- [CDISC USDM reference implementation — cdisc-org/usdm](https://github.com/cdisc-org/usdm)
- [Advancing Schedule of Activities with USDM: CDISC SOA Project Launch](https://www.cdisc.org/events/webinar/advancing-schedule-activities-usdm-cdisc-soa-project-launch)
- [USDM in action – from protocol to SDTM](https://d4k.dk/2024/08/09/usdm-in-action_-from-protocol-to-sdtm/)
- [Clinical Trial Schedule of Activities Specification Using FHIR Definitional Resources](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12583939/)

Internal evidence: `docs/mtb-requirements-traceability.md`, `docs/mtb-codebase-readiness-report.md`, `docs/universal-schedule-engine-architecture.md`, `docs/protocol-extraction-current-approach-and-case-handling.md`, `docs/protocol-extraction-demo-notes.md` §17–18, `backend/server.py:3789`, `backend/protocol_extraction.py:105`, `backend/protocol_agent.py:681,813-935`.
