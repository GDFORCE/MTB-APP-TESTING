"""Reading visit dates from the engine without moving anything else.

This is what makes the cutover survivable. A visit_instance carries far more than
a date: comments, tasks, workflow state, who completed it, the site's own edits.
The engine has no equivalent for any of that and should not acquire one.

So the cutover overlays DATES ONLY. Every operational document keeps its
identity and every field it had; ``scheduled_date``, ``window_start`` and
``window_end`` come from the engine instead. Three consequences follow:

  * nothing is created and nothing is deleted. A read cannot lose a site's
    comment thread, whatever the engine thinks;
  * reverting is instant and total - flip the trial back to LEGACY and the
    original dates are simply used again, because they were never overwritten;
  * a document the engine has no date for keeps the date it had, and says so.
    Blanking it would remove a visit from a site's list on the strength of a
    disagreement the parity gate was supposed to have caught.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any

from app.domain.schedule.parity import normalize_key

#: Only these move. Everything else on the document is operational state the
#: engine has no opinion about.
OVERLAID_FIELDS = ("scheduled_date", "window_start", "window_end")


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def apply_engine_dates(
    instances: list[dict[str, Any]],
    engine_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Overlay the engine's dates onto operational visit documents.

    Matching is by normalized visit name, the same rule the parity gate used to
    prove the two systems agree - so anything that matches here matched there.

    An instance the engine does not produce is returned UNCHANGED with
    ``uctsm_source_of_truth`` false, so it is visibly still on the operational
    date rather than quietly appearing to be engine-backed.
    """
    by_key: dict[str, list[dict[str, Any]]] = {}
    for row in engine_rows:
        key = normalize_key(str(row.get("name") or row.get("event_code") or ""))
        if key:
            by_key.setdefault(key, []).append(row)

    output: list[dict[str, Any]] = []
    for instance in instances:
        key = normalize_key(str(instance.get("name") or ""))
        candidates = by_key.get(key) or []
        # More than one match means the name is ambiguous. The parity gate
        # refuses to pass in that case, so reaching here means something changed
        # since; leaving the operational dates alone is the safe reading.
        if len(candidates) != 1:
            output.append({
                **instance,
                "uctsm_source_of_truth": False,
                "uctsm_read_note": (
                    "no single matching visit in the canonical schedule; showing "
                    "the operational date"
                ),
            })
            continue

        source = candidates[0]
        overlaid = dict(instance)
        for field in OVERLAID_FIELDS:
            value = _as_datetime(source.get(field))
            if value is not None:
                overlaid[field] = value
        overlaid["uctsm_source_of_truth"] = True
        overlaid["uctsm_event_code"] = source.get("event_code")
        overlaid["uctsm_logical_key"] = source.get("uctsm_logical_key")
        overlaid["uctsm_status"] = source.get("status")
        # An engine visit with no date is a real answer: the protocol anchor is
        # missing. The operational date stays visible so nothing disappears from
        # a site's list, but it is flagged rather than presented as confirmed.
        if _as_datetime(source.get("scheduled_date")) is None:
            overlaid["uctsm_source_of_truth"] = False
            overlaid["uctsm_read_note"] = (
                "the canonical schedule cannot date this visit yet; showing the "
                "operational date"
            )
        output.append(overlaid)
    return output


def unmatched_engine_visits(
    instances: list[dict[str, Any]],
    engine_rows: list[dict[str, Any]],
) -> list[str]:
    """Engine visits with no operational document, for the caller to surface.

    Deliberately returned rather than appended to the read: creating a visit
    document as a side effect of reading would give it no workflow history and no
    audit trail, and it would appear and disappear as the engine's view changed.
    """
    present = {normalize_key(str(item.get("name") or "")) for item in instances}
    missing: list[str] = []
    for row in engine_rows:
        name = str(row.get("name") or "")
        if normalize_key(name) not in present:
            missing.append(name)
    return missing
