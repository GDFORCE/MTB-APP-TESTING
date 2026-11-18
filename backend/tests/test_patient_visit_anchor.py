"""Regression test for the patient visit-schedule anchor fallback.

`_patient_visit_anchor` is a pure function (no I/O), so unlike most of this
test suite it does not need the live-Atlas harness used in test_foundation.py
and test_visit_instances.py.

Documented bug (docs/protocol-processing-and-schedule-formation-report.md
section 19, point 7): a patient with neither a valid baseline_date nor a
valid enrolled_date previously received a visit schedule silently anchored to
the current time ("now"), fabricating a scheduling input instead of flagging
the patient record for review. This contradicts the system's own stated
principle elsewhere of never inventing a date when information is missing.

The fix: `_patient_visit_anchor` now raises ValueError instead of falling back
to now(); every call site already has (or has been given) a try/except that
treats this exactly like any other calculation failure -- the visit/instance
becomes `manual_review` with an explicit reason, never a fabricated date.
"""
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

# These tests exercise one pure function and never touch the database, so a
# real Mongo/JWT configuration is not required -- only values that let
# `import server` succeed (Motor's client construction is lazy).
os.environ.setdefault('MONGO_URL', 'mongodb://127.0.0.1:1/test-anchor-fallback')
os.environ.setdefault('DB_NAME', 'test-anchor-fallback')
os.environ.setdefault('JWT_SECRET', 'test-secret')
os.environ.setdefault('JWT_REFRESH_SECRET', 'test-refresh-secret')

import pytest  # noqa: E402

import server  # noqa: E402


def test_baseline_date_is_preferred_and_made_timezone_aware():
    anchor = server._patient_visit_anchor({
        'baseline_date': '2026-01-01', 'enrolled_date': '2025-06-01',
    })
    assert anchor.isoformat() == '2026-01-01T00:00:00+00:00'


def test_enrolled_date_is_used_when_baseline_is_absent():
    anchor = server._patient_visit_anchor({
        'baseline_date': None, 'enrolled_date': '2026-02-15',
    })
    assert anchor.isoformat() == '2026-02-15T00:00:00+00:00'


def test_missing_both_dates_raises_instead_of_fabricating_now():
    with pytest.raises(ValueError, match='no valid baseline or enrollment date'):
        server._patient_visit_anchor({'baseline_date': None, 'enrolled_date': None})


def test_malformed_date_strings_on_both_fields_also_raise():
    with pytest.raises(ValueError, match='no valid baseline or enrollment date'):
        server._patient_visit_anchor({
            'baseline_date': 'not-a-real-date', 'enrolled_date': 'also-not-a-date',
        })


def test_missing_patient_fields_entirely_also_raise():
    with pytest.raises(ValueError, match='no valid baseline or enrollment date'):
        server._patient_visit_anchor({})
