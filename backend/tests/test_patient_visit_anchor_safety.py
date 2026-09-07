from datetime import date, datetime, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server


def test_baseline_anchor_is_preferred_and_normalized_to_utc():
    result = server._patient_visit_anchor({
        "baseline_date": "2026-04-05",
        "enrolled_date": "2026-04-01",
    })

    assert result == datetime(2026, 4, 5, tzinfo=timezone.utc)


def test_explicit_legacy_enrolment_date_remains_supported():
    result = server._patient_visit_anchor({"enrolled_date": date(2026, 4, 1)})

    assert result == datetime(2026, 4, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("patient", [None, {}, {"baseline_date": "not-a-date"}])
def test_missing_or_invalid_anchor_never_falls_back_to_current_date(patient):
    with pytest.raises(ValueError, match="anchor|baseline_date"):
        server._patient_visit_anchor(patient)
