"""Turns a queued extraction run into an executed one.

The API queues a run and returns immediately, because reading a two-hundred page
protocol takes minutes. This module is what the background worker calls: it
fetches the stored document, turns it into pages, builds the model-backed
provider, and hands both to the pipeline.

Failures are recorded on the run rather than raised into the request that
started it. A sponsor who uploads a protocol while the model provider is down
should see "extraction failed, and why" against their upload - not a silent
QUEUED row that never moves.
"""

from __future__ import annotations

import logging
import os
from uuid import UUID

from sqlalchemy.orm import Session

from app.db import models as db
from app.services.extraction_service import ExtractionService
from .claude_provider import ClaudeExtractionProvider
from .graph import DocumentPage
from .llm_client import AnthropicClient, ExtractionModelError

log = logging.getLogger(__name__)

#: Beyond this the cached document prefix stops being economical and the model's
#: attention is better spent on a narrowed page range. A protocol longer than
#: this is extracted from its schedule sections only, which is what a human does.
MAX_DOCUMENT_CHARS = 600_000


def pages_from_pdf(pdf_bytes: bytes) -> list[DocumentPage]:
    """Read a PDF into per-page text.

    Page numbers are 1-based and preserved, because every piece of evidence the
    model cites is checked against a page a reviewer will open.
    """
    from protocol_document_index import _extract_pdf_text_pages

    return [
        DocumentPage(page_number=index + 1, text=text)
        for index, text in enumerate(_extract_pdf_text_pages(pdf_bytes))
    ]


def pages_from_text(text: str) -> list[DocumentPage]:
    """Split plain text on form feeds, falling back to a single page."""
    parts = text.split("\f") if "\f" in text else [text]
    return [
        DocumentPage(page_number=index + 1, text=part)
        for index, part in enumerate(parts)
    ]


def document_text(pages: list[DocumentPage]) -> str:
    """The document as the model sees it, with page markers it can cite."""
    rendered: list[str] = []
    used = 0
    for page in pages:
        block = f"\n--- page {page.page_number} ---\n{page.text}"
        if used + len(block) > MAX_DOCUMENT_CHARS:
            rendered.append(
                f"\n--- remaining {len(pages) - page.page_number + 1} pages omitted "
                "because the document exceeds the extraction size limit ---"
            )
            break
        rendered.append(block)
        used += len(block)
    return "".join(rendered)


def build_provider(
    pages: list[DocumentPage], *, schedule_name: str = "Schedule of Assessments",
) -> ClaudeExtractionProvider:
    """The model-backed provider, configured from the environment."""
    client = AnthropicClient(document_text=document_text(pages))
    return ClaudeExtractionProvider(client, schedule_name=schedule_name)


def provider_configured() -> bool:
    """Whether extraction can run at all, so callers can say so up front."""
    return bool(os.getenv("ANTHROPIC_API_KEY"))


async def load_pages(document_uri: str) -> list[DocumentPage]:
    """Fetch a stored protocol document and turn it into pages."""
    from storage import get_storage

    key = document_uri.split("://", 1)[-1] if "://" in document_uri else document_uri
    data, content_type = await get_storage().open(key)
    if "pdf" in (content_type or "").lower() or document_uri.lower().endswith(".pdf"):
        return pages_from_pdf(data)
    return pages_from_text(data.decode("utf-8", errors="replace"))


async def execute_run(session: Session, run_id: UUID) -> UUID | None:
    """Run one queued extraction to completion.

    Returns the new schedule version id, or None when extraction failed. The
    failure is already recorded on the run, so the caller does not have to.
    """
    run = session.get(db.ExtractionRun, run_id)
    if run is None:
        raise KeyError("extraction run not found")
    version = session.get(db.ProtocolVersion, run.protocol_version_id)
    if version is None:
        raise KeyError("protocol version not found")

    try:
        pages = await load_pages(version.document_uri)
        provider = build_provider(
            pages, schedule_name=version.document_name or "Schedule of Assessments")
        return ExtractionService(session).execute(
            run_id, provider=provider, pages=pages)
    except (ExtractionModelError, Exception) as error:  # noqa: BLE001
        session.rollback()
        failed = session.get(db.ExtractionRun, run_id, with_for_update=True)
        if failed is not None and failed.status not in {"COMPLETED"}:
            failed.status = "FAILED"
            failed.error_details = {
                "type": type(error).__name__, "message": str(error)[:2000],
            }
            session.commit()
        log.exception("extraction run %s failed", run_id)
        return None
