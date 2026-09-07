"""Build a patient deviation report from the current confirmed plan.

The report is derived, not stored. Windows come from the schedule version the
patient is pinned to, so a v1 patient keeps being assessed against v1 windows even
after v2 is approved (requirement doc 9 section 27).
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as db
from app.domain.schedule.deviation import (
    ConfinementDeviation, DeviationReport, assess_activity, assess_confinement,
    assess_visit,
)
from app.services.schedule_service import PatientScheduleService


class DeviationService:
    def __init__(self, session: Session):
        self.session = session
        self.patient_schedules = PatientScheduleService(session)

    def report(
        self, patient_id: UUID, *, organization_id: UUID, today: date | None = None,
    ) -> DeviationReport:
        today = today or date.today()
        patient = self.session.scalar(select(db.Patient).where(
            db.Patient.id == patient_id, db.Patient.organization_id == organization_id,
        ))
        if patient is None:
            raise KeyError("patient not found")
        if patient.current_schedule_version_id is None:
            raise ValueError("patient is not pinned to an approved schedule")

        patient_schedule = self.session.scalar(select(db.PatientSchedule).where(
            db.PatientSchedule.patient_id == patient_id,
            db.PatientSchedule.status == "ACTIVE",
        ).order_by(db.PatientSchedule.generated_at.desc()))
        report = DeviationReport(
            patient_id=str(patient_id),
            schedule_version_id=str(patient.current_schedule_version_id),
            assessed_on=today,
        )
        if patient_schedule is None or patient_schedule.current_evaluation_id is None:
            return report

        rows = self.session.execute(
            select(db.PatientEvent, db.Event.code, db.Event.display_name)
            .join(db.Event, db.Event.id == db.PatientEvent.event_definition_id)
            .where(db.PatientEvent.schedule_evaluation_id == patient_schedule.current_evaluation_id)
            .order_by(db.PatientEvent.nominal_start_date, db.PatientEvent.occurrence_index)
        ).all()
        actual_by_logical = self._actual_dates([row.PatientEvent for row in rows])

        for row in rows:
            event = row.PatientEvent
            report.visits.append(assess_visit(
                logical_key=event.logical_key, event_code=row.code,
                display_name=row.display_name, status=event.status,
                planned_date=event.nominal_start_date,
                earliest_date=event.earliest_date, latest_date=event.latest_date,
                actual_date=actual_by_logical.get(event.logical_occurrence_id),
                today=today,
            ))
            for activity in self.session.scalars(select(db.PatientActivity).where(
                db.PatientActivity.patient_event_id == event.id,
            ).order_by(db.PatientActivity.sequence_number)):
                report.activities.append(assess_activity(
                    logical_key=activity.logical_key,
                    activity_code=activity.activity_code,
                    display_name=activity.activity_code or "Activity",
                    status=activity.status, planned_time=activity.planned_time,
                    earliest_time=activity.earliest_time,
                    latest_time=activity.latest_time, actual_time=activity.actual_time,
                ))

        report.confinements = self._confinements(patient, today)
        return report

    def _actual_dates(self, events: list[db.PatientEvent]) -> dict[UUID, date]:
        if not events:
            return {}
        actuals: dict[UUID, date] = {}
        rows = self.session.execute(
            select(db.PatientEvent.logical_occurrence_id, db.PatientEventOccurrence.actual_date)
            .join(db.PatientEventOccurrence,
                  db.PatientEventOccurrence.patient_event_id == db.PatientEvent.id)
            .where(
                db.PatientEvent.logical_occurrence_id.in_(
                    [item.logical_occurrence_id for item in events]),
                db.PatientEventOccurrence.actual_date.is_not(None),
            )
            .order_by(db.PatientEventOccurrence.recorded_at)
        )
        for logical_id, actual_date in rows:
            actuals[logical_id] = actual_date
        return actuals

    def _confinements(self, patient: db.Patient, today: date) -> list[ConfinementDeviation]:
        assert patient.current_schedule_version_id is not None
        try:
            evaluation = self.patient_schedules.evaluate_result(
                patient, patient.current_schedule_version_id,
                horizon=today + timedelta(days=365),
            )
        except ValueError:
            # An unapproved or failing schedule has nothing meaningful to compare.
            return []
        return [
            assess_confinement(
                episode_code=episode.episode_code, display_name=episode.display_name,
                planned_admission=_as_date(episode.planned_admission),
                actual_admission=_as_date(episode.actual_admission),
                planned_discharge=_as_date(episode.planned_discharge),
                actual_discharge=_as_date(episode.actual_discharge),
                extended=bool(episode.extended_by),
            )
            for episode in evaluation.confinements
            if episode.status.value not in {"NOT_APPLICABLE", "WAITING_FOR_ANCHOR"}
        ]


def _as_date(value: object) -> date | None:
    from datetime import datetime

    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None
