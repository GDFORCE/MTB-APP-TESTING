"""PI/CRC dashboard: only what currently needs a human decision.

Requirement sources: doc 2 section 13, doc 3 section 28, doc 5 section 25,
doc 10 section 24.

The governing constraint is what the dashboard must NOT do. Doc 2 section 13:
"the dashboard should NOT display every possible condition for every patient at all
times. That would create excessive clutter." Doc 3 section 28: "It should not list
hundreds of future cycles." So this service returns ACTIONS, not a schedule:
something a PI or CRC has to confirm, resolve, or chase today.

Wording is clinical, per doc 2 section 46 and doc 1 section 35 - no engine terms.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as db
from app.domain.schedule.deviation import DeviationType
from app.services.deviation_service import DeviationService

# How far ahead an upcoming visit counts as "approaching" rather than future noise.
UPCOMING_WINDOW_DAYS = 7


class ActionCategory(StrEnum):
    ANCHOR_CONFIRMATION = "ANCHOR_CONFIRMATION"
    CONDITION_CONFIRMATION = "CONDITION_CONFIRMATION"
    CONDITION_ACTIVE = "CONDITION_ACTIVE"
    IMPACT_CONFIRMATION = "IMPACT_CONFIRMATION"
    VISIT_DUE = "VISIT_DUE"
    VISIT_OVERDUE = "VISIT_OVERDUE"
    DEVIATION = "DEVIATION"
    ACTIVITY_INCOMPLETE = "ACTIVITY_INCOMPLETE"


# Lower sorts first. Anything blocking the schedule outranks anything informational.
PRIORITY = {
    ActionCategory.IMPACT_CONFIRMATION: 0,
    ActionCategory.ANCHOR_CONFIRMATION: 1,
    ActionCategory.CONDITION_CONFIRMATION: 2,
    ActionCategory.VISIT_OVERDUE: 3,
    ActionCategory.DEVIATION: 4,
    ActionCategory.VISIT_DUE: 5,
    ActionCategory.ACTIVITY_INCOMPLETE: 6,
    ActionCategory.CONDITION_ACTIVE: 7,
}


class DashboardAction(BaseModel):
    patient_id: str
    patient_code: str
    category: ActionCategory
    message: str
    due_date: date | None = None
    #: What the UI should open when the item is selected.
    target_type: str
    target_id: str | None = None
    priority: int = 5


class DashboardResult(BaseModel):
    trial_id: str | None = None
    generated_on: date
    actions: list[DashboardAction] = Field(default_factory=list)

    def by_category(self, category: ActionCategory) -> list[DashboardAction]:
        return [item for item in self.actions if item.category == category]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DashboardService:
    def __init__(self, session: Session):
        self.session = session
        self.deviations = DeviationService(session)

    def actions(
        self,
        *,
        organization_id: UUID,
        trial_id: UUID | None = None,
        today: date | None = None,
    ) -> DashboardResult:
        today = today or date.today()
        result = DashboardResult(
            trial_id=str(trial_id) if trial_id else None, generated_on=today)
        query = select(db.Patient).where(db.Patient.organization_id == organization_id)
        if trial_id is not None:
            query = query.where(db.Patient.trial_id == trial_id)
        patients = list(self.session.scalars(query))
        if not patients:
            return result

        for patient in patients:
            result.actions.extend(self._for_patient(patient, organization_id, today))
        result.actions.sort(key=lambda item: (item.priority, item.due_date or today))
        return result

    def _for_patient(
        self, patient: db.Patient, organization_id: UUID, today: date,
    ) -> list[DashboardAction]:
        actions: list[DashboardAction] = []
        code = patient.patient_code

        def add(category: ActionCategory, message: str, **extra) -> None:
            actions.append(DashboardAction(
                patient_id=str(patient.id), patient_code=code, category=category,
                message=message, priority=PRIORITY[category], **extra,
            ))

        # A recorded anchor or condition that is waiting on a human is the most
        # blocking thing on the board: dependent visits cannot be dated without it.
        for anchor_row, anchor_code in self.session.execute(
            select(db.PatientAnchor, db.Anchor.code)
            .join(db.Anchor, db.Anchor.id == db.PatientAnchor.anchor_definition_id)
            .where(
                db.PatientAnchor.patient_id == patient.id,
                db.PatientAnchor.status == "PENDING_CONFIRMATION",
            )
        ).all():
            add(
                ActionCategory.ANCHOR_CONFIRMATION,
                f"{_humanize(anchor_code)} recorded - confirm to update dependent visits",
                target_type="PATIENT_ANCHOR", target_id=str(anchor_row.id),
                due_date=anchor_row.value_date,
            )

        for row in self.session.scalars(select(db.PatientCondition).where(
            db.PatientCondition.patient_id == patient.id,
            db.PatientCondition.superseded_by_id.is_(None),
        )):
            if row.state == "PENDING_CONFIRMATION":
                add(
                    ActionCategory.CONDITION_CONFIRMATION,
                    f"{_humanize(row.condition_code)} awaiting confirmation",
                    target_type="PATIENT_CONDITION", target_id=str(row.id),
                    due_date=row.occurrence_date,
                )
            elif row.state == "ACTIVE":
                add(
                    ActionCategory.CONDITION_ACTIVE,
                    f"{_humanize(row.condition_code)} active - resolve when the patient recovers",
                    target_type="PATIENT_CONDITION", target_id=str(row.id),
                    due_date=row.occurrence_date,
                )

        for proposal in self.session.scalars(select(db.ScheduleImpactProposal).where(
            db.ScheduleImpactProposal.patient_id == patient.id,
            db.ScheduleImpactProposal.status == "PENDING",
        )):
            expires = proposal.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires <= _utc_now():
                continue
            add(
                ActionCategory.IMPACT_CONFIRMATION,
                "Schedule change previewed - confirm or cancel before it expires",
                target_type="SCHEDULE_IMPACT_PROPOSAL", target_id=str(proposal.id),
                due_date=expires.date(),
            )

        self._schedule_actions(patient, organization_id, today, add=add)
        return actions

    def _schedule_actions(
        self, patient: db.Patient, organization_id: UUID, today: date, *, add,
    ) -> None:
        """Due, overdue, deviated, and incomplete work - never the whole schedule."""
        patient_schedule = self.session.scalar(select(db.PatientSchedule).where(
            db.PatientSchedule.patient_id == patient.id,
            db.PatientSchedule.status == "ACTIVE",
        ).order_by(db.PatientSchedule.generated_at.desc()))
        if patient_schedule is None or patient_schedule.current_evaluation_id is None:
            return

        horizon = today + timedelta(days=UPCOMING_WINDOW_DAYS)
        rows = self.session.execute(
            select(db.PatientEvent, db.Event.code, db.Event.display_name, db.Event.event_type)
            .join(db.Event, db.Event.id == db.PatientEvent.event_definition_id)
            .where(
                db.PatientEvent.schedule_evaluation_id == patient_schedule.current_evaluation_id,
                db.PatientEvent.status == "RESOLVED",
                db.PatientEvent.nominal_start_date.is_not(None),
                # Bounded on purpose: an open-ended protocol must not put every
                # future cycle on the board (doc 3 section 28).
                db.PatientEvent.nominal_start_date <= horizon,
            )
            .order_by(db.PatientEvent.nominal_start_date)
        ).all()

        completed = self._completed_logical_ids(
            [row.PatientEvent for row in rows])
        for row in rows:
            event = row.PatientEvent
            if event.logical_occurrence_id in completed:
                continue
            deadline = event.latest_date or event.nominal_start_date
            assert event.nominal_start_date is not None
            if deadline is not None and today > deadline:
                add(
                    ActionCategory.VISIT_OVERDUE,
                    f"{row.display_name} was due on {deadline.isoformat()}",
                    target_type="PATIENT_EVENT", target_id=str(event.id),
                    due_date=deadline,
                )
            elif (event.earliest_date or event.nominal_start_date) <= today:
                add(
                    ActionCategory.VISIT_DUE,
                    f"{row.display_name} ({_visit_mode(row.event_type)}) is due",
                    target_type="PATIENT_EVENT", target_id=str(event.id),
                    due_date=event.nominal_start_date,
                )
            elif event.nominal_start_date <= horizon:
                add(
                    ActionCategory.VISIT_DUE,
                    f"{row.display_name} ({_visit_mode(row.event_type)}) is approaching",
                    target_type="PATIENT_EVENT", target_id=str(event.id),
                    due_date=event.nominal_start_date,
                )
            incomplete = self._incomplete_required_activities(event, today)
            if incomplete:
                add(
                    ActionCategory.ACTIVITY_INCOMPLETE,
                    f"{row.display_name}: {incomplete} required activity(ies) not recorded",
                    target_type="PATIENT_EVENT", target_id=str(event.id),
                    due_date=event.nominal_start_date,
                )

        try:
            report = self.deviations.report(
                patient.id, organization_id=organization_id, today=today)
        except (KeyError, ValueError):
            return
        for finding in report.deviations():
            if finding.deviation_type == DeviationType.MISSED:
                continue  # already surfaced as overdue
            add(
                ActionCategory.DEVIATION,
                f"{finding.display_name}: {finding.reason}",
                target_type="PATIENT_EVENT", due_date=finding.actual_date,
            )

    def _completed_logical_ids(self, events: list[db.PatientEvent]) -> set[UUID]:
        if not events:
            return set()
        return set(self.session.scalars(
            select(db.PatientEvent.logical_occurrence_id)
            .join(db.PatientEventOccurrence,
                  db.PatientEventOccurrence.patient_event_id == db.PatientEvent.id)
            .where(
                db.PatientEvent.logical_occurrence_id.in_(
                    [item.logical_occurrence_id for item in events]),
                db.PatientEventOccurrence.actual_date.is_not(None),
            )
        ))

    def _incomplete_required_activities(self, event: db.PatientEvent, today: date) -> int:
        """Count required activities still unrecorded on a visit day that has passed."""
        if event.nominal_start_date is None or event.nominal_start_date > today:
            return 0
        return len(list(self.session.scalars(select(db.PatientActivity.id).where(
            db.PatientActivity.patient_event_id == event.id,
            db.PatientActivity.requiredness == "REQUIRED",
            db.PatientActivity.actual_time.is_(None),
            db.PatientActivity.status.notin_(["NOT_APPLICABLE", "NOT_DONE", "COMPLETED"]),
        ))))


def _humanize(code: str) -> str:
    return code.replace("_", " ").title()


def _visit_mode(event_type: str) -> str:
    # Doc s13: the same vocabulary the extractor/adapter/validator write
    # (CORE_EVENT_TYPES), not a third list of near-miss strings. This used to
    # key on PHONE_CONTACT/REMOTE_VISIT/LAB_ONLY/IMAGING_ONLY/PROCEDURE_ONLY/
    # INPATIENT_CONFINEMENT, none of which a real event ever carries, so every
    # dashboard row silently fell back to the generic humanised label below.
    labels = {
        "SITE_VISIT": "site visit", "TELEPHONE_CONTACT": "telephone",
        "REMOTE": "remote visit", "HOME_VISIT": "home visit",
        "LABORATORY": "lab only", "IMAGING": "imaging",
        "PROCEDURE": "procedure", "INPATIENT_ADMISSION": "admission",
        "INPATIENT_DISCHARGE": "discharge",
    }
    return labels.get(event_type.upper(), event_type.replace("_", " ").lower())
