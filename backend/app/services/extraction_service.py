from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models as db
from app.db.repositories import ScheduleRepository
from app.domain.schedule.models import ScheduleStatus
from app.extraction.graph import (
    DocumentPage, ExtractionProvider, ExtractionResult, run_extraction,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ExtractionService:
    """Transactional boundary around a separately hosted extraction worker."""

    def __init__(self, session: Session):
        self.session = session

    def queue(
        self,
        *,
        organization_id: UUID,
        protocol_version: db.ProtocolVersion,
        idempotency_key: str | None,
        provider: str | None,
        model_name: str | None,
        model_version: str | None,
        prompt_version: str,
        configuration: dict[str, object] | None = None,
    ) -> db.ExtractionRun:
        if idempotency_key:
            existing = self.session.scalar(select(db.ExtractionRun).where(
                db.ExtractionRun.organization_id == organization_id,
                db.ExtractionRun.idempotency_key == idempotency_key,
            ))
            if existing:
                same_input = (
                    existing.protocol_version_id == protocol_version.id
                    and existing.document_hash == protocol_version.document_hash
                    and existing.prompt_version == prompt_version
                    and existing.model_name == model_name
                )
                if not same_input:
                    raise ValueError("idempotency key was already used for different extraction inputs")
                return existing
        run = db.ExtractionRun(
            organization_id=organization_id, protocol_version_id=protocol_version.id,
            idempotency_key=idempotency_key, status="QUEUED", provider=provider,
            model_name=model_name, model_version=model_version,
            prompt_version=prompt_version, schema_version="uctsm.v1",
            document_hash=protocol_version.document_hash,
            configuration=configuration or {}, trace={},
        )
        self.session.add(run)
        self.session.flush()
        self.session.add(db.AuditEvent(
            organization_id=organization_id, action="SCHEDULE_EXTRACTION_QUEUED",
            entity_type="EXTRACTION_RUN", entity_id=run.id, before=None,
            after={"protocol_version_id": str(protocol_version.id), "document_hash": protocol_version.document_hash},
        ))
        return run

    def complete(self, run_id: UUID, result: ExtractionResult) -> UUID:
        run = self.session.get(db.ExtractionRun, run_id, with_for_update=True)
        if run is None:
            raise KeyError("extraction run not found")
        if run.status not in {"QUEUED", "RUNNING", "PARTIAL"}:
            raise ValueError(f"cannot complete extraction in {run.status} state")
        run.trace = {"nodes": result.extraction_trace}
        run.completed_at = utc_now()
        if result.schedule is None:
            run.status = "FAILED"
            run.error_details = {"issues": [item.model_dump(mode="json") for item in result.issues]}
            raise ValueError("extraction did not produce a schema-valid schedule")
        schedule = result.schedule.model_copy(deep=True)
        schedule.validation_issues = result.issues
        schedule.schedule_metadata.status = ScheduleStatus.VALIDATION_REQUIRED
        definition = self.session.scalar(select(db.ScheduleDefinition).where(
            db.ScheduleDefinition.protocol_version_id == run.protocol_version_id,
            db.ScheduleDefinition.name == schedule.schedule_metadata.name,
            db.ScheduleDefinition.schedule_type == schedule.schedule_metadata.schedule_type,
        ))
        if definition is None:
            definition = db.ScheduleDefinition(
                protocol_version_id=run.protocol_version_id,
                name=schedule.schedule_metadata.name,
                description=schedule.schedule_metadata.description,
                schedule_type=schedule.schedule_metadata.schedule_type,
            )
            self.session.add(definition)
            self.session.flush()
        max_version = self.session.scalar(select(func.max(db.ScheduleVersion.version_number)).where(
            db.ScheduleVersion.schedule_definition_id == definition.id,
        )) or 0
        schedule.schedule_metadata.version_number = max_version + 1
        schedule.schedule_metadata.protocol_version_id = run.protocol_version_id
        ScheduleRepository(self.session).persist_draft(
            schedule, schedule_definition_id=definition.id, extraction_run_id=run.id,
        )
        run.status = "COMPLETED"
        self.session.add(db.AuditEvent(
            organization_id=run.organization_id, action="SCHEDULE_EXTRACTION_COMPLETED",
            entity_type="SCHEDULE_VERSION", entity_id=schedule.schedule_version_id,
            before=None, after={"extraction_run_id": str(run.id), "issues": len(result.issues)},
        ))
        return schedule.schedule_version_id

    def execute(
        self,
        run_id: UUID,
        *,
        provider: ExtractionProvider,
        pages: list[DocumentPage],
    ) -> UUID:
        """Worker entry point from parsed private document pages to persisted draft."""
        run = self.session.get(db.ExtractionRun, run_id, with_for_update=True)
        if run is None:
            raise KeyError("extraction run not found")
        if run.status != "QUEUED":
            raise ValueError(f"cannot execute extraction in {run.status} state")
        run.status = "RUNNING"
        run.started_at = utc_now()
        self.session.flush()
        try:
            result = run_extraction(
                provider, document_hash=run.document_hash, pages=pages,
            )
            return self.complete(run_id, result)
        except Exception as error:
            run.status = "FAILED"
            run.completed_at = utc_now()
            run.error_details = {"type": type(error).__name__, "message": str(error)}
            raise
