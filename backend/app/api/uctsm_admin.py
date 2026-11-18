"""Platform-admin view of the PROTOCOL_DEFINED extension-handler process.

Deliberately kept SQL-only (no MongoDB access) so it stays within the
existing architecture boundary: the UCTSM domain is PostgreSQL-authoritative
and does not read or write legacy Mongo collections (see
docs/universal-schedule-engine-architecture.md section 2). This is a
platform-wide admin capability -- which protocol pattern to build next is a
product-wide engineering question, not scoped to one organization -- so it
intentionally does not use `_identity`'s per-tenant organization scoping from
`uctsm.py`; it is gated by role only, the same way `admin_routes.py` gates
its platform-wide screens.
"""
from typing import Annotated, Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session

from app.db.base import get_session
from app.db.repositories import (
    unsupported_extension_handler_frequency, unsupported_handler_evidence,
    unsupported_pattern_alert_payloads,
)
from app.domain.schedule.exceptions import ImmutableScheduleError
from app.services.schedule_service import ScheduleReviewService


def _require_admin(user: dict[str, Any]) -> None:
    if user.get("role") != "admin":
        raise HTTPException(403, "Only platform administrators can view protocol extraction gaps")


def create_uctsm_admin_router(current_user_dependency: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/api/uctsm/admin", tags=["universal-schedule-admin"])
    User = Annotated[dict[str, Any], Depends(current_user_dependency)]
    Db = Annotated[Session, Depends(get_session)]

    @router.get("/unsupported-handlers")
    def list_unsupported_handlers(user: User, session: Db):
        """Ranked by real impact: the most-needed missing deterministic
        handler first. Drives the "what should engineering build next"
        screen -- see extension_handlers.py for how a handler, once written,
        is installed."""
        _require_admin(user)
        return {"handlers": unsupported_extension_handler_frequency(session)}

    @router.get("/unsupported-handlers/alerts")
    def list_alertable_handlers(user: User, session: Db):
        """Ready-to-act ticket payloads (see `system_alerts` schema in
        `admin_routes.py`) for handlers not yet alerted. Returning this
        separately from the raw ranking keeps "what should I be notified
        about" distinct from "show me everything, including what's already
        been triaged"."""
        _require_admin(user)
        return {"alerts": unsupported_pattern_alert_payloads(session)}

    @router.get("/unsupported-handlers/{handler}/evidence")
    def handler_evidence(handler: Annotated[str, Path(min_length=1)], user: User, session: Db):
        """The actual protocol text behind this gap -- deduplicated real
        quotes with page numbers -- so a developer can design the fix without
        manually opening every affected protocol's review screen."""
        _require_admin(user)
        rows = unsupported_handler_evidence(session, handler)
        return {"handler": handler, "evidence": [
            {**row, "schedule_version_id": str(row["schedule_version_id"])} for row in rows
        ]}

    @router.post("/unsupported-handlers/{handler}/revalidate")
    def revalidate_handler(
        handler: Annotated[str, Path(min_length=1)], user: User, session: Db,
    ):
        """Apply a newly-registered handler to every protocol it was already
        blocking, in one action -- never re-extracts or re-calls the AI; only
        re-runs the deterministic validator now that the handler exists."""
        _require_admin(user)
        try:
            results = ScheduleReviewService(session).revalidate_for_handler(handler)
            session.commit()
        except ImmutableScheduleError as error:
            session.rollback()
            raise HTTPException(409, str(error)) from error
        return {
            "handler": handler,
            "processed": len(results),
            "now_fully_clear": sum(1 for item in results if item["fully_clear"]),
            "results": [
                {**item, "schedule_version_id": str(item["schedule_version_id"])}
                for item in results
            ],
        }

    return router
