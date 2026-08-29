# Protocol page-index and retrieval integration

`backend/protocol_document_index.py` is the deterministic source-retrieval
layer for protocol extraction. It extracts embedded text with `pypdfium2` once,
indexes it by the SHA-256 of the original PDF, and returns page-numbered context
for each AI task.

## Request flow

```python
from pathlib import Path

from protocol_document_index import (
    ProtocolDocumentIndexCache,
    render_page_selection,
    retrieve_protocol_pages,
)

cache = ProtocolDocumentIndexCache(
    Path(__file__).parent / ".protocol-page-index-cache"
)

# One PDF text extraction (or a cache hit) for the complete request.
index = await asyncio.to_thread(cache.get_or_build, pdf_bytes)

classification_context = render_page_selection(
    retrieve_protocol_pages(
        index,
        task="classification",
        max_pages=14,
        neighbour_radius=1,
    ),
    max_characters=45_000,
)

schedule_context = render_page_selection(
    retrieve_protocol_pages(
        index,
        task="schedule_discovery",
        max_pages=24,
        neighbour_radius=1,
    ),
    max_characters=80_000,
)

timing_context = render_page_selection(
    retrieve_protocol_pages(
        index,
        task="timing",
        max_pages=24,
        neighbour_radius=1,
    ),
    max_characters=80_000,
)

activity_context = render_page_selection(
    retrieve_protocol_pages(
        index,
        task="activities",
        max_pages=24,
        neighbour_radius=1,
    ),
    max_characters=80_000,
)
```

Create the index before entering the agent graph and keep it in the graph state.
Each stage receives its focused text context and the `document_sha256`; it does
not upload the complete PDF again. The reviewer should use a `review` selection
from the same index, not the builder's generated answer as its only evidence.

## Evidence contract

Every rendered page starts with a stable citation such as:

```text
[PDF page 88; evidence_id=page-88-a3e14f9c8201; text_status=text]
```

AI outputs should store that `evidence_id` and a supporting quote/span for each
extracted field. Deterministic validation must reject an evidence ID that is not
present in the index. PDF page numbers remain one-based and refer to physical
PDF pages, not printed footer numbers.

## Scanned and mixed PDFs

`image_or_empty_pages` and `retrieval_warnings` must not be discarded. A page
with no usable embedded text requires the existing PDF-vision route or OCR. Do
not convert absence of embedded text into “not stated.” For a mixed PDF, use
text retrieval for searchable pages and render only the selected image pages
for the vision model.

## Caching and lifecycle

- Use a private, non-public cache directory configured by the application.
- Cache keys are the PDF content hash plus the index schema version.
- A corrupt or obsolete cache entry is ignored and rebuilt.
- Writes are atomic, so concurrent uploads of the same document converge safely.
- Apply the same retention/deletion policy as the source protocol because page
  text contains protocol content.
- Keep the index through builder, confirmer, reviewer, and repair stages. Do not
  rebuild it for each stage.

## Suggested agent routing

| Classified document/task | Contexts to use |
|---|---|
| Full protocol | discovery + timing + activities |
| Schedule-only document | discovery + timing + activities |
| Amendment | classification + discovery, then compare with the indexed base version |
| Synopsis | classification + discovery; flag completeness as uncertain |
| Unrelated/no schedule | classification only; stop schedule synthesis |

The page retriever decides only *where evidence is likely to be*. The AI
classifier decides the workflow, the builder forms the canonical schedule, and
the validator checks citations and temporal consistency.

## Migration sequence

1. Add the index and focused contexts to extraction request/graph state.
2. Change classifier/discovery/timing/activity prompts from full-PDF input to the
   corresponding rendered selection.
3. Keep the original PDF available only for selected scanned pages and an
   explicit low-confidence fallback.
4. Store `document_sha256`, selected page numbers, and evidence IDs with the
   extraction audit record.
5. Instrument cache hit rate, pages sent per stage, scanned-page rate, retrieval
   warnings, provider retries, and citation-validation failures.
6. Evaluate retrieval recall against the manually verified schedules for all 11
   protocols before lowering the maximum page limits.

