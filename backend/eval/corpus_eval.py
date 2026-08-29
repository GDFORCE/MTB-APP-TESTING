"""Real-protocol extraction eval harness.

Runs every real protocol PDF in the corpus through the live extractor, grades
each with an independent LLM judge (see judge.py), and writes a scorecard so we
can measure and iterate on robustness across the full spread of schedule types
(single-arm, multi-arm, cyclic, crossover, week/month-based, hour-based,
visit-only, and non-schedule documents that must extract to empty).

Run from backend/:
  ./.venv/Scripts/python.exe eval/corpus_eval.py            # all files
  ./.venv/Scripts/python.exe eval/corpus_eval.py 1 2 30     # only files 1, 2, 30

Env:
  GEMINI_API_KEY           required (in backend/.env)
  PROTOCOL_CORPUS_DIR      corpus folder (default: the Patient Visit Schedules dir)
  EVAL_CONCURRENCY         parallel files (default 4)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time

from dotenv import load_dotenv

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EVAL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BACKEND)
sys.path.insert(0, _EVAL)
load_dotenv(os.path.join(_BACKEND, ".env"))

import invariants as inv  # noqa: E402
import protocol_extraction as pe  # noqa: E402
import judge as jd  # noqa: E402

CORPUS_DIR = os.getenv(
    "PROTOCOL_CORPUS_DIR",
    r"D:\Patient Visit Schedules-20260429T140317Z-3-001\Patient Visit Schedules",
)
RESULTS_DIR = os.path.join(_EVAL, "results")
CONCURRENCY = int(os.getenv("EVAL_CONCURRENCY", "4"))

# --nojudge: run the extractor only (no paid grader call). Halves API cost —
# extractions are dumped to results.json and graded by hand against the PDFs.
NO_JUDGE = "--nojudge" in sys.argv

# Corpus = top-level PDFs whose name starts with "<number>." — the 62 numbered
# protocols. No space required after the dot (file 4 is "4.110_Protocol_...pdf").
_NUM_RE = re.compile(r"^(\d+)\.")

# Full multi-hundred-page protocols live in a subfolder. They are the hardest
# and most representative case — the Schedule of Assessments is an appendix and
# the cycle length lives in prose dozens of pages earlier — so they belong in
# the eval, not outside it. Skipped by number filters (they have no number).
FULL_PROTOCOL_DIR = os.path.join(CORPUS_DIR, "full_protocol")

# That folder is user-curated and also holds unrelated documents: a short CV and
# some large scanned answer keys. Neither filter alone is enough — the CV is
# small but innocuously named, the scans are big but recognisably named — so
# require BOTH a full-protocol-sized file and a name that isn't obviously
# something else. Override with PROTOCOL_FULL_FILES (comma-separated names).
FULL_PROTOCOL_MIN_BYTES = 200 * 1024
_NOT_A_PROTOCOL = re.compile(
    r"(resume|\bcv\b|curriculum|proof|question|answer|invoice|receipt)", re.I)


def discover(only: set[int] | None = None) -> list[str]:
    files = []
    for name in os.listdir(CORPUS_DIR):
        m = _NUM_RE.match(name)
        p = os.path.join(CORPUS_DIR, name)
        if not (m and os.path.isfile(p) and name.lower().endswith(".pdf")):
            continue
        if only is not None and int(m.group(1)) not in only:
            continue
        files.append(p)
    files.sort(key=lambda p: int(_NUM_RE.match(os.path.basename(p)).group(1)))

    if only is None and os.path.isdir(FULL_PROTOCOL_DIR):
        override = [n.strip() for n in os.getenv("PROTOCOL_FULL_FILES", "").split(",")
                    if n.strip()]
        for n in sorted(os.listdir(FULL_PROTOCOL_DIR)):
            p = os.path.join(FULL_PROTOCOL_DIR, n)
            if not (n.lower().endswith(".pdf") and os.path.isfile(p)):
                continue
            if override:
                if n in override:
                    files.append(p)
                continue
            if (os.path.getsize(p) >= FULL_PROTOCOL_MIN_BYTES
                    and not _NOT_A_PROTOCOL.search(n)):
                files.append(p)
    return files


async def run_one(sem: asyncio.Semaphore, path: str) -> dict:
    async with sem:
        name = os.path.basename(path)
        with open(path, "rb") as f:
            data = f.read()
        rec: dict = {"file": name, "kb": round(len(data) / 1024)}
        t0 = time.time()
        try:
            sched = await pe.get_extractor().extract(data)
            visits = [v.model_dump() for v in sched.visits]
            rec["n_extracted"] = len(visits)
            rec["visits"] = visits
            # Carry the reviewer-facing signals through to the scorecard.
            rec["schedule_kind"] = sched.schedule_kind
            rec["assumptions"] = sched.assumptions
            rec["source_notes"] = sched.source_notes
            # Free, offline structural check — runs on every extraction whether
            # or not the paid judge does.
            rec["invariants"] = inv.check_record(rec)
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"extract: {type(e).__name__}: {e}"
            rec["secs"] = round(time.time() - t0, 1)
            print(f"[X] {name}: EXTRACT ERROR — {e}")
            return rec
        if NO_JUDGE:
            rec["secs"] = round(time.time() - t0, 1)
            lines = [f"\n===== {name}  ({len(visits)} visits, {rec['secs']}s) ====="]
            for v in visits:
                d = "  ?" if v["day_offset"] is None else f"{v['day_offset']:>3}"
                span = f"..{v['day_end']}" if v.get("day_end") is not None else ""
                wb, wa = v.get("window_before"), v.get("window_after")
                win = f"+/-{v['window_days']}" if wb is None and wa is None else f"-{wb}/+{wa}"
                typ = f"[{v['visit_type']}] " if v.get("visit_type") else ""
                acts = ", ".join(v["activities"])
                lines.append(f"  d{d}{span:<4} {win:<8} {typ}{v['name']}  | {acts}")
            print("\n".join(lines))
            return rec
        try:
            g = await jd.grade(data, json.dumps({"visits": visits}, ensure_ascii=False))
            rec["grade"] = g.model_dump()
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"judge: {type(e).__name__}: {e}"
            rec["secs"] = round(time.time() - t0, 1)
            print(f"[?] {name}: JUDGE ERROR — {e}")
            return rec
        rec["secs"] = round(time.time() - t0, 1)
        mark = {"pass": "PASS", "partial": "PART", "fail": "FAIL"}.get(g.verdict, "????")
        print(f"[{mark}] {name[:52]:52} n={len(visits):>2} src~{g.visits_in_source:>2} "
              f"days={g.day_offsets:<13} halluc={g.hallucination:<5} {g.doc_kind}")
        return rec


async def main() -> None:
    only = {int(a) for a in sys.argv[1:] if a.isdigit()} or None
    files = discover(only)
    if not files:
        print(f"No corpus files found in {CORPUS_DIR}")
        return
    print(f"Corpus: {len(files)} protocol PDFs  |  extractor={pe.DEFAULT_MODEL}  "
          f"judge={jd.JUDGE_MODEL}  concurrency={CONCURRENCY}\n")
    sem = asyncio.Semaphore(CONCURRENCY)
    recs = await asyncio.gather(*(run_one(sem, p) for p in files))
    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_json = os.path.join(RESULTS_DIR, "results.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(recs, f, indent=2, ensure_ascii=False)
    clean, viol_lines = inv.report(recs)
    n_ok = sum(1 for r in recs if "error" not in r)
    if viol_lines:
        print("\n--- structural invariant violations ---")
        print("\n".join(viol_lines))
    print(f"\nstructurally clean: {clean}/{n_ok}")

    if NO_JUDGE:
        print(f"Extracted {n_ok}/{len(recs)} (no paid judge). Results: {out_json}")
        return
    write_scorecard(recs)


def _verdict(rec: dict) -> str:
    if "error" in rec:
        return "error"
    return rec.get("grade", {}).get("verdict", "error")


def write_scorecard(recs: list[dict]) -> None:
    n = len(recs)
    counts = {"pass": 0, "partial": 0, "fail": 0, "error": 0}
    for r in recs:
        counts[_verdict(r)] += 1
    lines = ["# Protocol extraction — corpus scorecard", ""]
    lines.append(f"Extractor **{pe.DEFAULT_MODEL}** · judge **{jd.JUDGE_MODEL}** · "
                 f"{n} real protocol PDFs")
    passed = counts["pass"]
    lines.append("")
    lines.append(f"**PASS {passed}/{n} ({100*passed//n if n else 0}%)** — "
                 f"partial {counts['partial']}, fail {counts['fail']}, error {counts['error']}")
    lines.append("")
    lines.append("| # | File | Verdict | n | src | days | activities | halluc | issues |")
    lines.append("|---|------|---------|---|-----|------|------------|--------|--------|")
    for r in recs:
        m = _NUM_RE.match(r["file"])
        num = m.group(1) if m else "?"
        v = _verdict(r)
        g = r.get("grade", {})
        if "error" in r:
            issues = r["error"]
            lines.append(f"| {num} | {r['file'][:40]} | **error** | "
                         f"{r.get('n_extracted','-')} | - | - | - | - | {issues} |")
            continue
        issues = "; ".join(g.get("issues", []))[:80]
        lines.append(
            f"| {num} | {r['file'][:40]} | {v} | {r.get('n_extracted','-')} | "
            f"{g.get('visits_in_source','-')} | {g.get('day_offsets','-')} | "
            f"{g.get('activities','-')} | {g.get('hallucination','-')} | {issues} |")
    lines.append("")
    lines.append("## Not-yet-passing (partial/fail/error)")
    for r in recs:
        if _verdict(r) == "pass":
            continue
        g = r.get("grade", {})
        issues = r.get("error") or "; ".join(g.get("issues", [])) or "(no issues listed)"
        lines.append(f"- **{r['file']}** — {_verdict(r)}: {issues}")
    out = os.path.join(RESULTS_DIR, "scorecard.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nPASS {passed}/{n}  (partial {counts['partial']}, fail {counts['fail']}, "
          f"error {counts['error']})")
    print(f"Scorecard: {out}")


if __name__ == "__main__":
    asyncio.run(main())
