# Protocol → visit-schedule extraction — design & validation

## The problem this design solves

Real protocols **collapse repetition**, and the numbers needed to expand it are
**not in the schedule table**.

The reference protocol `Ptc_PICN_V1 A2.pdf` (CLR_10_13) is the clearest case.
Its Schedule of Assessments is Appendix-I on page 42, and its columns read:

```
Screening | Cycle 1 | IC-1 | IC-2 | IC-3 | Cycle 2 & Next Cycles | IC-1 | IC-2 | IC-3
```

Nine columns — but the real schedule is **~25 visits**. Everything needed to
expand "Cycle 2 & Next Cycles" is scattered across other pages:

| Needed value | Where it lives |
|---|---|
| 21-day cycle, max 6 cycles | p15 — "every 3 weekly for maximum 6 cycles" |
| Intra-cycle spacing (7 days) | p23 — "next visit 7-days after this visit" |
| The expansion rule | p24 — "in each cycle, intra-cycle visits 1, 2 and 3 ... will be conducted" |
| A *relative* anchor | p24 — "within 3 days after intra-cycle visit 3" |
| Conditional imaging (cycles 2/4/6 only) | pp4, 24, 30, 35 |

Extract the appendix faithfully and you get 9 rows. A patient enrolled against
that template gets a schedule wrong by 16 visits.

## The design: declare structure, don't enumerate

The previous schema asked the model for an already-flattened visit list, which
required it to do multi-page arithmetic in its head, silently, with no way to
check the result.

Now the model emits the **structure it read** — `repeating_blocks` with a cycle
length and member layout, `relative_to` anchors, `conditional_activities` — and
`expand_schedule()` does the arithmetic **in Python**, where it is deterministic
and unit-testable with no API key.

Three consequences:

- **Correctness is testable offline.** Cycle arithmetic is ordinary code.
- **Output stays small.** A 6-cycle protocol is ~8 rows + 1 block instead of 25
  enumerated rows, so long schedules no longer risk truncation.
- **The frontend contract is unchanged.** `extract()` returns an already-expanded
  flat `visits` list, so the visit-schedule editor consumes exactly what it always did.

Anything the server had to assume (an open-ended tail it bounded, a relative
anchor it could not resolve) is appended to `assumptions` and returned to the
sponsor. Extraction remains a **draft** — nothing is written without review.

## Structural taxonomy (measured, not assumed)

Signal prevalence across the 62 machine-readable corpus documents:

| Pattern | Docs | Representative file |
|---|---|---|
| Visit windows (±N days) | 37% | most |
| Week/month-based timing | 37% | most |
| Negative screening days | 29% | most |
| Telephonic visits | 24% | — |
| **Hour-level (intra-day) schedules** | 21% | 29. Fever-Synopsis |
| Cycle length stated in prose | 19% | 55. PICN synopsis |
| **Crossover / washout periods** | 15% | 2. SPIL Ipratropium |
| Early-termination / unscheduled | 15% | — |
| **Documents with NO schedule** (must extract to empty) | 15% | 30. GCP checklist |
| **Relative anchors** | 13% | 32. GS-US-330-1508 |
| Conditional per-cycle assessments | 11% | 48. Protocol |
| **Collapsed cycles** | 8% | 55. PICN, 48. Protocol |

Two cases drove specific design decisions:

- **`48. Protocol.pdf`** — *"imaging every 6th week for Cycles 1-6 and every 8th
  week thereafter"*: the cadence **changes mid-study** and the tail is
  **open-ended**. Modelled as two `repeating_blocks` with different
  `cycle_length_days`; the open tail is bounded (`OPEN_ENDED_CYCLE_CAP`) and flagged.
- **9 of 71 documents have no extractable text at all** — they are pure scans.
  Vision is mandatory; a text-only pipeline would silently return nothing for
  ~13% of real uploads.

## Validation

### Tier 1 — deterministic expansion (offline, no API key)

`tests/test_protocol_expansion.py` — **27 tests**, run in <1s. Cases are modelled
on the real protocols above: the PICN collapsed column expanding 9 → 25 visits
with correct day arithmetic, cadence changes, open-ended bounding, relative-anchor
chains, circular-reference termination, conditional activities landing only in
cycles 2/4/6, undated visits preserved and sorted last, duplicate collapsing,
runaway-expansion capping, and purity/repeatability.

### Tier 2 — structural invariants (offline, no API key)

`eval/invariants.py` + `tests/test_protocol_invariants.py` — **29 tests**.

The paid LLM judge answers *"is this extraction faithful to THIS document?"* —
accurate, but it can only grade documents someone paid to grade. Invariants
answer a cheaper, complementary question: *"is this extraction structurally
coherent at all?"* They fire on **any** schedule the system ever produces,
including protocols nobody has seen, and they run for free over `results.json`.

They catch: lost day offsets (every visit collapsed onto baseline), unexpanded
name templates leaking to the UI, duplicate bookings, non-chronological output,
impossible windows/day ranges, `schedule_kind` contradicting the visit list, and
screening visits with positive offsets. Two tests assert the two layers agree —
expansion output must satisfy every invariant, so the producer and the guard
cannot drift apart.

### Tier 3 — live corpus + LLM judge (requires API credit)

```
cd backend
./.venv/Scripts/python.exe eval/corpus_eval.py            # all 63 docs, judged
./.venv/Scripts/python.exe eval/corpus_eval.py --nojudge  # extract only, half cost
./.venv/Scripts/python.exe eval/corpus_eval.py 48 55      # specific files
./.venv/Scripts/python.exe eval/invariants.py             # re-check cached results, free
```

`discover()` now also includes `full_protocol/` — the hardest and most
representative case, previously excluded from the eval.

> **Status: not yet run against the new design.** The configured
> `ANTHROPIC_API_KEY` returns `400 — credit balance is too low`, so Tier 3 is
> blocked on billing, not on code. Tiers 1 and 2 (56 tests) pass offline today.
>
> An earlier revision of this file reported "4/4 archetypes pass". That result
> came from **four synthetic PDFs** generated to match the prompt's own
> assumptions (`eval/run_eval.py`) — it could not have discovered the collapsed-cycle
> problem, because no synthetic fixture collapsed a cycle. Treat Tier 3 numbers as
> unestablished until a real-corpus run is recorded here.

## Known limitations

- **Open-ended protocols** ("until disease progression") are expanded to a bounded
  number of cycles and flagged. There is no correct automatic answer; the sponsor
  must set the real number.
- **Degraded scans.** Vision handles clean scans well, but a heavily degraded
  assessment table may still be misread. The prompt instructs the model to record
  uncertainty in `assumptions` rather than guess.
- **Truly divergent multi-arm** schedules rely on the model reading arm structure
  from the table; unusual layouts may need a human pass.
- Review-before-save is the safety net throughout — extraction never auto-writes.
