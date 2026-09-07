"""Turn a patient's schedule PROJECTION into actual in-app notifications (doc s15).

The engine could already calculate reminders (``app.services.schedule_projection.
project``). Nothing ever consumed them - the projection endpoint existed only
for a screen to poll on request. This module is the missing consumer: given
one patient's projection and the reminder keys already delivered to them, it
decides exactly what to write and what to withdraw, and nothing else.

Kept pure and synchronous on purpose. The only side-effecting piece (writing to
Mongo, resolving a patient's login user_id, running the SQL evaluation) lives
in the caller (``server.py``'s worker loop), so the actual decision logic here
- what counts as new, what counts as stale, how a reminder is worded - is
testable with no database and no event loop.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as db
from app.services.schedule_projection import (
    ProjectedConfinement, ProjectedEvent, ProjectionResult, ReminderCandidate,
    project, reconcile,
)
from app.services.schedule_service import PatientScheduleService


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def compute_patient_projection(
    session: Session, patient: db.Patient, *, today: date | None = None,
) -> ProjectionResult:
    """The same computation ``GET /patients/{id}/schedule/projection`` exposes,
    factored out so the reminder worker (below) and that read-only endpoint
    are guaranteed to agree - one calculation, two callers, never two rules
    that could quietly drift apart.
    """
    patient_schedule = session.scalar(select(db.PatientSchedule).where(
        db.PatientSchedule.patient_id == patient.id,
        db.PatientSchedule.status == "ACTIVE",
    ).order_by(db.PatientSchedule.generated_at.desc()))
    if patient_schedule is None or patient_schedule.current_evaluation_id is None:
        return ProjectionResult()

    rows = session.execute(
        select(db.PatientEvent, db.Event.code, db.Event.event_type, db.Event.display_name)
        .join(db.Event, db.Event.id == db.PatientEvent.event_definition_id)
        .where(db.PatientEvent.schedule_evaluation_id == patient_schedule.current_evaluation_id)
    ).all()
    projected = [
        ProjectedEvent(
            logical_key=row.PatientEvent.logical_key, event_code=row.code,
            event_type=row.event_type, display_name=row.display_name,
            status=row.PatientEvent.status,
            nominal_date=row.PatientEvent.nominal_start_date,
            earliest_date=row.PatientEvent.earliest_date,
            latest_date=row.PatientEvent.latest_date,
            is_unscheduled=str(row.event_type).upper() == "UNSCHEDULED",
        )
        for row in rows
    ]

    service = PatientScheduleService(session)
    confinements: list[ProjectedConfinement] = []
    if patient.current_schedule_version_id is not None:
        result = service.evaluate_result(
            patient, patient.current_schedule_version_id,
            horizon=(today or date.today()) + timedelta(days=365),
        )
        for episode in result.confinements:
            definition = next(
                (item for item in service.repository.get(
                    patient.current_schedule_version_id).confinement_episodes
                 if item.code == episode.episode_code), None)
            members = ([
                definition.admission_event_code, *definition.dose_event_codes,
                definition.discharge_event_code,
            ] if definition else [])
            confinements.append(ProjectedConfinement(
                episode_code=episode.episode_code, display_name=episode.display_name,
                status=episode.status.value,
                start_date=_as_date(episode.actual_admission or episode.planned_admission),
                end_date=_as_date(episode.actual_discharge or episode.planned_discharge),
                member_event_codes=members,
            ))
    return project(projected, today=today or date.today(), confinements=confinements)

#: doc s15: never send a reminder for these. ``project()`` already enforces
#: this by never emitting a ReminderCandidate for them in the first place
#: (inactive conditionals, unresolved dates, cancelled/completed events all
#: fall into ``suppressed`` instead) - this module trusts that contract rather
#: than re-deriving it, so there is exactly one place that decides suppression.


@dataclass(frozen=True)
class ReminderSync:
    """What one worker pass must do for one patient."""

    #: New notification documents to insert (covers both genuinely new
    #: reminders and a moved date's replacement for an old one).
    to_create: list[dict]
    #: Reminder keys whose notification must stop being presented as active -
    #: a visit was cancelled, resolved differently, or moved (superseding its
    #: own old key). Never a deletion of history; the caller marks these
    #: withdrawn, it does not need to un-notify a phone push already delivered.
    to_withdraw_keys: list[str]


def sync_patient_reminders(
    *,
    user_id: str,
    patient_id: str,
    trial_id: str,
    projection: ProjectionResult,
    existing_keys: list[str],
    now: datetime,
) -> ReminderSync:
    """Diff a freshly computed projection against what was already delivered.

    ``existing_keys`` are every reminder key this patient currently has an
    active (non-withdrawn) notification for. Doc s15's requirement - "when a
    planned date changes ... the relevant reminders must be recalculated" -
    is exactly what ``reconcile()`` already computes; this function's only
    job is turning that diff into notification documents.
    """
    plan = reconcile(existing_keys, projection.reminders)
    by_key = {item.key: item for item in projection.reminders}

    to_create = [
        _notification_document(
            user_id=user_id, patient_id=patient_id, trial_id=trial_id,
            candidate=by_key[key], now=now,
        )
        for key in (*plan.added, *plan.updated)
    ]
    return ReminderSync(to_create=to_create, to_withdraw_keys=list(plan.cancelled))


_POLICY_PREFIX = {
    "OVERDUE": "Overdue: ",
    "DUE_WINDOW": "",
    "UPCOMING": "",
}


def _notification_document(
    *, user_id: str, patient_id: str, trial_id: str,
    candidate: ReminderCandidate, now: datetime,
) -> dict:
    prefix = _POLICY_PREFIX.get(candidate.policy_state, "")
    return {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "title": f"{prefix}Upcoming visit" if candidate.requires_attendance
                  else f"{prefix}Study contact",
        "body": candidate.message,
        "type": "visit_reminder",
        "reminder_key": candidate.key,
        "logical_key": candidate.logical_key,
        "event_code": candidate.event_code,
        "patient_id": patient_id,
        "trial_id": trial_id,
        "policy_state": candidate.policy_state,
        "nominal_date": _as_datetime(candidate.nominal_date),
        "requires_attendance": candidate.requires_attendance,
        "read": False,
        "withdrawn": False,
        "created_at": now,
    }


def _as_datetime(value: date) -> datetime:
    return datetime(value.year, value.month, value.day)
