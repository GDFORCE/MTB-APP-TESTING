import io

import pytest
from reportlab.pdfgen import canvas

import protocol_document_index as document_index
from protocol_document_index import (
    PdfIndexingError,
    ProtocolDocumentIndexCache,
    build_protocol_document_index,
    render_page_selection,
    retrieve_protocol_pages,
)


def _index(pages: list[str]):
    return build_protocol_document_index(b"stable-pdf-content", extracted_pages=pages)


def test_build_index_is_stable_and_marks_pages_without_embedded_text():
    pages = [
        "CONFIDENTIAL PROTOCOL\nSynopsis\nA randomized crossover study",
        "CONFIDENTIAL PROTOCOL\nSchedule of Assessments\nVisit Day 1 Day 2",
        "CONFIDENTIAL PROTOCOL\n",
    ]

    first = _index(pages)
    second = _index(pages)

    assert first == second
    assert first.page_count == 3
    assert first.pages[1].page_number == 2
    assert first.pages[1].evidence_id.startswith("page-2-")
    assert first.pages[2].text_status == "image_or_empty"
    assert "confidential protocol" in first.boilerplate_lines
    assert "confidential protocol" not in first.pages[1].searchable_text
    assert first.pages[1].section_markers == ["Schedule of Assessments"]


def test_schedule_retrieval_selects_table_and_adjacent_footnote_pages():
    index = _index(
        [
            "Table of Contents\nSchedule of Assessments ........ 88",
            "Background and rationale. No operational timing is defined here.",
            "Schedule of Assessments\nScreening Day -10\nPeriod I Day 1",
            "Table continued\nPeriod II Day 1\nFollow-up Month 1\nFootnote a: if clinically indicated",
            "Statistical methods and sample size calculation.",
        ]
    )

    selection = retrieve_protocol_pages(
        index,
        task="schedule_discovery",
        max_pages=3,
        neighbour_radius=1,
    )

    assert selection.page_numbers == [2, 3, 4]
    assert selection.pages[1].score > selection.pages[0].score
    assert "relevance seed" in selection.pages[1].reasons
    assert any("adjacent to relevant page 3" in reason for reason in selection.pages[2].reasons)
    assert selection.omitted_page_count == 2


def test_task_profiles_retrieve_timing_and_activity_evidence():
    index = _index(
        [
            "Narrative introduction to the investigational product.",
            "Timing rules\nVisit window ±3 days. Washout at least 21 days. Cycle length 28 days.",
            "Procedures\nHematology, pharmacokinetic blood sample, vital signs and ECG assessments.",
        ]
    )

    timing = retrieve_protocol_pages(index, task="timing", max_pages=1, neighbour_radius=0)
    activities = retrieve_protocol_pages(index, task="activities", max_pages=1, neighbour_radius=0)

    assert timing.page_numbers == [2]
    assert activities.page_numbers == [3]
    assert any("visit window" in reason for reason in timing.pages[0].reasons)
    assert any("pharmacokinetic" in reason for reason in activities.pages[0].reasons)


def test_classification_always_keeps_opening_context_then_adds_schedule_pages():
    index = _index(
        [
            "Protocol Amendment 2\nProtocol synopsis\nTwo-arm phase III study",
            "Objectives and endpoints",
            "Safety reporting",
            "Schedule of Events\nScreening Cycle 1 Day 1 Follow-up",
        ]
    )

    selection = retrieve_protocol_pages(
        index,
        task="classification",
        max_pages=4,
        neighbour_radius=0,
    )

    assert selection.page_numbers == [1, 2, 3, 4]
    assert "document opening/synopsis context" in selection.pages[0].reasons


def test_retrieval_falls_back_and_exposes_scanned_pages_instead_of_guessing():
    index = _index(["", "   ", "Unrelated short text"])

    selection = retrieve_protocol_pages(
        index,
        task="schedule_discovery",
        max_pages=2,
        neighbour_radius=0,
    )

    assert selection.page_numbers == [1, 2]
    assert selection.image_or_empty_pages == [1, 2, 3]
    assert any("vision/OCR" in warning for warning in selection.retrieval_warnings)
    rendered = render_page_selection(selection)
    assert "[NO EMBEDDED TEXT — USE PDF VISION/OCR]" in rendered


def test_render_context_preserves_page_citations_and_never_slices_a_page():
    index = _index(
        [
            "Schedule of Events\n" + "A" * 220,
            "Schedule of Assessments\n" + "B" * 220,
        ]
    )
    selection = retrieve_protocol_pages(
        index,
        task="review",
        max_pages=2,
        neighbour_radius=0,
    )

    rendered = render_page_selection(selection, max_characters=420)

    assert "[PDF page 1; evidence_id=page-1-" in rendered
    assert "A" * 220 in rendered
    assert "B" * 220 not in rendered
    assert "CONTEXT BUDGET OMITTED SELECTED PDF PAGES: 2" in rendered


def test_content_addressed_cache_prevents_reextracting_the_pdf(tmp_path, monkeypatch):
    calls = []

    def fake_extract(data: bytes):
        calls.append(data)
        return ["Schedule of Events\nScreening Day -14 Baseline Day 1"]

    monkeypatch.setattr(document_index, "_extract_pdf_text_pages", fake_extract)
    cache = ProtocolDocumentIndexCache(tmp_path)

    first = cache.get_or_build(b"one-pdf")
    second = cache.get_or_build(b"one-pdf")

    assert first == second
    assert calls == [b"one-pdf"]
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_corrupt_cache_is_ignored_and_rebuilt(tmp_path, monkeypatch):
    cache = ProtocolDocumentIndexCache(tmp_path)
    digest = document_index.hashlib.sha256(b"one-pdf").hexdigest()
    tmp_path.mkdir(exist_ok=True)
    cache._path(digest).write_text("not json", encoding="utf-8")
    monkeypatch.setattr(
        document_index,
        "_extract_pdf_text_pages",
        lambda _data: ["Visit Schedule\nDay 1 and Day 28"],
    )

    rebuilt = cache.get_or_build(b"one-pdf")

    assert rebuilt.page_count == 1
    assert cache.load(b"one-pdf") == rebuilt


def test_pypdfium2_boundary_extracts_real_pdf_pages():
    output = io.BytesIO()
    pdf = canvas.Canvas(output)
    pdf.drawString(72, 760, "Protocol synopsis and study design")
    pdf.showPage()
    pdf.drawString(72, 760, "Schedule of Assessments - Screening Day -14")
    pdf.save()

    index = build_protocol_document_index(output.getvalue())

    assert index.page_count == 2
    assert "Protocol synopsis" in index.pages[0].text
    assert "Schedule of Assessments" in index.pages[1].text
    assert retrieve_protocol_pages(
        index,
        task="schedule_discovery",
        max_pages=1,
        neighbour_radius=0,
    ).page_numbers == [2]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_pages": 0}, "max_pages"),
        ({"neighbour_radius": -1}, "neighbour_radius"),
    ],
)
def test_invalid_retrieval_limits_are_rejected(kwargs, message):
    with pytest.raises(ValueError, match=message):
        retrieve_protocol_pages(_index(["Schedule of Events"]), **kwargs)


def test_empty_pdf_and_empty_extracted_page_list_are_rejected():
    with pytest.raises(PdfIndexingError, match="empty"):
        document_index._extract_pdf_text_pages(b"")
    with pytest.raises(PdfIndexingError, match="no pages"):
        build_protocol_document_index(b"fake", extracted_pages=[])

