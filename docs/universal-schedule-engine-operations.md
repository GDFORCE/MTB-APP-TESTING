# Universal Schedule Engine — Operations and Cutover

The authoritative design is in [universal-schedule-engine-architecture.md](universal-schedule-engine-architecture.md). This document covers deployment and the safe retirement of legacy visit-gap scheduling.

## Deployment

1. Provision PostgreSQL with TLS and enable `pgcrypto` (`CREATE EXTENSION IF NOT EXISTS pgcrypto`).
2. Set `UCTSM_DATABASE_URL` to a `postgresql+psycopg://` connection string. Do not place credentials in source control.
3. Apply the schema from `backend`: `alembic upgrade head`.
4. Configure the extraction worker’s provider/model/prompt versions. The API queues immutable `extraction_runs`; a worker invokes `app.extraction.graph.run_extraction` with a provider adapter and completes it through `ExtractionService.complete`.
5. Keep protocol object URIs private. The API stores locators, not public document links; document rendering must issue short-lived authorized URLs.
6. Deploy the backend and frontend. New APIs are under `/api/uctsm` and do not read legacy visit/gap collections.

The runtime is intended for Python 3.12+. The current development workstation uses Python 3.11, and the implementation remains compatible for local tests.

## Clinical workflow

1. Queue extraction with an `Idempotency-Key`.
2. Poll `/api/uctsm/extraction-runs/{id}` until the worker completes.
3. Validate the resulting schedule version.
4. Review evidence and record field decisions for event name and timing. Corrections replace a typed event on the editable version, retain before/after/reason, and reopen deterministic validation.
5. Submit and approve. Approval re-reads persisted data, reruns validation, requires field decisions, and freezes the version.
6. Pin a patient to the approved version, record required anchors/states, and evaluate with a bounded horizon and idempotency key.
7. Use the current patient schedule and patient-event explanation endpoints for UI/notifications. Record actual occurrences separately; planned dates remain reproducible.

## Cutover gate

Do not enable authoritative mode globally until each affected tenant/trial has:

- an approved UCTSM schedule;
- an explicit policy for existing-patient adoption versus legacy historical retention;
- patient anchor/state mappings;
- UI consumers switched to UCTSM projections;
- notification consumers switched to resolved patient events;
- exported and retention-checked legacy schedule history.

After those checks, set `UCTSM_AUTHORITATIVE=true`. The server then returns `410 Gone` for legacy schedule extraction, fixed-row authoring, previews, and approvals rather than silently falling back to visit-gap logic. Historical legacy reads remain available during retention. Remove those reads and drop old collections only in a later separately approved destructive release.

## Verification

For an immediate self-contained demonstration (no database or credentials):

```powershell
cd backend
python scripts/uctsm_demo.py
```

The output shows both calculated patient windows and safe waiting states.

From `backend`:

```powershell
pytest -q tests/test_uctsm_domain.py tests/test_uctsm_persistence.py tests/test_uctsm_extraction.py tests/test_uctsm_workflow.py tests/test_uctsm_notifications.py
python -m compileall -q app server.py
```

From `frontend`:

```powershell
npx tsc --noEmit
```

The repository’s full legacy test suite is not a single isolated test process: several older Mongo integration modules own and close global asyncio loops, so a monolithic `pytest -q` run can fail later modules with `RuntimeError: Event loop is closed`. Run those integration modules in their documented isolated groups. This pre-existing harness limitation does not affect the UCTSM focused suite.

## Deliberate fail-safe behavior

- `UNRESOLVED`, unsupported, approximate-without-policy, conflicting, and evidence-free timing blocks approval.
- A missing trigger/anchor yields a waiting status, not a fabricated date.
- A true recurrence termination condition without an effective timestamp yields `UNRESOLVED`; it does not silently erase occurrences.
- Recurrence always requires a count/date/event/condition or caller horizon and has a hard safety bound.
- A client cannot choose an arbitrary schedule version for evaluation; the server uses the patient’s pinned approved version.
- An amendment never edits an approved version or automatically migrates a patient.
