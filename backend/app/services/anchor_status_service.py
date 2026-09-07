"""Per-patient anchor status for the Patient Profile (requirement doc 1 s22).

Reads the stored anchor records and hands them to the pure resolver in
app/domain/schedule/anchor_status.py. Nothing here computes a date: the point of
this view is to say what is KNOWN about each anchor, so an empty schedule row can
be explained ("Awaiting Surgery Date") instead of appearing broken.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as db
from app.domain.schedule.anchor_status import (
    AnchorRecord, AnchorStatusView, resolve_anchor_status,
)
from app.domain.schedule.models import UniversalSchedule
from app.domain.schedule.timing import AnchorReference


class AnchorStatusService:
    def __init__(self, session: Session):
        self.session = session

    def statuses(
        self, patient_id: UUID, *, organization_id: UUID,
    ) -> list[AnchorStatusView]:
        from app.services.schedule_service import PatientScheduleService

        patient = self.session.scalar(select(db.Patient).where(
            db.Patient.id == patient_id,
            db.Patient.organization_id == organization_id,
        ))
        if patient is None:
            raise KeyError("patient not found")
        if patient.current_schedule_version_id is None:
            return []

        service = PatientScheduleService(self.session)
        schedule = service.repository.get(patient.current_schedule_version_id)
        required = self._required_anchor_codes(schedule, patient, service)
        records = self._records_by_anchor(patient_id, schedule)
        actual_event_codes = self._event_codes_with_actuals(patient_id)

        views: list[AnchorStatusView] = []
        for anchor in schedule.anchors:
            views.append(resolve_anchor_status(
                anchor_code=anchor.code,
                display_name=anchor.display_name,
                records=records.get(anchor.code, []),
                applicable=anchor.code in required,
                source_event_code=anchor.source_event_code,
                source_event_has_actual=(
                    anchor.source_event_code in actual_event_codes
                    if anchor.source_event_code else False
                ),
            ))
        return views

    def _required_anchor_codes(
        self, schedule: UniversalSchedule, patient: db.Patient, service,
    ) -> set[str]:
        """Anchors some part of THIS patient's schedule actually depends on.

        An anchor only referenced by visits that do not apply to this patient
        (another arm, another cohort) is NOT_REQUIRED. Asking a site to supply a
        surgery date for a patient who is not having surgery is the noise doc 1
        section 22 wants removed.
        """
        applicable_codes = {
            item.code for item in schedule.events
            if not item.applicability
        }
        try:
            context = service._context(patient, schedule.schedule_version_id)
            from app.domain.schedule.evaluator import _evaluate_applicability
            from app.domain.schedule.condition import TruthValue

            for event in schedule.events:
                if not event.applicability:
                    continue
                if _evaluate_applicability(event.applicability, context) != TruthValue.FALSE:
                    applicable_codes.add(event.code)
        except Exception:
            # A schedule we cannot contextualise must not hide anchors: showing an
            # anchor that turns out unnecessary is recoverable, hiding one that is
            # needed leaves a visit permanently undated with no explanation.
            applicable_codes = {item.code for item in schedule.events}

        required: set[str] = set()
        for event in schedule.events:
            if event.code not in applicable_codes:
                continue
            required.update(_anchor_codes_in(event.timing))
            if event.recurrence is not None:
                reference = event.recurrence.start_reference
                if isinstance(reference, AnchorReference):
                    required.add(reference.code)
        return required

    def _records_by_anchor(
        self, patient_id: UUID, schedule: UniversalSchedule,
    ) -> dict[str, list[AnchorRecord]]:
        """Oldest first. The last live record is current; earlier ones superseded."""
        code_by_id = {anchor.id: anchor.code for anchor in schedule.anchors}
        rows = list(self.session.scalars(select(db.PatientAnchor).where(
            db.PatientAnchor.patient_id == patient_id,
        ).order_by(db.PatientAnchor.recorded_at)))
        grouped: dict[str, list[db.PatientAnchor]] = {}
        for row in rows:
            code = code_by_id.get(row.anchor_definition_id)
            if code is not None:
                grouped.setdefault(code, []).append(row)

        result: dict[str, list[AnchorRecord]] = {}
        for code, items in grouped.items():
            usable = [item for item in items if item.status != "PENDING_CONFIRMATION"]
            last_usable = usable[-1] if usable else None
            result[code] = [
                AnchorRecord(
                    value=item.value_datetime or item.value_date,
                    record_status=item.status,
                    source_type=item.source_type,
                    recorded_at=item.recorded_at,
                    # An unconfirmed candidate is not a value the schedule uses,
                    # so it never counts as the current one (doc 1 s10, s16).
                    superseded=(item is not last_usable),
                )
                for item in items
            ]
        return result

    def _event_codes_with_actuals(self, patient_id: UUID) -> set[str]:
        rows = self.session.execute(
            select(db.Event.code)
            .join(db.PatientEvent, db.PatientEvent.event_definition_id == db.Event.id)
            .join(
                db.PatientEventOccurrence,
                db.PatientEventOccurrence.patient_event_id == db.PatientEvent.id,
            )
            .join(
                db.PatientSchedule,
                db.PatientSchedule.id == db.PatientEvent.patient_schedule_id,
            )
            .where(
                db.PatientSchedule.patient_id == patient_id,
                db.PatientEventOccurrence.actual_date.is_not(None),
            )
        )
        return {code for (code,) in rows}


def _anchor_codes_in(value: object) -> set[str]:
    """Every anchor code a timing expression reaches, at any nesting depth."""
    found: set[str] = set()
    if isinstance(value, AnchorReference):
        found.add(value.code)
        return found
    if hasattr(value, "model_dump"):
        for field in getattr(type(value), "model_fields", {}):
            found |= _anchor_codes_in(getattr(value, field, None))
        return found
    if isinstance(value, (list, tuple)):
        for item in value:
            found |= _anchor_codes_in(item)
    return found
