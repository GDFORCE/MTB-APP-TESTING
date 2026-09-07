"""Give an operational trial its canonical schedule versions.

The live Add Trial flow already extracts a protocol and stores a
``CanonicalSchedulePlan`` per schedule definition. This service takes those
definitions and produces one UCTSM DRAFT schedule version each, so the Sponsor/PI
review, approval, patient assignment and evaluation all happen in the canonical
engine rather than in a second, weaker copy of it.

It owns no scheduling logic. Translation lives in ``canonical_import``; persistence,
version numbering and audit live in ``ExtractionService``. This module only resolves
the trial/protocol/version rows the canonical side needs and keeps the whole thing
idempotent, because Add Trial can be retried and a trial must not accumulate a new
draft per retry.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models as db
from app.services.canonical_import import extraction_result_from_plan
from app.services.extraction_service import ExtractionService

PROVIDER = "mtb.protocol_extraction"
PROMPT_VERSION = "mtb.canonical-plan.v2"


@dataclass(frozen=True)
class ImportedSchedule:
    """One canonical draft produced from one operational schedule definition."""

    external_schedule_definition_id: str
    schedule_definition_id: UUID
    schedule_version_id: UUID
    version_number: int
    status: str
    name: str
    created: bool


def plan_fingerprint(plan: dict[str, Any]) -> str:
    """Content address for an extracted plan.

    The live flow does not retain the uploaded PDF, so the protocol version is
    identified by the content that was actually extracted from it. Two imports of
    the same plan therefore resolve to the same protocol version instead of
    stacking unrelated versions onto one trial.
    """
    payload = json.dumps(plan or {}, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _trial(
    session: Session, *, organization_id: UUID, external_trial_id: str,
    study_title: str | None, protocol_id: str | None, actor_id: UUID,
) -> db.Trial:
    row = session.scalar(select(db.Trial).where(
        db.Trial.organization_id == organization_id,
        db.Trial.external_trial_id == external_trial_id,
    ))
    if row is not None:
        return row
    row = db.Trial(
        organization_id=organization_id, external_trial_id=external_trial_id,
        study_title=study_title, protocol_id=protocol_id,
    )
    session.add(row)
    session.flush()
    session.add(db.AuditEvent(
        organization_id=organization_id, actor_id=actor_id, action="TRIAL_CREATED",
        entity_type="TRIAL", entity_id=row.id, before=None,
        after={"external_trial_id": external_trial_id, "source": PROVIDER},
    ))
    return row


def _protocol(session: Session, trial: db.Trial, protocol_number: str) -> db.Protocol:
    row = session.scalar(select(db.Protocol).where(db.Protocol.trial_id == trial.id))
    if row is not None:
        return row
    row = db.Protocol(trial_id=trial.id, protocol_number=protocol_number)
    session.add(row)
    session.flush()
    return row


def _protocol_version(
    session: Session, protocol: db.Protocol, *, document_hash: str,
    document_name: str, document_uri: str, version_label: str, actor_id: UUID,
) -> db.ProtocolVersion:
    row = session.scalar(select(db.ProtocolVersion).where(
        db.ProtocolVersion.protocol_id == protocol.id,
        db.ProtocolVersion.document_hash == document_hash,
    ))
    if row is not None:
        return row
    taken = set(session.scalars(select(db.ProtocolVersion.version_label).where(
        db.ProtocolVersion.protocol_id == protocol.id,
    )))
    # Two different extractions can both name themselves "v1.0" - a re-upload of
    # a corrected PDF, most often. The label is the protocol's own text and is
    # kept, but it has to stay distinguishable within one protocol.
    label = version_label or f"Extracted {len(taken) + 1}"
    if label in taken:
        base_label, suffix = label, 2
        while label in taken:
            label = f"{base_label} ({suffix})"
            suffix += 1
    row = db.ProtocolVersion(
        protocol_id=protocol.id,
        version_label=label,
        document_name=document_name, document_uri=document_uri,
        document_hash=document_hash, uploaded_by=actor_id,
        extraction_status="COMPLETED",
        metadata_json={"source": PROVIDER},
    )
    session.add(row)
    session.flush()
    # An amendment must never silently replace the version already in use for
    # enrolment; that switch is an explicit approval step, not an import effect.
    if protocol.current_version_id is None:
        protocol.current_version_id = row.id
    return row


def _existing_version_for_run(session: Session, run_id: UUID) -> db.ScheduleVersion | None:
    return session.scalar(select(db.ScheduleVersion).where(
        db.ScheduleVersion.extraction_run_id == run_id,
    ))


def import_schedule_definitions(
    session: Session,
    *,
    organization_id: UUID,
    actor_id: UUID,
    external_trial_id: str,
    protocol_number: str,
    study_title: str | None = None,
    definitions: Iterable[dict[str, Any]],
) -> tuple[db.Trial, list[ImportedSchedule]]:
    """Import every operational schedule definition as a canonical draft version.

    ``definitions`` are the operational documents, each carrying ``id``,
    ``canonical_plan`` and ``evidence_facts``. A definition with no canonical plan
    is skipped rather than turned into an empty schedule: an empty schedule would
    look approvable while promising a patient nothing.
    """
    trial = _trial(
        session, organization_id=organization_id, external_trial_id=external_trial_id,
        study_title=study_title, protocol_id=protocol_number, actor_id=actor_id,
    )
    protocol = _protocol(session, trial, protocol_number or external_trial_id)
    service = ExtractionService(session)
    imported: list[ImportedSchedule] = []

    for definition in definitions:
        plan = definition.get("canonical_plan")
        external_id = str(definition.get("id") or "")
        if not isinstance(plan, dict) or not plan.get("events") or not external_id:
            continue
        name = (
            str(definition.get("option_label") or "").strip()
            or str((plan.get("title") or "")).strip()
            or "Primary"
        )
        fingerprint = plan_fingerprint(plan)
        protocol_version = _protocol_version(
            session, protocol, document_hash=fingerprint,
            document_name=str(definition.get("file_name") or "protocol.pdf"),
            document_uri=f"private://protocol-extractions/{external_id}",
            version_label=str(plan.get("protocol_version") or "").strip(),
            actor_id=actor_id,
        )

        run = service.queue(
            organization_id=organization_id, protocol_version=protocol_version,
            idempotency_key=f"canonical-import:{external_id}", provider=PROVIDER,
            model_name=str(definition.get("model_name") or "") or None,
            model_version=None, prompt_version=PROMPT_VERSION,
            configuration={"external_schedule_definition_id": external_id},
        )
        session.flush()
        already = _existing_version_for_run(session, run.id)
        if already is not None:
            # A retried Add Trial must reuse the draft the reviewer may already be
            # working on, not silently create a second one beside it.
            schedule_definition = session.get(db.ScheduleDefinition, already.schedule_definition_id)
            imported.append(ImportedSchedule(
                external_schedule_definition_id=external_id,
                schedule_definition_id=already.schedule_definition_id,
                schedule_version_id=already.id, version_number=already.version_number,
                status=already.status,
                name=schedule_definition.name if schedule_definition else name,
                created=False,
            ))
            continue

        result = extraction_result_from_plan(
            plan, name=name,
            description=str(definition.get("option_description") or "").strip() or None,
            evidence_facts=definition.get("evidence_facts") or [],
            source={
                "external_schedule_definition_id": external_id,
                "external_trial_id": external_trial_id,
                "document_hash": fingerprint,
            },
        )
        schedule_version_id = service.complete(run.id, result)
        version = session.get(db.ScheduleVersion, schedule_version_id)
        assert version is not None
        schedule_definition = session.get(db.ScheduleDefinition, version.schedule_definition_id)
        if schedule_definition is not None:
            # Keeps the operational document and the canonical definition joined,
            # which is what the parity check and the read-mode switch look up.
            schedule_definition.external_schedule_definition_id = external_id
        imported.append(ImportedSchedule(
            external_schedule_definition_id=external_id,
            schedule_definition_id=version.schedule_definition_id,
            schedule_version_id=version.id, version_number=version.version_number,
            status=version.status, name=name, created=True,
        ))

    return trial, imported


@dataclass(frozen=True)
class ScheduleVersionRow:
    """One row of the trial-level protocol/schedule version table (doc 9)."""

    schedule_definition_id: UUID
    schedule_version_id: UUID
    name: str
    schedule_type: str
    version_number: int
    status: str
    protocol_version_label: str
    effective_from: Any
    approved_at: Any
    is_current_protocol_version: bool
    patients: int


def list_schedule_versions(
    session: Session, *, organization_id: UUID, external_trial_id: str,
) -> list[ScheduleVersionRow]:
    """Every canonical version this trial has, newest first, with its patient count.

    A superseded version is listed, never hidden: patients enrolled under it stay
    on it, so "which schedule is this patient actually on" has to remain
    answerable after an amendment.
    """
    trial = session.scalar(select(db.Trial).where(
        db.Trial.organization_id == organization_id,
        db.Trial.external_trial_id == external_trial_id,
    ))
    if trial is None:
        return []
    rows = session.execute(
        select(db.ScheduleDefinition, db.ScheduleVersion, db.ProtocolVersion, db.Protocol)
        .join(db.ScheduleVersion,
              db.ScheduleVersion.schedule_definition_id == db.ScheduleDefinition.id)
        .join(db.ProtocolVersion,
              db.ProtocolVersion.id == db.ScheduleDefinition.protocol_version_id)
        .join(db.Protocol, db.Protocol.id == db.ProtocolVersion.protocol_id)
        .where(db.Protocol.trial_id == trial.id)
        .order_by(db.ScheduleDefinition.name, db.ScheduleVersion.version_number.desc())
    ).all()
    counts = dict(session.execute(
        select(db.Patient.current_schedule_version_id, func.count(db.Patient.id))
        .where(db.Patient.trial_id == trial.id)
        .group_by(db.Patient.current_schedule_version_id)
    ).all())
    return [
        ScheduleVersionRow(
            schedule_definition_id=definition.id,
            schedule_version_id=version.id,
            name=definition.name,
            schedule_type=definition.schedule_type,
            version_number=version.version_number,
            status=version.status,
            protocol_version_label=protocol_version.version_label,
            effective_from=version.effective_from,
            approved_at=version.approved_at,
            is_current_protocol_version=(
                protocol.current_version_id == protocol_version.id),
            patients=int(counts.get(version.id, 0)),
        )
        for definition, version, protocol_version, protocol in rows
    ]
