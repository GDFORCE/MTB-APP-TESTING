"""Protocol-extraction eval harness.

Generates synthetic Schedule-of-Assessments PDFs for the main clinical-trial
schedule archetypes, runs each through the live extractor, and prints the
extracted flat visit list so you can eyeball correctness (absolute day offsets,
names, windows, activities, cyclic enumeration, multi-arm disambiguation).

Run from backend/:  ./.venv/Scripts/python.exe eval/run_eval.py
Requires GEMINI_API_KEY in backend/.env and `pip install fpdf2`.
"""
import asyncio
import os
import sys

from dotenv import load_dotenv

# Resolve backend/ regardless of CWD so imports + .env work.
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND)
load_dotenv(os.path.join(_BACKEND, ".env"))

from fpdf import FPDF  # noqa: E402
import protocol_extraction as pe  # noqa: E402


def make_pdf(title: str, header: list[str], rows: list[list[str]], note: str = "") -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 8, "Schedule of Assessments", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 7)
    w = 190 / len(header)
    for h in header:
        pdf.cell(w, 6, h, border=1)
    pdf.ln()
    for r in rows:
        for c in r:
            pdf.cell(w, 6, c, border=1)
        pdf.ln()
    if note:
        pdf.ln(2)
        pdf.set_font("Helvetica", "I", 6)
        pdf.multi_cell(0, 4, note)
    return bytes(pdf.output())


# ── Archetypes ───────────────────────────────────────────────────────────────
CASES = {
    "normal-single-arm": make_pdf(
        "Study ABC-101 (Phase II, single arm)",
        ["Assessment", "Screen D-14", "Baseline D1", "Wk4", "Wk8", "EoT Wk12"],
        [["Consent", "X", "", "", "", ""],
         ["Vitals", "X", "X", "X", "X", "X"],
         ["Blood draw", "X", "X", "X", "", "X"],
         ["ECG", "X", "", "X", "", "X"],
         ["Window", "-", "+/-3d", "+/-3d", "+/-3d", "+/-7d"]]),

    "cyclic-oncology": make_pdf(
        "Study ONC-202 (3 cycles x 21 days)",
        ["Assessment", "C1D1", "C1D8", "C2D1", "C2D8", "C3D1", "C3D8"],
        [["Dosing", "X", "X", "X", "X", "X", "X"],
         ["Vitals", "X", "X", "X", "X", "X", "X"],
         ["Tumor scan", "X", "", "", "", "X", ""],
         ["Window", "-", "+/-2d", "+/-2d", "+/-2d", "+/-2d", "+/-2d"]],
        note="Treatment administered in 21-day cycles for up to 3 cycles."),

    "parallel-2-arm-shared-soa": make_pdf(
        "Study CVD-303 (2 arms: Drug vs Placebo, shared SoA)",
        ["Assessment", "Screen D-28", "Rand D1", "Wk2", "Wk6", "Wk12"],
        [["Consent", "X", "", "", "", ""],
         ["Randomize", "", "X", "", "", ""],
         ["Vitals", "X", "X", "X", "X", "X"],
         ["Labs", "X", "X", "", "X", "X"],
         ["Window", "-", "+/-0", "+/-3d", "+/-5d", "+/-7d"]],
        note="Both arms follow the identical visit schedule above; only study "
             "drug differs. Do not duplicate visits per arm."),

    "multi-arm-divergent": make_pdf(
        "Study NEU-404 (Arm A weekly, Arm B monthly)",
        ["Assessment", "Arm A Wk1", "Arm A Wk2", "Arm A Wk4",
         "Arm B Mo1", "Arm B Mo2"],
        [["Dosing", "X", "X", "X", "X", "X"],
         ["Vitals", "X", "X", "X", "X", "X"],
         ["MRI", "", "", "X", "", "X"],
         ["Window", "+/-2d", "+/-2d", "+/-3d", "+/-7d", "+/-7d"]],
        note="Arm A is dosed weekly; Arm B is dosed monthly. The two arms have "
             "different visit schedules."),
}


async def main() -> None:
    ex = pe.get_extractor()
    if not getattr(ex, "configured", True):
        print("GEMINI_API_KEY not set — extraction disabled. Aborting.")
        return
    for name, data in CASES.items():
        r = await ex.extract(data)
        print(f"\n=== {name}: {len(r.visits)} visits ===")
        for v in r.visits:
            print(f"  d{v.day_offset:>4} +/-{v.window_days}  {v.name:26} | "
                  f"{', '.join(v.activities)}")


if __name__ == "__main__":
    asyncio.run(main())
