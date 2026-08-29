"""Offline regression coverage for persistence-facing protocol timing helpers."""
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

# Importing server constructs the Motor client but these pure tests never
# connect. Existing test environments provide real values; defaults keep this
# module runnable in an isolated developer shell.
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "mtb_protocol_timing_test")

import server  # noqa: E402
from protocol_extraction import ExtractedSchedule, ExtractedVisit  # noqa: E402


ANCHOR = datetime(2026, 8, 12, tzinfo=timezone.utc)


def test_extraction_payload_preserves_undated_visit_and_numbering_metadata():
    schedule = ExtractedSchedule(
        schedule_kind="linear",
        anchor_study_day=1,
        includes_day_zero=False,
        visits=[ExtractedVisit(
            name="Unscheduled", source_day_label="Unscheduled",
            visit_type="Unscheduled")],
    )
    payload = server._schedule_extraction_payload(schedule)

    assert payload["anchor_study_day"] == 1
    assert payload["includes_day_zero"] is False
    assert payload["visits"][0]["day_offset"] is None
    assert payload["visits"][0]["source_day_label"] == "Unscheduled"
    assert payload["visits"][0]["review_status"] == "pending"


def test_extraction_payload_accepts_absolute_hour_zero_without_day_offset():
    schedule = ExtractedSchedule(
        schedule_kind="intra_day",
        visits=[ExtractedVisit(
            name="Hour 0", source_day_label="Hour 0", day_offset=None,
            hour_offset=0, hour_offset_basis="absolute")],
    )
    row = server._schedule_extraction_payload(schedule)["visits"][0]
    assert row["day_offset"] is None
    assert row["hour_offset"] == 0
    assert row["hour_offset_basis"] == "absolute"
    assert row["extraction_warning"] is False
    assert row["review_status"] == "ok"


def test_calculator_uses_exact_day_one_no_zero_formula():
    calculated = server._calculate_template_datetime(ANCHOR, {
        "source_day_label": "Day 8",
        "anchor_study_day": 1,
        "includes_day_zero": False,
        "day_offset": 8,  # conflicting AI arithmetic must not win
    })
    assert calculated == datetime(2026, 8, 19, tzinfo=timezone.utc)


def test_absolute_hour_26_with_no_day_offset_is_calculable():
    calculated = server._calculate_template_datetime(ANCHOR, {
        "day_offset": None,
        "hour_offset": 26,
        "hour_offset_basis": "absolute",
    })
    assert calculated == datetime(2026, 8, 13, 2, tzinfo=timezone.utc)


def test_legacy_hour_26_keeps_existing_additive_semantics():
    calculated = server._calculate_template_datetime(ANCHOR, {
        "day_offset": 1,
        "hour_offset": 26,
    })
    assert calculated == datetime(2026, 8, 14, 2, tzinfo=timezone.utc)


def test_undated_preview_is_manual_review_not_baseline():
    rows = server._build_schedule_preview([{
        "id": "u1", "visit_number": 1, "name": "Unscheduled",
        "day_offset": None, "source_day_label": "Unscheduled",
        "window_days": 3,
    }], ANCHOR)
    assert rows[0]["scheduled_date"] is None
    assert rows[0]["status"] == "manual_review"
    assert "no calculable day offset" in rows[0]["manual_review_reason"]


def test_unstated_window_stays_unknown_and_does_not_create_tolerance():
    template = {
        "id": "baseline", "visit_number": 1, "name": "Baseline",
        "day_offset": 0, "window_days": None,
    }
    start, end = server._schedule_window(template, ANCHOR)
    assert start == ANCHOR
    assert end == ANCHOR

    schedule = ExtractedSchedule.model_validate({
        "schedule_kind": "linear",
        "visits": [{"name": "Baseline", "day_offset": 0, "window_days": None}],
    })
    payload = server._schedule_extraction_payload(schedule)
    assert payload["visits"][0]["window_days"] is None
    assert server.VisitIn(
        trial_id="trial-1", visit_number=1, name="Baseline",
    ).window_days is None


def test_asymmetric_window_and_day_range_are_preserved():
    template = {
        "source_day_label": "Day 14-17",
        "anchor_study_day": 1,
        "includes_day_zero": False,
        "day_offset": 14,
        "day_end": 17,
        "window_days": 3,
        "window_before": 0,
        "window_after": 2,
    }
    normalized, warning = server._normalized_template_timing(template)
    scheduled = server._calculate_template_datetime(ANCHOR, normalized)
    scheduled_end = server._calculate_template_end_datetime(
        ANCHOR, normalized, scheduled)
    window_start, window_end = server._schedule_window(normalized, scheduled)

    assert normalized["day_offset"] == 13
    assert normalized["day_end"] == 16
    assert warning
    assert scheduled == datetime(2026, 8, 25, tzinfo=timezone.utc)
    assert scheduled_end == datetime(2026, 8, 28, tzinfo=timezone.utc)
    assert window_start == scheduled
    assert window_end == datetime(2026, 8, 27, tzinfo=timezone.utc)


def test_invalid_day_zero_no_zero_convention_never_schedules_old_guess():
    template, warning = server._normalized_template_timing({
        "source_day_label": "Day 0",
        "anchor_study_day": 1,
        "includes_day_zero": False,
        "day_offset": 0,
    })
    assert template["day_offset"] is None
    assert "Day 0 is invalid" in warning
    rows = server._build_schedule_preview([{"id": "bad", **template}], ANCHOR)
    assert rows[0]["status"] == "manual_review"
    assert rows[0]["scheduled_date"] is None


def test_untouched_planned_and_manual_review_instances_are_repointable():
    future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    assert server._instance_is_repointable({
        "status": "planned", "scheduled_date": future,
        "updated_by": None, "note": "",
    }, ANCHOR)
    assert server._instance_is_repointable({
        "status": "manual_review", "scheduled_date": None,
        "updated_by": None, "note": "",
    }, ANCHOR)
    assert not server._instance_is_repointable({
        "status": "completed", "scheduled_date": future,
        "updated_by": None, "note": "",
    }, ANCHOR)


def test_relative_dependent_recomputes_when_target_moves():
    rows = server._resolve_relative_template_offsets([
        {"id": "target", "name": "Dose", "day_offset": 10},
        {"id": "dependent", "name": "Follow-up", "day_offset": 17,
         "relative_to": "Dose", "relative_offset_days": 7},
    ])
    assert {row["id"]: row.get("day_offset") for row in rows} == {
        "target": 10, "dependent": 17,
    }

    moved = server._resolve_relative_template_offsets([
        {"id": "target", "name": "Dose", "day_offset": 20},
        {"id": "dependent", "name": "Follow-up", "day_offset": 17,
         "relative_to": "Dose", "relative_offset_days": 7},
    ])
    assert {row["id"]: row.get("day_offset") for row in moved}["dependent"] == 27


def test_relative_dependent_becomes_undated_when_target_is_renamed():
    rows = server._resolve_relative_template_offsets([
        {"id": "target", "name": "Renamed Dose", "day_offset": 10},
        {"id": "dependent", "name": "Follow-up", "day_offset": 17,
         "relative_to": "Dose", "relative_offset_days": 7},
    ])
    dependent = next(row for row in rows if row["id"] == "dependent")
    assert dependent["day_offset"] is None
    assert "missing, ambiguous, or undated" in dependent["_relative_resolution_warning"]


def test_create_visit_recomputes_relatives_and_returns_refetched_row(monkeypatch):
    events = []
    current = {
        "id": "new-visit", "trial_id": "trial-1", "visit_number": 2,
        "name": "Follow-up", "day_offset": 7,
        "relative_to": "Dose", "relative_offset_days": 7,
    }

    async def insert_one(_doc):
        events.append("insert")

    async def refetch(_query, _projection):
        events.append("refetch")
        return current

    fake_db = SimpleNamespace(
        trials=SimpleNamespace(find_one=AsyncMock(return_value={"id": "trial-1"})),
        visits=SimpleNamespace(
            insert_one=AsyncMock(side_effect=insert_one),
            find_one=AsyncMock(side_effect=refetch),
        ),
    )
    monkeypatch.setattr(server, "db", fake_db)
    monkeypatch.setattr(server.uuid, "uuid4", lambda: "new-visit")
    monkeypatch.setattr(server, "_can_access_trial", AsyncMock(return_value=True))

    async def materialize(_doc):
        events.append("materialize")
        return 1

    async def recompute(trial_id):
        assert trial_id == "trial-1"
        events.append("recompute")
        return 1

    monkeypatch.setattr(server, "_materialize_new_template_for_enrolled", materialize)
    monkeypatch.setattr(server, "_recompute_relative_templates", recompute)

    result = asyncio.run(server.create_visit(
        server.VisitIn(
            trial_id="trial-1", visit_number=2, name="Follow-up",
            day_offset=None, relative_to="Dose", relative_offset_days=7,
        ),
        user={"id": "sponsor-1", "role": "sponsor"},
    ))

    assert events == ["insert", "materialize", "recompute", "refetch"]
    assert result["day_offset"] == 7
    fake_db.visits.insert_one.assert_awaited_once()


def test_update_visit_returns_post_recompute_row(monkeypatch):
    template = {
        "id": "dependent", "trial_id": "trial-1", "visit_number": 2,
        "name": "Follow-up", "day_offset": 7,
        "relative_to": "Dose", "relative_offset_days": 7,
    }
    fresh = {**template, "comments": "updated"}
    recomputed = {**fresh, "day_offset": 12}
    visits = SimpleNamespace(
        find_one=AsyncMock(side_effect=[template, fresh, recomputed]),
        update_one=AsyncMock(),
    )
    fake_db = SimpleNamespace(
        visits=visits,
        trials=SimpleNamespace(find_one=AsyncMock(return_value={"id": "trial-1"})),
    )
    monkeypatch.setattr(server, "db", fake_db)
    monkeypatch.setattr(server, "_require_schedule_owner", AsyncMock())
    monkeypatch.setattr(server, "_rematerialize_template_change", AsyncMock(return_value=1))
    monkeypatch.setattr(server, "_recompute_relative_templates", AsyncMock(return_value=1))
    monkeypatch.setattr(server, "write_audit", AsyncMock())

    result = asyncio.run(server.update_visit(
        "dependent", server.VisitUpdate(comments="updated"),
        user={"id": "sponsor-1", "role": "sponsor"},
    ))

    assert result["day_offset"] == 12
    assert visits.find_one.await_count == 3
    server._recompute_relative_templates.assert_awaited_once_with("trial-1")


def test_delete_visit_recomputes_dependents_after_target_removal(monkeypatch):
    events = []
    template = {
        "id": "dose", "trial_id": "trial-1", "visit_number": 1,
        "name": "Dose", "day_offset": 0,
    }

    async def delete_one(_query):
        events.append("delete")

    async def recompute(trial_id):
        assert trial_id == "trial-1"
        events.append("recompute")
        return 2

    fake_db = SimpleNamespace(
        visits=SimpleNamespace(
            find_one=AsyncMock(return_value=template),
            delete_one=AsyncMock(side_effect=delete_one),
        ),
        trials=SimpleNamespace(find_one=AsyncMock(return_value={"id": "trial-1"})),
    )
    monkeypatch.setattr(server, "db", fake_db)
    monkeypatch.setattr(server, "_require_schedule_owner", AsyncMock())
    monkeypatch.setattr(server, "_rematerialize_template_delete", AsyncMock(return_value=1))
    monkeypatch.setattr(server, "_recompute_relative_templates", recompute)
    monkeypatch.setattr(server, "write_audit", AsyncMock())

    result = asyncio.run(server.delete_visit(
        "dose", user={"id": "sponsor-1", "role": "sponsor"}))

    assert events == ["delete", "recompute"]
    assert result == {
        "deleted": True,
        "instances_removed": 1,
        "relative_templates_recalculated": 2,
    }
