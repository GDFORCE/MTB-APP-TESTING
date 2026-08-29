# Protocol Extraction: Current Approach and How It Handles Any Case

**System:** My Trial Board (MTB)
**Scope:** what changed in the AI extraction pipeline recently, and exactly how the system behaves when it meets a protocol design it has no specific rule for.
**Report date:** 25 August 2026
**Primary implementation:** `backend/protocol_agent.py`, `backend/protocol_document_index.py`, `backend/protocol_extraction.py`, `backend/schedule_schema.py`

> This document supersedes §7.6–7.11 of `docs/protocol-processing-and-schedule-formation-report.md`, which still describes an older architecture (a keyword-scored timing/visit-evidence pair of stages, plus a separate "independent confirmation" stage). Both were replaced by the design described below. Everything else in that report — canonical schema, deterministic projection, human review, patient materialization — is unaffected and still accurate.

---

## 1. What changed, and why

Two structural changes landed on top of the original decomposed-agent design. Both are visible directly in the current `build_schedule_extraction_graph()` in `protocol_agent.py`.

### 1.1 Evidence gathering: keyword-scored excerpt → full-document sweep

**Before:** two single-shot stages (`timing_node`, `visit_evidence_node`) each worked from a keyword-scored ~24-page excerpt of the document. A fact stated only in prose, on a page that never matched the scorer's vocabulary (a dose-modification rule, a rule mentioned once outside any table), could be silently missed.

**Now:** one stage, `evidence_sweep_node` (`protocol_agent.py:1353`), reads the **entire** document. `chunk_protocol_pages()` (`protocol_document_index.py`) partitions every page of the PDF into fixed-size chunks (default 22 pages "core" each, `PROTOCOL_EVIDENCE_CHUNK_CORE_PAGES`), with a 4-page overlap on each side (`PROTOCOL_EVIDENCE_CHUNK_OVERLAP_PAGES`) so a table or governing rule straddling a chunk boundary is still legible. One AI call runs per chunk, in parallel, extracting both timing facts and visit/activity facts from its own core pages only — a page belongs to exactly one chunk's responsibility, so a fact is never claimed twice. Every chunk's evidence IDs are namespaced (`chunk0-...`, `chunk1-...`) before the results are merged into the same `ScheduleTimingEvidence` / `ScheduleVisitEvidence` shapes the rest of the pipeline already expects — nothing downstream had to change.

Coverage is now a **structural guarantee** ("every page belongs to exactly one chunk"), not a scoring outcome. That's the single biggest reliability change: a rule stated once, in prose, on an odd page, is now read by construction rather than found by luck.

The evidence-sweep prompt (`_CHUNK_EVIDENCE_PROMPT`) also carries specific instructions earned from real failure modes:
- **Population-gated activities** (e.g. "EQ-5D only for US/Germany/France subjects," "pregnancy test only for women of childbearing potential") must preserve the exact gating condition, not get flattened to "some subjects."
- **Multi-day confinement/housing blocks** (an inpatient PK/BA-BE stay: check-in day, pre-dose-only housing days, dosing+intensive-PK day, discharge day) should be captured as one fact per differentiated day when the source states distinct activities per day, not compressed into a single start/end range.
- A conditional/triggered visit ("repeat if ANC < 1000," "extend by 1 week if toxicity persists") is still a real fact even though its date can't be resolved to a fixed offset — capture it with the trigger condition preserved verbatim.

### 1.2 Verification: independent second synthesis → deterministic checks feeding one audit

**Before:** after synthesis, a *second*, separately-run synthesis stage ("confirm") built another schedule from the same evidence, and deterministic Python diffed the two drafts for disagreements. That diff was fed into the audit stage alongside the builder draft.

**Now:** the "confirm" stage is gone entirely — there is no second AI-authored schedule anymore. In its place, `audit_node` (`protocol_agent.py:1540`) runs three **deterministic, code-only** checks against the single builder draft:

| Check | What it catches |
|---|---|
| `_validate_evidence_links()` | Every timing, window, activity, arm, period, and canonical-graph field (anchors, events, activities, recurrences, transitions, conditions, conflicts) must cite a real evidence ID from the sweep's own catalog, in the right category, above the confidence threshold. Unsupported or wrongly-typed citations are flagged. |
| `_structural_issues()` | Malformed graph structure — e.g. `schedule_kind="none"` but visits were produced (or vice versa), duplicate compiled visit rows, and anything `expand_schedule()`'s own validation already flags. |
| `_visit_coverage_issues()` | Compares the final visit count against the number of distinct `visit_columns` facts the evidence sweep inventoried. This is the deterministic catch for the single most common failure mode — a wide, plainly-numbered table (Week 4, 8, 12, 16...) silently collapsed down to a handful of "representative" visits. The audit LLM is explicitly not trusted to catch this alone, since it's never shown the full column inventory. |

These results are handed to the audit prompt labeled explicitly as **"confirmed real defects, not a hypothesis"** — a deliberate change from before, where the diff between two independently-generated drafts was treated as a noisy disagreement signal that still needed AI judgment to interpret. The comment in the code states the reasoning directly: the old confirm-diff signal was "two independently generated schedules phrased things differently," which is weaker evidence than a mechanically-computed structural defect.

Net effect: **one fewer full-schedule AI generation per extraction** (cheaper, faster), while the specific failure mode confirmation used to catch (dropped visit columns, broken evidence links) is now caught more reliably by code that can't hallucinate or disagree with itself.

### 1.3 Minor fix: Gemini PDF context cache vs. per-stage system instructions

Gemini's API rejects a request that sets both `cached_content` and `system_instruction` in the same call. Since every stage in this pipeline uses a different `system_instruction`, it can't be baked into the shared per-extraction PDF cache. The fix (`protocol_extraction.py`, `GeminiProtocolExtractor`): when a cache is active, the stage's system instruction is folded into the prompt text of that call instead of passed as a separate parameter — the cache still holds only the (large) PDF bytes, and every stage keeps its own distinct instructions.

---

## 2. The current pipeline, precisely

```
classify
  │
  ├─ (classifier AND discovery both say "no schedule") ──► no_schedule ──► finalize
  │
  ▼
discover
  ▼
evidence_sweep   (full-document, chunked, parallel — see §1.1)
  ▼
synthesize       (one AI call authors the canonical schedule graph)
  ▼
audit ◄────────────────────────────┐   (AI semantic audit + the 3 deterministic
  │                                 │    checks from §1.2, scored across 6 dimensions)
  ├─ accepted AND no deterministic  │
  │  issues ──────────────────────►│──► finalize
  │                                 │
  ├─ refinement_count >= max_refinements (default 2) ──► finalize
  │                                 │
  └─ otherwise ──► refine ─────────┘
                    (repair prompt; re-enters audit)
```

Nodes: `classify`, `needs_selection`, `discover`, `evidence_sweep`, `no_schedule`, `synthesize`, `audit`, `refine`, `finalize` (`protocol_agent.py:1749-1757`). The stage previously called "repair" is now the `refine` node — same correction prompt, renamed to match its role in the simplified loop.

`classify` and `discover` still receive an additional keyword-scored page excerpt as a locating aid (their job is finding *where* things are, a lower-stakes task). `synthesize`, `audit`, and `refine` do not — they rely on the full evidence-sweep pool plus whatever the provider's own PDF attachment gives them, since a scored excerpt on top of a full sweep would be redundant cost with no coverage benefit (`protocol_agent.py:690-707`).

---

## 3. How the system handles a case it has no specific rule for

This is the mechanism, not marketing — every part below is a real code path, not an aspiration.

### 3.1 Classification is a hint, never a gate

`classify` guesses a document type and a set of schedule archetypes (`linear, cyclic, crossover, factorial, multi_arm, multi_phase, event_driven, intra_day, long_term_extension, mixed`) from `DocumentTaskClassification` (`schedule_schema.py:72-105`). But `_classification_guidance()` injects **all ten** archetype rules into every synthesis/audit/refine call unconditionally — the classifier's guess is never used to withhold a rule (`protocol_agent.py:873-906`):

> Classify runs first with the least evidence of any stage in the pipeline; if it guesses one shape and later evidence shows another (or a blend — a cyclic regimen inside a multi-arm design, a crossover with an intra-day PK block), a gate here would silently withhold the rules needed to model it correctly.

So a design that blends patterns doesn't need to have been anticipated as its own named category — the model applies whichever of the ten composable rules its own evidence supports, simultaneously. `mixed` exists explicitly as the instruction to "model each part with the structure the protocol actually prints instead of forcing one shape."

### 3.2 The universal fallback for anything unclear is "unresolved," never a guess

The `TIMING SHAPE RULE`, which applies to every timing object in the schedule (`protocol_agent.py:681`):

> Choose the kind from what the source actually supplies... Procedure prose with no number and no anchor — "pre-dose," "at each visit," "as clinically indicated" — must use kind `unresolved` with the exact wording preserved. Never label such a value offset or relative and leave its companion field empty.

The same logic is enforced structurally, not just by prompt instruction: a `WindowSpec` asserted as `stated` with no actual magnitude is automatically downgraded to `unclear` by a Pydantic validator (`schedule_schema.py:208-217`) — the system can't accidentally accept "there's a window, trust me" with no number.

Every fact also requires a real `evidence_id` pointing at an actual page quote from the sweep's catalog; `_validate_evidence_links()` mechanically rejects anything that doesn't (§1.2). Nothing can be asserted with no source.

### 3.3 Contradictions are recorded, not silently resolved

Anything the model finds two conflicting source statements about becomes a `ScheduleConflict` object (`schedule_schema.py:345-351`) with `status: unresolved` — visible to the human reviewer, not picked-for-you.

### 3.4 The multi-dimensional acceptance gate

`ScheduleAudit.accepted` (`protocol_agent.py:130-144`) is a computed property, not a single yes/no from the model:

```python
accepted = (
    approved
    and all(dimension.accepted for dimension in [
        visit_coverage, timing, windows, visit_types,
        procedure_mapping, overall_schedule])
    and not any(issue.severity in ("critical", "major") for issue in issues)
)
```

And each individual dimension's own `accepted` (`protocol_agent.py:98-102`) requires `passed=True` **and** `accuracy >= MIN_ACCEPT_CONFIDENCE` (default 0.75, `PROTOCOL_EXTRACTION_MIN_CONFIDENCE`). One weak dimension — say the model is confident about visit coverage but unsure about windows — is enough to fail the whole gate. There is no dimension that can be skipped or averaged away; a `not applicable` dimension (nothing of that kind in this protocol) auto-passes, everything else has to clear the bar on its own.

### 3.5 The refinement loop, and its exit

If the audit doesn't accept, or any deterministic issue was found, `refine` (the repair prompt) gets the candidate schedule, the deterministic issues, the full audit output, and the evidence — and produces a corrected draft, which re-enters `audit`. This repeats until either the gate is cleared or `PROTOCOL_EXTRACTION_MAX_REFINEMENTS` rounds (default 2) are exhausted — whichever comes first. There is no infinite retry.

### 3.6 The actual answer for "never seen before": it never refuses, and it never silently trusts itself

Put together, a genuinely novel structure is handled the same way an ordinary one is:

1. The full-document sweep still reads every page, regardless of whether the design matches any archetype.
2. Synthesis composes whatever combination of the ten structural rules the evidence actually supports; anything that fits none of them becomes `unresolved` timing or a recorded `conflict`, never a fabricated value.
3. Audit is scored the same way regardless of design familiarity — six independently-gated dimensions, plus code-only structural/evidence/coverage checks that can't be talked out of a real defect.
4. `finalize_node` (`protocol_agent.py:1644-1693`) sets `verification_status = "verified"` **only if** the audit was accepted **and** there are zero deterministic issues **and** deterministic expansion itself didn't already require review — any single failure defaults to `needs_review`.
5. Regardless of that status, nothing becomes a real trial schedule or touches a patient until a human opens the editor and explicitly saves it.

So there's no separate code path for "I don't recognize this" — an exotic design simply has a lower chance of clearing every gate in step 4, which routes it to `needs_review` more often. The system doesn't need to know it's looking at something new; it only needs the evidence, the composable rules, and the audit gate to do their job, and it's built so that uncertainty defaults to "ask a human" rather than "guess confidently."

---

## 4. What this does *not* solve

Being honest about the edges, since the mechanism above is real but not unlimited:

- **Provider-dependent.** This entire graph — classify, full sweep, synthesize, audit, refine — only runs for the **Gemini** provider (`GeminiProtocolExtractor.extract_all`, `protocol_extraction.py:1564`). The `.env` in this repo currently sets `PROTOCOL_EXTRACTION_PROVIDER=gemini`, so it's what's live. The Claude, OpenRouter/DeepSeek, and local Ollama extractors are explicitly single-shot — literally commented `"no classification stage"` in their own `extract_all()` (`protocol_extraction.py:1205-1208, 1834-1837`) — no archetype composition, no evidence sweep, no audit/refine loop. Switching provider loses every mechanism described in §3.
- **No language-specific handling.** Nothing in any prompt addresses non-English protocols. It relies entirely on Gemini's native multilingual reading, untested and unguided by any rule in this codebase.
- **No real OCR step**, though the raw PDF bytes are always attached to the Gemini call too, so scanned pages are frequently still readable via the model's own vision — the deterministic page indexer just can't verify that itself, so it flags those pages for extra scrutiny rather than skipping them.
- **Hard caps still apply** regardless of design complexity: 25 MB file size, 400 visits in the final expanded output (truncates + warns beyond that), open-ended cycles preview-capped at 12 occurrences unless the protocol states a total.
- **`verified` is a self-consistency claim, not regulatory sign-off.** It means the builder, the deterministic checks, and the audit all agreed with each other under the current evidence — never that a person has confirmed it.

---

## 5. Source map

| Responsibility | Location |
|---|---|
| Full-document evidence sweep, chunking, merge | `protocol_agent.py:1353` (`evidence_sweep_node`); `protocol_document_index.py` (`chunk_protocol_pages`, `render_page_chunk`) |
| Archetype rules (composable, always active) | `protocol_agent.py:813-870` (`_ARCHETYPE_GUIDANCE`) |
| Classification-as-hint injection | `protocol_agent.py:873-935` (`_classification_guidance`) |
| Deterministic audit-feeding checks | `protocol_agent.py:988` (`_validate_evidence_links`), `:1122` (`_visit_coverage_issues`), `:1157` (`_structural_issues`) |
| Audit acceptance gate | `protocol_agent.py:82-144` (`ScheduleAccuracyDimension`, `ScheduleAudit`) |
| Graph wiring | `protocol_agent.py:1749-1779` (`build_schedule_extraction_graph`) |
| Gemini PDF-cache / system-instruction fix | `protocol_extraction.py`, `GeminiProtocolExtractor._cached_generate` |
| Provider capability gap | `protocol_extraction.py:1202-1208` (Claude), `:1831-1837` (OpenRouter), `1848+` (Ollama) |
