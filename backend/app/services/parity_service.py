"""Running the parity gate, and the cutover it gates.

The caller supplies the operational schedule, because the canonical services
speak to Postgres and the operational store is Mongo. That keeps the boundary
explicit: this module never reaches across it, and a caller cannot accidentally
compare a patient against someone else's visits because the linkage is checked
here rather than assumed.

The cutover rule is the whole point:

    A trial's visit reads move to the engine ONLY when a parity run for that
    trial, against the schedule version the patients are actually pinned to,
    matched. No override, no force flag.

Moving back to LEGACY needs no gate at all. Reversing a cutover must never be
harder than making one - if the engine turns out to disagree with reality, the
person who notices should be able to undo it immediately.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as db
from app.domain.schedule.parity import (
    ParityReport, ParityVerdict, compare_schedules, to_comparable,
)
from app.services.operational_projection import bridge_visit_documents
from app.domain.schedule.models import utc_now
from app.services.schedule_service import PatientScheduleService

READ_MODE_LEGACY = "LEGACY"
READ_MODE_ENGINE = "ENGINE"


class ParityService:
    def __init__(self, session: Session):
        self.session = session

    # --- running the comparison ----------------------------------------------------

    def compare_patient(
        self,
        patient_id: UUID,
        *,
        organization_id: UUID,
        legacy_visits: list[dict[str, object]],
        horizon: date | None = None,
        actor_id: UUID | None = None,
        record: bool = True,
    ) -> tuple[ParityReport, db.ParityRun | None]:
        """Compare one linked patient's schedule across both systems."""
        patient = self.session.scalar(select(db.Patient).where(
            db.Patient.id == patient_id,
            db.Patient.organization_id == organization_id,
        ))
        if patient is None:
            raise KeyError("patient not found")
        if patient.current_schedule_version_id is None:
            report = ParityReport(
                verdict=ParityVerdict.NOT_COMPARABLE,
                note="the patient is not pinned to an approved schedule version",
            )
            return report, self._record(patient, report, actor_id) if record else None

        service = PatientScheduleService(self.session)
        _evaluation, events = service.evaluate(
            patient.id, organization_id=organization_id,
            horizon=horizon or (date.today() + timedelta(days=730)),
        )
        engine_rows = self._engine_rows(patient, events)

        report = compare_schedules(
            to_comparable(legacy_visits, key_fields=("name",)),
            to_comparable(engine_rows, key_fields=("name",)),
        )
        run = self._record(patient, report, actor_id) if record else None
        return report, run

    def _engine_rows(
        self, patient: db.Patient, events: list[db.PatientEvent],
    ) -> list[dict[str, object]]:
        """The engine's schedule in the operational shape, for comparison only."""
        definitions = {
            row.id: row for row in self.session.scalars(select(db.Event).where(
                db.Event.schedule_version_id == patient.current_schedule_version_id,
            ))
        }
        from app.services.operational_bridge import BridgeEvent

        bridge_events = []
        for item in events:
            definition = definitions.get(item.event_definition_id)
            if definition is None:
                continue
            bridge_events.append(BridgeEvent(
                patient_event_id=item.id,
                logical_occurrence_id=item.logical_occurrence_id,
                logical_key=item.logical_key,
                event_definition_id=item.event_definition_id,
                event_code=definition.code,
                name=definition.display_name,
                event_type=definition.event_type,
                status=item.status,
                nominal_date=item.nominal_start_date,
                earliest_date=item.earliest_date,
                latest_date=item.latest_date,
                activities=(),
            ))
        return bridge_visit_documents(
            patient_id=str(patient.id), trial_id=str(patient.trial_id),
            schedule_version_id=str(patient.current_schedule_version_id),
            evaluation_id="parity-check", events=bridge_events,
            generated_at=utc_now(),
        )

    def _record(
        self, patient: db.Patient, report: ParityReport, actor_id: UUID | None,
    ) -> db.ParityRun:
        run = db.ParityRun(
            organization_id=patient.organization_id, trial_id=patient.trial_id,
            patient_id=patient.id,
            schedule_version_id=patient.current_schedule_version_id,
            verdict=report.verdict.value, compared=report.compared,
            matched=report.matched,
            differences=[item.model_dump(mode="json") for item in report.differences],
            note=report.note, ran_by=actor_id,
        )
        self.session.add(run)
        self.session.flush()
        return run

    # --- the gate ------------------------------------------------------------------

    def readiness(self, trial_id: UUID, *, organization_id: UUID) -> dict[str, object]:
        """Whether this trial's reads may move to the engine, and why not if not.

        Every patient pinned to a schedule version must have a MATCHING run
        against THAT version. A trial where nine patients matched and the tenth
        was never checked is not ready: the tenth patient is the one who ends up
        on the wrong day.
        """
        trial = self.session.scalar(select(db.Trial).where(
            db.Trial.id == trial_id, db.Trial.organization_id == organization_id,
        ))
        if trial is None:
            raise KeyError("trial not found")

        patients = list(self.session.scalars(select(db.Patient).where(
            db.Patient.trial_id == trial_id,
            db.Patient.current_schedule_version_id.is_not(None),
        )))
        runs = list(self.session.scalars(select(db.ParityRun).where(
            db.ParityRun.trial_id == trial_id,
        ).order_by(db.ParityRun.ran_at)))
        # Latest run per (patient, schedule version). A run against a superseded
        # version proves nothing about the version the patient is on now.
        latest: dict[tuple[UUID, UUID], db.ParityRun] = {}
        for run in runs:
            if run.patient_id is None or run.schedule_version_id is None:
                continue
            latest[(run.patient_id, run.schedule_version_id)] = run

        unchecked: list[str] = []
        failing: list[str] = []
        for patient in patients:
            key = (patient.id, patient.current_schedule_version_id)
            run = latest.get(key)
            if run is None:
                unchecked.append(patient.patient_code)
            elif run.verdict != ParityVerdict.MATCH.value:
                failing.append(patient.patient_code)

        ready = bool(patients) and not unchecked and not failing
        reasons: list[str] = []
        if not patients:
            reasons.append(
                "no patient in this trial is pinned to an approved schedule version, "
                "so there is nothing to compare"
            )
        if unchecked:
            reasons.append(
                f"{len(unchecked)} patient(s) have not been compared against the "
                f"version they are pinned to: {', '.join(sorted(unchecked)[:10])}"
            )
        if failing:
            reasons.append(
                f"{len(failing)} patient(s) have differences that need a decision: "
                f"{', '.join(sorted(failing)[:10])}"
            )
        return {
            "trial_id": str(trial_id),
            "read_mode": trial.schedule_read_mode,
            "ready": ready,
            "patients": len(patients),
            "checked": len(patients) - len(unchecked),
            "unchecked": sorted(unchecked),
            "with_differences": sorted(failing),
            "reasons": reasons,
        }

    def set_read_mode(
        self,
        trial_id: UUID,
        *,
        organization_id: UUID,
        mode: str,
        actor_id: UUID,
        reason: str,
    ) -> dict[str, object]:
        """Move a trial's visit reads between the operational store and the engine."""
        if mode not in {READ_MODE_LEGACY, READ_MODE_ENGINE}:
            raise ValueError(f"read mode must be {READ_MODE_LEGACY} or {READ_MODE_ENGINE}")
        if not reason or not reason.strip():
            raise ValueError("changing where visit dates are read from needs a reason")
        trial = self.session.scalar(select(db.Trial).where(
            db.Trial.id == trial_id, db.Trial.organization_id == organization_id,
        ).with_for_update())
        if trial is None:
            raise KeyError("trial not found")

        if mode == READ_MODE_ENGINE:
            status = self.readiness(trial_id, organization_id=organization_id)
            if not status["ready"]:
                raise ValueError(
                    "parity has not been proven for this trial: "
                    + "; ".join(status["reasons"])
                )

        before = trial.schedule_read_mode
        trial.schedule_read_mode = mode
        self.session.add(db.AuditEvent(
            organization_id=organization_id, actor_id=actor_id,
            action="OPERATIONAL_READ_MODE_CHANGED",
            entity_type="TRIAL", entity_id=trial.id,
            before={"schedule_read_mode": before},
            after={"schedule_read_mode": mode, "reason": reason.strip()},
        ))
        return {"trial_id": str(trial_id), "read_mode": mode, "previous": before}


def read_mode_for_trial(session: Session, external_trial_id: str) -> str:
    """Where an operational caller should read this trial's visit dates from.

    Answers LEGACY for anything not linked or not cut over, so a caller that
    forgets to handle a new mode keeps the behaviour it has today.
    """
    if not external_trial_id:
        return READ_MODE_LEGACY
    trial = session.scalar(select(db.Trial).where(
        db.Trial.external_trial_id == external_trial_id,
    ))
    if trial is None:
        return READ_MODE_LEGACY
    return trial.schedule_read_mode or READ_MODE_LEGACY
