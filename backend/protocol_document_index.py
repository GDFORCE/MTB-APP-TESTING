"""Deterministic PDF page indexing and protocol-schedule retrieval.

The AI extraction pipeline should not send an entire large protocol to every
agent.  This module extracts the PDF text once, stores a content-addressed page
index, and selects the pages most relevant to a specific schedule task.

The retriever is deliberately deterministic.  It does not make clinical
decisions and it does not replace evidence validation; it only narrows the
source context while retaining PDF page numbers and stable page evidence IDs.
Pages with little or no embedded text are reported as image/scanned pages so a
caller can route them to a vision/OCR fallback instead of treating them as
empty evidence.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field


INDEX_SCHEMA_VERSION = "protocol-page-index-v1"
MIN_TEXT_CHARACTERS = 40

RetrievalTask = Literal[
    "classification",
    "schedule_discovery",
    "timing",
    "activities",
    "review",
]


class PdfIndexingError(RuntimeError):
    """Raised when a PDF cannot be opened or its pages cannot be indexed."""


class IndexedProtocolPage(BaseModel):
    """Text and retrieval metadata for one one-based PDF page."""

    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    evidence_id: str
    text: str
    searchable_text: str
    text_sha256: str
    character_count: int = Field(ge=0)
    text_status: Literal["text", "sparse_text", "image_or_empty"]
    section_markers: list[str] = Field(default_factory=list)


class ProtocolDocumentIndex(BaseModel):
    """Content-addressed, serializable index of a protocol PDF."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = INDEX_SCHEMA_VERSION
    document_sha256: str
    page_count: int = Field(ge=1)
    pages: list[IndexedProtocolPage]
    boilerplate_lines: list[str] = Field(default_factory=list)


class RetrievedProtocolPage(BaseModel):
    """A selected page plus an auditable explanation of why it was selected."""

    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    evidence_id: str
    score: int = Field(ge=0)
    reasons: list[str] = Field(default_factory=list)
    text: str
    text_status: Literal["text", "sparse_text", "image_or_empty"]


class ProtocolPageSelection(BaseModel):
    """Schedule-focused context selected from a page index."""

    model_config = ConfigDict(extra="forbid")

    document_sha256: str
    task: RetrievalTask
    pages: list[RetrievedProtocolPage]
    omitted_page_count: int = Field(ge=0)
    image_or_empty_pages: list[int] = Field(default_factory=list)
    retrieval_warnings: list[str] = Field(default_factory=list)

    @property
    def page_numbers(self) -> list[int]:
        return [page.page_number for page in self.pages]


_LIGATURES = str.maketrans(
    {
        "\ufb00": "ff",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
    }
)


def _clean_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").translate(_LIGATURES)
    text = text.replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    cleaned_lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    # Preserve line boundaries because section headings and tables depend on
    # them, but collapse runs of blank lines produced by PDF text extraction.
    return re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned_lines)).strip()


def _normalise_for_search(text: str) -> str:
    text = _clean_text(text).casefold()
    text = re.sub(r"[^\w%±+/<>=.\-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalised_lines(text: str) -> list[str]:
    result: list[str] = []
    for line in _clean_text(text).splitlines():
        normalised = _normalise_for_search(line)
        if 4 <= len(normalised) <= 160:
            result.append(normalised)
    return result


_SECTION_PATTERNS = (
    re.compile(r"\b(?:schedule|table) of (?:events|assessments|activities|procedures)\b", re.I),
    re.compile(r"\bvisit schedule\b", re.I),
    re.compile(r"\bstudy (?:design|schema|flow chart|flowchart)\b", re.I),
    re.compile(r"\b(?:treatment|study) period\b", re.I),
    re.compile(r"\b(?:screening|follow[- ]?up|end of (?:study|treatment))\b", re.I),
    re.compile(r"\b(?:appendix|annex(?:ure)?)\b", re.I),
)


def _section_markers(text: str) -> list[str]:
    markers: list[str] = []
    for line in _clean_text(text).splitlines():
        if not line or len(line) > 180:
            continue
        if any(pattern.search(line) for pattern in _SECTION_PATTERNS):
            marker = re.sub(r"\s+", " ", line).strip()
            if marker not in markers:
                markers.append(marker)
        if len(markers) >= 8:
            break
    return markers


def _text_status(text: str) -> Literal["text", "sparse_text", "image_or_empty"]:
    meaningful = re.sub(r"\W+", "", text, flags=re.UNICODE)
    if len(meaningful) < MIN_TEXT_CHARACTERS:
        return "image_or_empty"
    if len(meaningful) < 160:
        return "sparse_text"
    return "text"


def _extract_pdf_text_pages(pdf_bytes: bytes) -> list[str]:
    """Extract embedded text from every page using pypdfium2.

    Kept as a small boundary function so callers/tests can replace only the
    PDF library interaction without changing index or retrieval behaviour.
    """

    if not pdf_bytes:
        raise PdfIndexingError("the uploaded PDF is empty")
    try:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(pdf_bytes)
    except Exception as exc:  # pypdfium exposes several provider exceptions
        raise PdfIndexingError(f"could not open the protocol PDF: {exc}") from exc

    pages: list[str] = []
    try:
        if len(document) < 1:
            raise PdfIndexingError("the protocol PDF contains no pages")
        for page_number in range(len(document)):
            page = None
            text_page = None
            try:
                page = document[page_number]
                text_page = page.get_textpage()
                pages.append(text_page.get_text_range() or "")
            except Exception as exc:
                raise PdfIndexingError(
                    f"could not extract text from PDF page {page_number + 1}: {exc}"
                ) from exc
            finally:
                if text_page is not None:
                    text_page.close()
                if page is not None:
                    page.close()
    finally:
        document.close()
    return pages


def build_protocol_document_index(
    pdf_bytes: bytes,
    *,
    extracted_pages: Iterable[str] | None = None,
) -> ProtocolDocumentIndex:
    """Build a deterministic text index for a PDF.

    ``extracted_pages`` is primarily an adapter/testing hook.  Production
    callers normally omit it and let pypdfium2 read the PDF exactly once.
    """

    digest = hashlib.sha256(pdf_bytes).hexdigest()
    raw_pages = list(extracted_pages) if extracted_pages is not None else _extract_pdf_text_pages(pdf_bytes)
    if not raw_pages:
        raise PdfIndexingError("the protocol PDF contains no pages")

    cleaned_pages = [_clean_text(text) for text in raw_pages]
    page_lines = [_normalised_lines(text) for text in cleaned_pages]
    line_frequency: Counter[str] = Counter()
    for lines in page_lines:
        # A repeated line counts at most once per page.
        line_frequency.update(set(lines))

    repeat_threshold = max(3, math.ceil(len(cleaned_pages) * 0.45))
    boilerplate = sorted(
        line
        for line, frequency in line_frequency.items()
        if frequency >= repeat_threshold and not any(
            key in line
            for key in ("schedule of", "visit schedule", "study schema")
        )
    )
    boilerplate_set = set(boilerplate)

    indexed_pages: list[IndexedProtocolPage] = []
    for position, (text, lines) in enumerate(zip(cleaned_pages, page_lines), start=1):
        searchable = " ".join(line for line in lines if line not in boilerplate_set)
        text_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        indexed_pages.append(
            IndexedProtocolPage(
                page_number=position,
                evidence_id=f"page-{position}-{text_digest[:12]}",
                text=text,
                searchable_text=searchable,
                text_sha256=text_digest,
                character_count=len(text),
                text_status=_text_status(text),
                section_markers=_section_markers(text),
            )
        )

    return ProtocolDocumentIndex(
        document_sha256=digest,
        page_count=len(indexed_pages),
        pages=indexed_pages,
        boilerplate_lines=boilerplate,
    )


class ProtocolDocumentIndexCache:
    """Optional persistent content-addressed cache for page indexes."""

    def __init__(self, cache_directory: str | Path):
        self.directory = Path(cache_directory)

    def _path(self, digest: str) -> Path:
        return self.directory / f"{digest}.{INDEX_SCHEMA_VERSION}.json"

    def load(self, pdf_bytes: bytes) -> ProtocolDocumentIndex | None:
        digest = hashlib.sha256(pdf_bytes).hexdigest()
        path = self._path(digest)
        if not path.is_file():
            return None
        try:
            index = ProtocolDocumentIndex.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if index.document_sha256 != digest or index.schema_version != INDEX_SCHEMA_VERSION:
            return None
        return index

    def store(self, index: ProtocolDocumentIndex) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self._path(index.document_sha256)
        # Write a complete temporary file in the destination directory and
        # atomically replace the cache entry. Concurrent requests can safely
        # converge on the same content-addressed index.
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{index.document_sha256[:12]}-",
            suffix=".tmp",
            dir=self.directory,
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as temporary:
                temporary.write(index.model_dump_json())
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, target)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return target

    def get_or_build(self, pdf_bytes: bytes) -> ProtocolDocumentIndex:
        cached = self.load(pdf_bytes)
        if cached is not None:
            return cached
        index = build_protocol_document_index(pdf_bytes)
        self.store(index)
        return index


# Weighted phrases are intentionally explicit and auditable. Generic words
# such as "day" and "visit" receive low weight; schedule section titles and
# constraint phrases receive high weight.
_COMMON_PHRASES: tuple[tuple[str, int], ...] = (
    ("schedule of events", 35),
    ("schedule of assessments", 35),
    ("schedule of activities", 35),
    ("schedule of procedures", 35),
    ("visit schedule", 30),
    ("study schema", 20),
    ("study design", 8),
    ("treatment schedule", 18),
    ("assessment schedule", 18),
    ("visit window", 22),
    ("study visit", 6),
    ("screening", 5),
    ("follow-up", 7),
    ("follow up", 7),
    ("end of treatment", 7),
    ("end of study", 7),
)

_TASK_PHRASES: dict[RetrievalTask, tuple[tuple[str, int], ...]] = {
    "classification": (
        ("protocol synopsis", 18),
        ("synopsis", 10),
        ("protocol amendment", 18),
        ("study design", 12),
        ("treatment period", 8),
        ("crossover", 14),
        ("randomized", 6),
        ("randomised", 6),
        ("cycle", 4),
        ("cohort", 5),
        ("arm", 3),
    ),
    "schedule_discovery": (
        ("visit", 3),
        ("timepoint", 5),
        ("time point", 5),
        ("period i", 5),
        ("period ii", 5),
        ("cycle", 4),
        ("day 1", 3),
        ("week", 2),
        ("month", 2),
        ("footnote", 5),
    ),
    "timing": (
        ("visit window", 25),
        ("window", 8),
        ("within", 3),
        ("prior to", 5),
        ("after the last", 6),
        ("post-dose", 5),
        ("post dose", 5),
        ("pre-dose", 5),
        ("pre dose", 5),
        ("washout", 12),
        ("cycle length", 12),
        ("dosing day", 8),
        ("calendar month", 10),
        ("±", 5),
        ("every", 3),
        ("day", 2),
        ("week", 3),
        ("month", 3),
        ("year", 3),
        ("hour", 3),
    ),
    "activities": (
        ("procedure", 7),
        ("assessment", 6),
        ("laboratory", 5),
        ("hematology", 6),
        ("blood sample", 6),
        ("pharmacokinetic", 8),
        ("vital signs", 7),
        ("electrocardiogram", 7),
        ("ecg", 4),
        ("adverse event", 4),
        ("footnote", 6),
    ),
    "review": (
        ("visit", 3),
        ("window", 8),
        ("footnote", 7),
        ("cycle", 5),
        ("period", 3),
        ("day", 2),
        ("week", 3),
        ("month", 3),
        ("procedure", 4),
        ("assessment", 4),
        ("dose", 3),
        ("conditional", 6),
        ("if clinically indicated", 8),
    ),
}

_NEGATIVE_PHRASES: tuple[tuple[str, int], ...] = (
    ("table of contents", 22),
    ("list of abbreviations", 10),
    ("references", 5),
    ("bibliography", 10),
)


def _phrase_count(text: str, phrase: str) -> int:
    if not phrase:
        return 0
    # Substring counting is appropriate for multi-word protocol terminology;
    # cap repeats so a dense narrative page cannot beat a schedule heading by
    # repeating one generic word dozens of times.
    return min(text.count(phrase.casefold()), 4)


def _score_page(page: IndexedProtocolPage, task: RetrievalTask) -> tuple[int, list[str]]:
    text = page.searchable_text
    if not text:
        return 0, []
    score = 0
    reasons: list[str] = []
    for phrase, weight in (*_COMMON_PHRASES, *_TASK_PHRASES[task]):
        count = _phrase_count(text, phrase)
        if count:
            contribution = weight + (count - 1) * max(1, weight // 4)
            score += contribution
            reasons.append(f"{phrase} (+{contribution})")
    for phrase, penalty in _NEGATIVE_PHRASES:
        if phrase in text:
            score = max(0, score - penalty)
            reasons.append(f"{phrase} (-{penalty})")

    # Table-like pages frequently contain several distinct timing units even
    # when the schedule title appeared only on the first page of the table.
    timing_classes = sum(
        bool(re.search(pattern, text))
        for pattern in (
            r"\bday\s*[+-]?\d+\b",
            r"\bweek\s*\d+\b",
            r"\bmonth\s*\d+\b",
            r"\bcycle\s*\d+\b",
            r"\b\d+(?:\.\d+)?\s*(?:hours?|hrs?|minutes?|mins?)\b",
            r"(?:±|\+/-)\s*\d+",
        )
    )
    if timing_classes >= 2:
        bonus = min(18, timing_classes * 4)
        score += bonus
        reasons.append(f"multiple timing patterns (+{bonus})")
    if page.section_markers:
        score += 5
        reasons.append("schedule/design section marker (+5)")
    return max(0, score), reasons


def retrieve_protocol_pages(
    index: ProtocolDocumentIndex,
    *,
    task: RetrievalTask = "schedule_discovery",
    max_pages: int = 24,
    neighbour_radius: int = 1,
    minimum_seed_score: int = 6,
) -> ProtocolPageSelection:
    """Select schedule-relevant pages for one extraction task.

    High-scoring pages are seeds. Adjacent pages are then included because
    protocol tables and their footnotes commonly span page boundaries. The
    final result is ordered by original PDF page number, never by score.
    """

    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    if neighbour_radius < 0:
        raise ValueError("neighbour_radius cannot be negative")
    if index.page_count != len(index.pages):
        raise ValueError("index page_count does not match its pages")

    scored: dict[int, tuple[int, list[str]]] = {
        page.page_number: _score_page(page, task) for page in index.pages
    }
    ranked = sorted(
        (
            (score, page_number)
            for page_number, (score, _reasons) in scored.items()
            if score >= minimum_seed_score
        ),
        key=lambda item: (-item[0], item[1]),
    )

    selected: dict[int, tuple[int, list[str]]] = {}

    def add_page(page_number: int, score: int, reasons: list[str]) -> bool:
        if page_number < 1 or page_number > index.page_count:
            return False
        if page_number in selected:
            old_score, old_reasons = selected[page_number]
            selected[page_number] = (max(old_score, score), list(dict.fromkeys([*old_reasons, *reasons])))
            return True
        if len(selected) >= max_pages:
            return False
        selected[page_number] = (score, reasons)
        return True

    # The beginning of the synopsis is important to document/task
    # classification even when it does not contain schedule keywords.
    if task == "classification":
        for page_number in range(1, min(3, index.page_count) + 1):
            score, reasons = scored[page_number]
            add_page(page_number, score, ["document opening/synopsis context", *reasons])

    for score, seed_page in ranked:
        if len(selected) >= max_pages:
            break
        _, reasons = scored[seed_page]
        if not add_page(seed_page, score, ["relevance seed", *reasons]):
            break
        # Prefer the immediately following page first: schedule table titles
        # tend to be on the first page and their continuation/footnotes follow.
        neighbour_offsets: list[int] = []
        for distance in range(1, neighbour_radius + 1):
            neighbour_offsets.extend((distance, -distance))
        for offset in neighbour_offsets:
            neighbour = seed_page + offset
            if 1 <= neighbour <= index.page_count:
                neighbour_score, neighbour_reasons = scored[neighbour]
                add_page(
                    neighbour,
                    neighbour_score,
                    [f"adjacent to relevant page {seed_page}", *neighbour_reasons],
                )

    warnings: list[str] = []
    if not selected:
        fallback_count = min(max_pages, 3, index.page_count)
        for page_number in range(1, fallback_count + 1):
            add_page(page_number, 0, ["fallback: no schedule-keyword page found"])
        warnings.append(
            "No schedule-relevant embedded text was found; selection uses opening pages only."
        )

    image_pages = [page.page_number for page in index.pages if page.text_status == "image_or_empty"]
    selected_image_pages = [number for number in sorted(selected) if number in image_pages]
    if image_pages:
        warnings.append(
            f"{len(image_pages)} page(s) have no usable embedded text and require vision/OCR review."
        )
    if selected_image_pages:
        warnings.append(
            "Selected context includes image/empty pages: "
            + ", ".join(str(number) for number in selected_image_pages)
            + "."
        )

    page_by_number = {page.page_number: page for page in index.pages}
    result_pages: list[RetrievedProtocolPage] = []
    for page_number in sorted(selected):
        page = page_by_number[page_number]
        score, reasons = selected[page_number]
        result_pages.append(
            RetrievedProtocolPage(
                page_number=page_number,
                evidence_id=page.evidence_id,
                score=score,
                reasons=reasons,
                text=page.text,
                text_status=page.text_status,
            )
        )

    return ProtocolPageSelection(
        document_sha256=index.document_sha256,
        task=task,
        pages=result_pages,
        omitted_page_count=index.page_count - len(result_pages),
        image_or_empty_pages=image_pages,
        retrieval_warnings=warnings,
    )


class ProtocolPageChunk(BaseModel):
    """One deterministic, non-scored slice of a protocol document.

    Unlike ProtocolPageSelection (keyword-scored, may omit low-scoring pages),
    chunk core_page_numbers partition the ENTIRE document with no gaps and no
    overlap between chunks -- every page belongs to exactly one chunk's core,
    so full coverage is a structural guarantee rather than a scoring outcome.
    context_page_numbers extends a few pages past each edge (a table or a
    governing rule can straddle a chunk boundary) purely so that boundary
    content is legible; a fact is only this chunk's responsibility when its
    page is in core_page_numbers, never when it is context-only, so the same
    fact is never claimed by two chunks.
    """

    model_config = ConfigDict(extra="forbid")

    chunk_index: int = Field(ge=0)
    document_sha256: str
    core_page_numbers: list[int]
    context_page_numbers: list[int]
    pages: list[RetrievedProtocolPage]


def chunk_protocol_pages(
    index: ProtocolDocumentIndex,
    *,
    core_pages: int = 22,
    overlap_pages: int = 4,
) -> list[ProtocolPageChunk]:
    """Partition every page of the document into full-coverage chunks.

    No scoring, no omission: every page number from 1 to index.page_count
    appears in exactly one chunk's core_page_numbers.
    """

    if core_pages < 1:
        raise ValueError("core_pages must be at least 1")
    if overlap_pages < 0:
        raise ValueError("overlap_pages cannot be negative")

    page_by_number = {page.page_number: page for page in index.pages}
    chunks: list[ProtocolPageChunk] = []
    start = 1
    chunk_index = 0
    while start <= index.page_count:
        end = min(start + core_pages - 1, index.page_count)
        context_start = max(1, start - overlap_pages)
        context_end = min(index.page_count, end + overlap_pages)
        core_numbers = list(range(start, end + 1))
        context_numbers = list(range(context_start, context_end + 1))
        pages = [
            RetrievedProtocolPage(
                page_number=number,
                evidence_id=page_by_number[number].evidence_id,
                score=0,
                reasons=["deterministic full-coverage chunk"],
                text=page_by_number[number].text,
                text_status=page_by_number[number].text_status,
            )
            for number in context_numbers
        ]
        chunks.append(ProtocolPageChunk(
            chunk_index=chunk_index,
            document_sha256=index.document_sha256,
            core_page_numbers=core_numbers,
            context_page_numbers=context_numbers,
            pages=pages,
        ))
        chunk_index += 1
        start = end + 1
    return chunks


def render_page_chunk(
    chunk: ProtocolPageChunk,
    *,
    max_characters: int = 100_000,
) -> str:
    """Render one chunk's pages as page-cited AI context, core pages marked.

    Every page carries an explicit CORE/CONTEXT role so the model knows which
    pages it must extract facts from (core) and which are shown only to
    interpret content that straddles the boundary (context) -- the
    neighbouring chunk owns those pages' facts, so re-emitting them here
    would double-count the same fact in the merged evidence pool.
    """

    if max_characters < 200:
        raise ValueError("max_characters must be at least 200")
    core_set = set(chunk.core_page_numbers)
    rendered: list[str] = []
    used = 0
    omitted: list[int] = []
    for page in chunk.pages:
        role = (
            "CORE — extract every schedule-relevant fact from this page"
            if page.page_number in core_set else
            "CONTEXT ONLY — do not extract facts from this page; it belongs "
            "to a neighbouring chunk, shown here only so boundary-straddling "
            "tables/rules are legible"
        )
        body = page.text.strip() or "[NO EMBEDDED TEXT — USE PDF VISION/OCR]"
        block = (
            f"[PDF page {page.page_number}; evidence_id={page.evidence_id}; "
            f"text_status={page.text_status}; {role}]\n{body}"
        )
        additional = len(block) + (2 if rendered else 0)
        if used + additional > max_characters:
            omitted.append(page.page_number)
            continue
        rendered.append(block)
        used += additional
    if omitted:
        notice = "[CONTEXT BUDGET OMITTED PAGES: " + ", ".join(map(str, omitted)) + "]"
        if used + len(notice) + 2 <= max_characters:
            rendered.append(notice)
    return "\n\n".join(rendered)


def render_page_selection(
    selection: ProtocolPageSelection,
    *,
    max_characters: int = 80_000,
) -> str:
    """Render selected pages as page-cited AI context within a hard budget.

    No page is silently cut in the middle. If the next complete page would
    exceed the budget it is omitted and the rendered context records that fact.
    """

    if max_characters < 200:
        raise ValueError("max_characters must be at least 200")
    chunks: list[str] = []
    used = 0
    omitted: list[int] = []
    for page in selection.pages:
        body = page.text.strip() or "[NO EMBEDDED TEXT — USE PDF VISION/OCR]"
        chunk = (
            f"[PDF page {page.page_number}; evidence_id={page.evidence_id}; "
            f"text_status={page.text_status}]\n{body}"
        )
        additional = len(chunk) + (2 if chunks else 0)
        if used + additional > max_characters:
            omitted.append(page.page_number)
            continue
        chunks.append(chunk)
        used += additional

    if omitted:
        notice = "[CONTEXT BUDGET OMITTED SELECTED PDF PAGES: " + ", ".join(map(str, omitted)) + "]"
        if used + len(notice) + 2 <= max_characters:
            chunks.append(notice)
    return "\n\n".join(chunks)


__all__ = [
    "INDEX_SCHEMA_VERSION",
    "IndexedProtocolPage",
    "PdfIndexingError",
    "ProtocolDocumentIndex",
    "ProtocolDocumentIndexCache",
    "ProtocolPageChunk",
    "ProtocolPageSelection",
    "RetrievedProtocolPage",
    "RetrievalTask",
    "build_protocol_document_index",
    "chunk_protocol_pages",
    "render_page_chunk",
    "render_page_selection",
    "retrieve_protocol_pages",
]
