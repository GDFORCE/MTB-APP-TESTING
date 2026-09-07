# My Trial Board — System Dossier

**System:** My Trial Board (MTB)
**Scope:** the whole application — architecture, roles, every screen, the core flows — plus a stage-by-stage account of what happens to a protocol PDF from the moment it is uploaded.
**Report date:** 31 August 2026
**Branch:** `universal-schedule-engine`
**Primary implementation:** `backend/server.py`, `backend/protocol_agent.py`, `backend/protocol_extraction.py`, `backend/app/`, `frontend/app/`

> Counts and behaviour in this document were read from the source at time of writing. Where it states a limit or a rule, that is a rule which exists in code rather than in intent.
>
> Companion documents: `docs/protocol-extraction-current-approach-and-case-handling.md` (deeper on §6), `docs/universal-schedule-engine-architecture.md` (deeper on §8), `docs/protocol-processing-and-schedule-formation-report.md` (background; its §7.6–7.11 describe a superseded architecture).

---

## Contents

| § | Section |
|---|---|
| 1 | [What the system is](#1-what-the-system-is) |
| 2 | [Architecture](#2-architecture) |
| 3 | [Roles and tenancy](#3-roles-and-tenancy) |
| 4 | [Every screen](#4-every-screen) |
| 5 | [The core flows](#5-the-core-flows) |
| 6 | [**Protocol extraction, end to end**](#6-protocol-extraction-end-to-end) |
| 7 | [From approved schedule to a patient's dates](#7-from-approved-schedule-to-a-patients-dates) |
| 8 | [The two schedule engines](#8-the-two-schedule-engines) |
| 9 | [Changes in this working session](#9-changes-in-this-working-session) |
| 10 | [Open gaps](#10-open-gaps) |
| — | [Source map](#source-map) |

At a glance:

| Metric | Count |
|---|---:|
| Screens (route files) | 95 |
| API endpoints | 262 |
| MongoDB collections | 42 |
| PostgreSQL tables (UCTSM) | 29 |
| Backend test files | 49 |
| `backend/server.py` | 9,833 lines |

---

## 1. What the system is

A clinical trial runs on a **schedule of assessments**: a table in the protocol saying which visit happens on which study day, with what tolerance, and what is done at it. Everything in MTB radiates out from getting that table right.

A trial protocol arrives as a PDF — often several hundred pages, with the schedule printed as a wide table on one page and the rules that govern it scattered through prose on others. A sponsor uploads it. The system reads it, reconstructs the schedule, and hands it to a human to check. Once a Principal Investigator approves it, sites enrol patients against it, and each patient gets their own dated copy of that schedule anchored to their own baseline date.

The chain is short and every link is load-bearing:

```
protocol PDF
    -> AI extraction
    -> draft schedule
    -> human review
    -> approved visit templates
    -> patient enrolment
    -> dated visit instances
    -> reminders, tasks, adherence
```

Everything else in the application supports that chain or the people working along it: authentication and organisation management so the right people see the right trials, messaging so a coordinator can reach a patient, an audit trail because this is regulated work, and a platform-admin portal for the operators who run the service.

> **The rule the whole system is built around:** nothing the AI produces reaches a patient without a person approving it. Extraction always returns a draft. Uncertainty resolves to "ask a human", never to a confident guess.

---

## 2. Architecture

### 2.1 The client

A single **Expo / React Native** app served through Expo Router, so the filesystem is the route table. `app/(auth)/` holds everything a signed-out person can reach; `app/(app)/` holds everything else.

`app/_layout.tsx` mounts a `RouterGuard` that watches auth state and redirects: signed out to the welcome screen, signed in to the dashboard for that role. There is no separate build per role — role is a runtime branch.

Design is centralised in `frontend/src/theme/tokens.ts` — the "Dawn Rounds" palette: cream-blush paper (`#FBF2E8`), deep plum ink (`#2E1B33`), raspberry-rose primary (`#A6213F`), an apricot-to-rose dawn gradient, with Bricolage Grotesque, Figtree and Spline Sans Mono as the three type roles.

### 2.2 The server

**FastAPI.** The bulk lives in one large module, `backend/server.py` (9,833 lines, 149 routes), with two role-gated routers bolted on at the bottom:

| Router | Routes | Audience |
|---|---:|---|
| `backend/server.py` | 149 | Everyone |
| `backend/admin_routes.py` | 72 | Platform operators |
| `backend/org_routes.py` | 19 | Organisation admins |
| `backend/app/api/uctsm.py` | 22 | The universal schedule engine |

Every route carries its own role gate through `require_roles(...)`, and access to a trial or patient is re-checked per request against the caller's organisation and relationships — never assumed from the role alone.

### 2.3 Two databases, on purpose

| Store | Holds | Why |
|---|---|---|
| **MongoDB** | Users, organisations, sites, trials, visit templates, patients, visit instances, messaging, notifications, files, audit logs — 42 collections | The operational system of record. Everything the app does today runs on it. |
| **PostgreSQL** (UCTSM) | The universal schedule engine: 29 tables covering trials, protocol versions, schedule versions, evidence, events, anchors, patient evaluations | A stricter, immutable, evidence-linked model of a schedule with a hard approval gate. Opened only when an `/api/uctsm` route is called. |

The two are deliberately isolated — the SQL domain may not read legacy visit rows, and the legacy path does not depend on Postgres being up. §8 covers how they relate today.

### 2.4 The AI layer

Extraction is provider-abstracted behind a `ProtocolExtractor` interface, with four backends:

| Provider | Status | Runs the full graph? |
|---|---|---|
| **Gemini** | Live (`PROTOCOL_EXTRACTION_PROVIDER=gemini`) | Yes |
| Claude | Available | No — single-shot |
| OpenRouter / DeepSeek | Available | No — single-shot |
| Ollama (local) | Offline development | No — single-shot |

The local Ollama path renders two PDF pages at a time and checkpoints by SHA-256, so a 250-page scan can be resumed without separate OCR or loading the whole document into model memory. Processing is sequential and can take hours on a CPU-only machine.

The pipeline itself is a **LangGraph** state machine — §6 is entirely about it.

---

## 3. Roles and tenancy

Eight roles, each landing on its own dashboard. Who you are decides what you see; which organisation you belong to decides which trials exist for you at all.

`Role = Literal['sponsor', 'cro', 'smo', 'site', 'pi', 'crc', 'patient', 'admin']`

| Role | Who they are | What they do here | Lands on |
|---|---|---|---|
| `sponsor` | The company running the trial | Create trials, upload protocols, author and share the visit schedule, manage sites, watch de-identified enrolment | `/sponsor/dashboard` |
| `cro` | Contract research org acting for a sponsor | Same surface as sponsor | `/sponsor/dashboard` |
| `pi` | Principal Investigator at a site | Approve or reject the schedule, own patients, run visits, sign off tasks | `/pi/dashboard` |
| `crc` | Clinical Research Coordinator | Day-to-day patient work: enrol, schedule, update visits, chase overdue | `/crc/dashboard` |
| `smo` | Site Management Organisation | Oversees several sites and their PIs | `/pi/dashboard` |
| `site` | A hospital / research site account | Site-level oversight of its own trials and staff | `/site/dashboard` |
| `patient` | The trial participant | See their own visits, medications, trial info, message their site | `/patient/dashboard` |
| `admin` | Platform operator | Runs the service: users, orgs, tickets, alerts, audit, terms | `/admin` |

### 3.1 How access is actually decided

Role is necessary but never sufficient.

A PI may only touch a trial through one of three real ties, checked **fail-closed** on every request:

1. They created it (`created_by`), or
2. their organisation matches the trial's sponsor organisation — the pre-enrolment approval path, valid even for an unclaimed trial, or
3. they are a listed PI on it (they own a patient enrolled in it).

There is no "unclaimed trial is open to any PI" path. Patient access is scoped the same way — a coordinator reaching a patient outside their scope gets a 403, never a silent read.

### 3.2 The organisation layer

Above individual users sits an organisation layer: organisations have sites, members, an owner, and an **org admin** flag. Org admins get their own portal to invite members, assign them to sites, transfer ownership, review trial-access requests and read their own audit trail. Sponsors grant a site access to a trial; sites can request it.

---

## 4. Every screen

All 95 route files, grouped the way the app groups them. Line counts are a rough proxy for how much lives in each.

### 4.1 Getting in — `app/(auth)/`

Registration is phone-first and multi-step: pick an entity type, provide details, verify by OTP on phone or email, set a password, set security questions. Recovery has three routes — password reset, security questions, and a human support path for people locked out entirely.

| Route | Purpose | Lines |
|---|---|---:|
| `welcome` | First screen — sign in or start registration | 183 |
| `sign-in` | Phone/email plus password | 106 |
| `entity-type` | Are you a sponsor, a site, a patient — branches the whole signup | 93 |
| `register` | The main registration form: identity, organisation lookup, hospital place search, role details | 1190 |
| `verify-phone` | Phone OTP step | 45 |
| `verify-email` | Email OTP step | 42 |
| `set-password` | Password creation with OS autofill support | 214 |
| `security-questions` | Recovery questions captured at signup | 180 |
| `register-success` | Confirmation and next step | 150 |
| `join-invite` | Accept an invitation by code or link | 210 |
| `forgot-password` | Start recovery by phone or email | 378 |
| `reset-password` | Set a new password from a verified token | 233 |
| `login-support` | Pre-login support request for people who cannot get in | 310 |
| `help-support` | Public help and contact routes | 269 |

### 4.2 Shared across roles

| Route | Purpose | Lines |
|---|---|---:|
| `dashboard` | Generic role dashboard — hero, stats, next visit, notifications; patients get a progress ring | 298 |
| `chat` | Full messaging surface: conversations, recipients, files, flags, invite links | 1156 |
| `conversation/[id]` | One thread, with members and settings | 585 |
| `conversation/[id]/files` | Files shared in a thread | 117 |
| `conversations/join/[token]` | Join a conversation from a link | 56 |
| `notifications` | Notification list; routes each kind to its screen | 69 |
| `audit-trail` | Audit events visible to the current user | 241 |
| `ownership-transfer` | Accept or decline transfer of an organisation | 315 |
| `data-policy` | Data handling statement | 59 |
| `no-internet`, `session-timeout` | Full-screen takeovers for lost connectivity and expired sessions | 45 |

### 4.3 Sponsor and CRO — `app/(app)/sponsor/`

| Route | Purpose | Lines |
|---|---|---:|
| `dashboard` | Portfolio view: trials, de-identified enrolment, site performance, recruitment | 870 |
| `trials` | Trial list with phase and status filters | 337 |
| `add-trial` | Create a trial. Uploading the protocol PDF here runs extraction once and pre-fills both the trial details and the schedule | 646 |
| `visit-schedule` | **The schedule editor** — the largest screen in the app. Table, filters, per-visit detail sheet, drag-reorder, add/delete, CSV export, multi-substudy cards | 2160 |
| `share-schedule` | Send the schedule to sites/PIs for review; generates the review tasks and a shareable PDF link | 893 |
| `sites` | Trial site management, bulk import, site directory | 1222 |
| `patients` | De-identified subject list across the sponsor's trials | 305 |
| `principal-investigators` | PI directory and lookup | 233 |
| `notifications` | Sponsor-scoped notification feed | 219 |
| `profile` | Sponsor account and organisation profile | 347 |
| `trial-detail` | Thin route into the shared trial summary | 35 |

### 4.4 Site staff — `app/(app)/clinical/`, `/pi/`, `/crc/`

| Route | Purpose | Lines |
|---|---|---:|
| `pi/dashboard` | PI action queue: overdue visits, visits due today, schedules awaiting review, unread messages | 834 |
| `crc/dashboard` | Coordinator equivalent, same computed payload | 896 |
| `clinical/patients` | Patient list with next-visit and status per patient | 340 |
| `clinical/visit-detail` | **The patient record**: demographics, current visit, remarks, the full visit timeline, visit history, and the update sheet with tasks and comments | 926 |
| `clinical/add-patient` | Enrol a patient — identity, PI/CRC assignment, substudy and arm pickers, baseline date, and a live preview of the dates that baseline produces | 845 |
| `clinical/invite-patient` | Invite a patient to register before enrolling them | 94 |
| `clinical/schedule-review` | The PI's approve / reject surface for a shared schedule, with the visit list and change diff | 757 |
| `clinical/trial-summary` | Trial overview for site staff: subjects, visits, documents, team | 1245 |
| `clinical/team` | Site team roster | 549 |
| `clinical/team-member` | One team member's detail and permissions | 447 |
| `clinical/invite-member` | Invite a colleague to the site team | 353 |
| `clinical/team-calendar` | Site-wide visit calendar across patients | 537 |
| `clinical/calendar-settings` | Working hours and calendar preferences | 283 |
| `clinical/profile/[slug]` | Staff profile with role-specific sections | 1294 |
| `clinical/my-trials` | Trials this staff member is attached to | 79 |
| `clinical/patient-schedule` | Route into the newer UCTSM patient schedule view — **currently unreachable**, nothing navigates to it | 1 |

### 4.5 Patient — `app/(app)/patient/`

| Route | Purpose | Lines |
|---|---|---:|
| `dashboard` | Next visit, progress, reminders, quick links | 734 |
| `my-trial` | The patient's trial at a glance, with medication and dose history | 571 |
| `my-visits` | The visit timeline with status, timing and dates | 169 |
| `visit-detail` | One visit: what happens, where, its checklist and care team | 214 |
| `calendar` | Month calendar of visits with window bands | 518 |
| `about-trial` | Plain-language trial information and documents | 734 |
| `profile` | Patient account, contact details, consent and preferences | 1217 |
| `medication-reminder` | Medication reminder entry point | 24 |
| `messages` | Route into the shared chat surface | 3 |

### 4.6 Organisation admin — `app/(app)/org-admin/`

| Route | Purpose | Lines |
|---|---|---:|
| `sponsor` | Sponsor organisation administration | 289 |
| `smo` | SMO administration across its sites | 554 |
| `site` | Site administration: members, trials, visits | 391 |
| `trial-access-requests` | Approve or decline requests for trial access | 394 |

### 4.7 Platform admin — `app/(app)/admin/`

A full operator portal behind a role guard in its own layout. Seventeen screens covering the running of the service rather than the running of a trial.

| Route | Purpose | Lines |
|---|---|---:|
| `index` | Operations overview | 398 |
| `users` | User administration: status, unlock, password reset, force logout, export | 715 |
| `organizations` | Org directory, duplicate detection, merges, name-change requests | 846 |
| `delegation` | Delegation of authority between org roles | 808 |
| `emergency-access` | Break-glass access with its own trail | 654 |
| `invitations` | Every outstanding invitation, resend and cancel | 592 |
| `terms` | Terms versions and acceptance tracking | 580 |
| `messages` | Broadcast messages to user segments | 496 |
| `audit-logs` | Platform-wide audit search | 426 |
| `trials` | All trials on the platform | 415 |
| `master-data` | Controlled vocabularies and submission approvals | 399 |
| `notification-monitoring` | Delivery stats, logs, retry | 394 |
| `reports` | Generated reports and downloads | 369 |
| `tickets` | Support tickets with internal notes | 368 |
| `alerts` | System alerts: retry, escalate, resolve, notify | 321 |
| `profile` | Admin account | 457 |

---

## 5. The core flows

Five journeys carry almost all the value in the product. Everything else is support.

### 5.1 Creating a trial

A sponsor opens `add-trial` and either types the details or drops the protocol PDF.

On a PDF, the client calls `POST /protocols/extract`, which runs **one** analysis and returns two things: the trial metadata to pre-fill the form, and a fully audited schedule stashed server-side under an extraction ID with a **two-hour** expiry.

The sponsor confirms the details, the trial is created, and the app navigates to the schedule editor carrying that extraction ID — where the prepared schedule is *consumed* rather than re-analysed. The PDF is never uploaded twice and the AI is never paid for twice.

### 5.2 Authoring the schedule

`sponsor/visit-schedule` is the editor. It shows the extracted rows as a table — number, visit name, day, window — with a summary strip (`AI Extracted` · `N visits` · `Agent verified`) and, when anything needs attention, an `N need review` pill. Three filter chips isolate the work: **All / Pending / OK**.

Tapping a row opens a detail sheet with the exact protocol timing text alongside the computed placement, plus activities, procedures, clinical and admin tasks, operational constraints and comments. In edit mode rows can be added, deleted and dragged into order.

Saving diffs against what is already stored and issues only the writes that changed. If any row is still pending, saving asks for confirmation first. When a protocol prints more than one independent schedule of assessments, each becomes its own collapsible card and is saved separately with a substudy label.

### 5.3 Sharing, review and approval

The sponsor sends the schedule to sites via `POST /shares`. That creates a share record with a token, a public PDF link, and one **schedule review** task per reviewer, plus a notification.

The PI opens `clinical/schedule-review`, reads the visit list and — for an amended schedule — a per-visit diff of what changed, then approves or rejects with a reason. The decision notifies the trial's sponsors and updates the trial's schedule status.

### 5.4 Enrolling a patient

Two paths:

- **Invite first.** `POST /patients/invite` sends an invitation carrying the patient's data. It expires in three days (`INVITE_TTL_DAYS = 3`); enrolment happens only once they accept and complete registration.
- **Enrol directly.** `POST /patients` on the `add-patient` screen.

Either way the form captures a **baseline date**, which is the anchor the whole schedule hangs from, and shows a live preview of the resulting dates before anything is saved. Subject IDs are checked for duplicates against the trial both optimistically in the client and authoritatively in the database.

On enrolment, `materialize_visit_instances` copies every applicable visit template into a **per-patient visit instance** with its own dates, window, status, task list and comment thread. Applicability is filtered by the patient's substudy and arm, so a patient in substudy B never receives substudy A's visits. From that moment the patient's schedule is theirs — editing it never touches the template or any other patient.

### 5.5 Running the visits

Visit status is **derived on read**, not stored as a countdown. A stored instance carries only `planned` or a terminal state, and `_effective_visit_status` turns that into `planned` / `due` / `overdue` by comparing the window against now. That derivation is applied identically by the patient list, the task queue and the dashboards, so no two screens can disagree about whether a visit is late.

Site staff work the visit from the patient record: mark complete, change the date (which recalculates the window and records what it was rescheduled from), tick clinical and admin tasks, add attributed comments, or set a terminal outcome — screen fail, withdrawn, drop out.

`GET /tasks` rolls all of this into the PI/CRC action queue: overdue visits, visits due today, schedules awaiting review, unread messages — computed on every read, nothing cached.

Patients see the same schedule from their side, plus medication reminders and dose logging, which feed an adherence calculation available to them and to their site staff (`GET /adherence`).

---

## 6. Protocol extraction, end to end

A sponsor selects a PDF. Somewhere between one and forty minutes later a table of visits appears on screen. This section is everything that happens in between — the stages, what each one is allowed to conclude, and every point where the system chooses to say "I don't know" instead of guessing.

**The constants that bound it:**

| Constant | Value | Where |
|---|---|---|
| Max PDF size | 25 MB | `MAX_PDF_BYTES` |
| Evidence chunk core / overlap | 22 / 4 pages | `PROTOCOL_EVIDENCE_CHUNK_CORE_PAGES`, `..._OVERLAP_PAGES` |
| Audit dimensions | 6 | `ScheduleAudit` |
| Minimum accepted confidence | 0.75 | `PROTOCOL_EXTRACTION_MIN_CONFIDENCE` |
| Max repair passes | 2 | `PROTOCOL_EXTRACTION_MAX_REFINEMENTS` |
| Open-ended cycle preview cap | 12 | `OPEN_ENDED_CYCLE_CAP` |
| Max expanded visits | 400 | `MAX_EXPANDED_VISITS` |
| Prepared-extraction cache | 2 hours | `protocol_extractions.expires_at` |

### 6.1 Two doors, one pipeline

A protocol can enter from two places, and they behave differently on purpose.

| Entry | Endpoint | What comes back |
|---|---|---|
| **Add Trial** | `POST /protocols/extract` | Trial metadata *and* the audited schedule. The schedule is written to `protocol_extractions` with a 2-hour expiry and returned as an **extraction ID**. Expired rows are swept on every call. |
| **Visit Schedule** | `POST /trials/{id}/extract-schedule` | The schedule only, returned inline in the editor's row format and persisted as a draft schedule definition. |
| **Consume** | `POST /trials/{id}/protocol-extractions/{eid}/consume` | Hands back the schedule prepared during Add Trial. **No AI call.** This is what makes the two-screen flow cost one extraction. |

There is also `POST /protocols/extract-details` — a lighter call that reads only the trial metadata, used when the sponsor wants the form filled but not the schedule.

### 6.2 The gate at the door

Before a single token is spent, the upload is checked deterministically:

- content type must be PDF (or the filename must end `.pdf`),
- the body must be non-empty,
- it must be under 25 MB — the reader is **capped**, so an oversized upload is refused rather than streamed into memory,
- and for the schedule route the first five bytes must literally be `%PDF-`. A renamed file is rejected here rather than confusing a model later.

Failures are separated by cause, because they need different responses from the operator:

| Status | Meaning |
|---|---|
| `400` | The document is wrong — not a PDF, empty, or corrupt. |
| `503` | Extraction is **not configured** (no provider key), or the provider is refusing work — quota, billing, overload. The error says explicitly that the document was not the problem, so nobody wastes an afternoon re-uploading a perfectly good protocol. |
| `502` | The pipeline ran and genuinely failed to produce a schedule. |

### 6.3 The graph

Extraction is not one prompt. It is a compiled **LangGraph** state machine with nine nodes and three exits, defined in `backend/protocol_agent.py:1868-1898`. State carries the PDF bytes, the classification, the evidence pools, the candidate schedule, the audit, a refinement counter and a per-stage checkpoint.

```
START
  │
  ▼
classify ──────────────────────► needs_selection ──┐   (many independent schedules)
  │                                                 │
  ▼                                                 │
discover ──────────────────────► no_schedule ──────┤   (no schedule in this document)
  │                                                 │
  ▼                                                 │
evidence_sweep      full document, chunked, parallel│
  │                                                 │
  ▼                                                 │
synthesize          one call authors the graph      │
  │                                                 │
  ▼                                                 │
audit ◄─────────────────┐  6 scored dimensions      │
  │                     │  + 3 deterministic checks │
  ├── accepted AND no   │                           │
  │   deterministic ────┼──────────────────────────►├──► finalize ──► END
  │   issues            │                           │
  ├── refinement_count  │                           │
  │   >= 2 ─────────────┼──────────────────────────►┘
  │                     │
  └── otherwise ──► refine
                    (repair prompt, re-enters audit)
```

Nodes: `classify`, `needs_selection`, `discover`, `evidence_sweep`, `no_schedule`, `synthesize`, `audit`, `refine`, `finalize`.

Every stage runs through a shared `run_stage` helper that gives it three retries with backoff for transient provider failures, and a **checkpoint**: a completed stage's output is stored as plain JSON, so a resumed run never pays for work already done, and a malformed checkpoint is discarded rather than trusted.

`classify` and `discover` also receive a keyword-scored page excerpt as a locating aid — their job is finding *where* things are, a lower-stakes task. `synthesize`, `audit` and `refine` do not: they rely on the full evidence pool plus the provider's own PDF attachment, since a scored excerpt on top of a full sweep would be redundant cost with no coverage benefit.

### 6.4 The evidence sweep — the single most important change

This is where the system stopped losing facts.

**Before:** two single-shot stages each worked from a keyword-scored ~24-page excerpt. A fact stated only in prose, on a page that never matched the scorer's vocabulary — a dose-modification rule, a rule mentioned once outside any table — could be silently missed.

**Now:** `chunk_protocol_pages` (`protocol_document_index.py`) partitions **every page** of the PDF into fixed chunks — 22 core pages each, with 4 pages of overlap on either side so a table or governing rule that straddles a boundary is still legible. One AI call runs per chunk, in parallel, and each chunk is responsible only for its own core pages, so a page belongs to exactly one chunk and no fact is claimed twice. Evidence IDs are namespaced per chunk (`chunk0-…`, `chunk1-…`) before merging into the same evidence shapes the rest of the pipeline already expects.

> Coverage became a **structural guarantee** — every page belongs to exactly one chunk — instead of a scoring outcome. A rule mentioned once on an odd page is now read by construction rather than found by luck.

The sweep prompt carries instructions earned from real failure modes:

- **Population-gated activities** keep their gate verbatim. "EQ-5D only for US, Germany and France subjects" must not flatten to "some subjects".
- **Multi-day confinement blocks** — an inpatient PK/BA-BE stay with a check-in day, pre-dose-only housing days, a dosing day and a discharge day — are captured as one fact per differentiated day, not compressed into a date range.
- **Conditional and triggered visits** — "repeat if ANC < 1000", "extend by one week if toxicity persists" — are real facts even though no fixed offset can be computed. The trigger is preserved word for word.

### 6.5 Synthesis — declare structure, don't flatten it

One AI call authors the canonical schedule graph from the evidence pool. The critical design decision is *what it is asked to emit*.

Real protocols collapse repetition. A schedule of assessments prints "Cycle 2 & Next Cycles" or "every 8th week thereafter", and the numbers needed to expand that — cycle length, cycle count, intra-cycle spacing — live in prose on other pages entirely. In one real protocol the table is on page 42, the cycle length on page 15 and the expansion rule on page 24.

Asking a model for an already-flattened list is asking it to do multi-page arithmetic in its head, silently, with no way to check the result. So the model emits the **structure it read** — repeating blocks with a cycle length and a member layout, relative anchors, conditional activities — and deterministic Python does the arithmetic afterwards, where it is testable without an API key.

**The timing shape rule**, applied to every timing value in the schedule:

> Choose the kind from what the source actually supplies. Procedure prose with no number and no anchor — "pre-dose", "at each visit", "as clinically indicated" — must use kind `unresolved` with the exact wording preserved. Never label such a value *offset* or *relative* and leave its companion field empty.

That is not only a prompt instruction. It is enforced structurally:

- A `WindowSpec` asserted as `stated` with no actual magnitude is **automatically downgraded to `unclear`** by a Pydantic validator. The system cannot accept "there is a window, trust me" with no number.
- Every fact must cite a real `evidence_id` from the sweep's own catalogue. Nothing can be asserted with no source.
- Where the protocol says two contradictory things, the disagreement is recorded as a `ScheduleConflict` with status `unresolved` and shown to the reviewer, rather than being quietly decided.

### 6.6 The audit gate

The draft is then attacked from two directions at once: three code-only checks that cannot hallucinate, and one AI audit scored across six independent dimensions.

#### The deterministic checks

| Check | What it catches |
|---|---|
| `_validate_evidence_links()` | Every timing, window, activity, arm, period and canonical-graph field must cite a real evidence ID from the sweep's catalogue, in the right category, above the confidence threshold. Unsupported or wrongly-typed citations are flagged. |
| `_structural_issues()` | Malformed graph structure — `schedule_kind="none"` but visits were produced (or the reverse), duplicate compiled visit rows, and anything `expand_schedule()`'s own validation already flags. |
| `_visit_coverage_issues()` | Compares the final visit count against the number of distinct `visit_columns` facts the sweep inventoried. This is the deterministic catch for the single most common failure mode — a wide, plainly-numbered table (Week 4, 8, 12, 16…) silently collapsed to a handful of "representative" visits. |

Those results are handed to the audit prompt labelled explicitly as **"confirmed real defects, not a hypothesis"**. The audit model is not trusted to catch dropped visit columns on its own, because it is never shown the full column inventory.

This replaced an earlier design in which a *second* AI-authored schedule was generated and diffed against the first. That diff was a noisy signal — "two independently generated schedules phrased things differently" — and weaker evidence than a mechanically computed structural defect. Net effect: one fewer full-schedule generation per extraction, and the failure mode it used to catch is now caught more reliably.

#### The six dimensions

Acceptance is a **computed property**, not a yes/no from the model:

```python
accepted = (
    approved
    and all(dimension.accepted for dimension in [
        visit_coverage, timing, windows, visit_types,
        procedure_mapping, overall_schedule])
    and not any(issue.severity in ("critical", "major") for issue in issues)
)
```

Each individual dimension's own `accepted` requires `passed=True` **and** `accuracy >= 0.75`. One weak dimension — say the model is confident about visit coverage but unsure about windows — is enough to fail the whole gate. There is no dimension that can be skipped or averaged away; a *not applicable* dimension (nothing of that kind in this protocol) auto-passes, everything else has to clear the bar on its own.

### 6.7 Refine, and the way out

If the audit does not accept, or any deterministic issue was found, `refine` receives the candidate schedule, the deterministic issues, the full audit output and the evidence, and produces a corrected draft that re-enters the audit. This repeats until the gate clears or the repair budget — default **two** passes — is exhausted. There is no unbounded retry.

`finalize` then sets the verification status. It is `verified` only if **all three** hold:

1. the audit was accepted, **and**
2. there are zero deterministic issues, **and**
3. deterministic expansion itself did not require review.

Any single failure means `needs_review`. Every unresolved critical or major finding, and every stage warning, is appended to the schedule's `assumptions` list so it travels with the draft to the reviewer.

> **What "verified" actually means:** the builder, the deterministic checks and the audit all agreed with each other under the current evidence. It is a self-consistency claim, not regulatory sign-off, and never means a person has confirmed anything.

### 6.8 Deterministic expansion

`expand_schedule()` turns the declared structure into the flat visit list the app consumes. It is pure — the same input always yields the same visits — and it never mutates its input. This is where every piece of arithmetic the model was deliberately *not* asked to do actually happens:

| Construct | Handling |
|---|---|
| **Repeating blocks** | One concrete visit per cycle per member, with cycle start days computed from the block's cycle length. |
| **Open-ended blocks** ("until progression") | Expanded to the protocol's stated total cycle count if one exists; otherwise capped at **12** occurrences, with an assumption recorded that says so and asks the reviewer to confirm the real number. |
| **Relative anchors** ("28 days after the last dose") | Resolved against the named visit and turned into an absolute day. If the target cannot be found, the visit survives *undated* with an assumption, rather than being dropped. |
| **Calendar offsets** ("Month 3") | Keeps its true unit. A calendar value and unit are stored alongside the 30-day approximation, so when a real patient date exists the arithmetic can be redone with true month lengths and leap years. |
| **Malformed blocks** | A non-positive cycle length, no members, or a last cycle before the first is skipped with an explicit warning naming the block — never silently ignored. |
| **Runaway expansions** | Truncated at **400** visits with a warning to check the cycle count. |

Everything that required an assumption lands on `assumptions`; anything that looks wrong lands on `warnings` — which forces the whole schedule to `needs_review`.

### 6.9 Crossing into the editor

`_schedule_extraction_payload()` (`server.py:3789`) converts the extracted schedule into the row shape the editor has always consumed. Its own docstring: *"Convert an extracted schedule to the existing editor response contract."* Three things happen here that matter:

1. **Undated visits are kept and flagged.** Each visit is tested with `_has_calculable_template_time` — does it have a day offset, or an explicitly absolute hour? If not, the row is marked `extraction_warning` and `review_status: "pending"`. The comment in the code is the policy: **never turn an unknown day into baseline.** The visit is kept, undated and flagged.
2. **Activities are routed.** The protocol's single activity list is split into **clinical** and **admin** tasks, so the editor's two columns are both populated instead of one being permanently empty.
3. **Schedule-level context travels with the rows** — `assumptions`, `source_notes`, `schedule_kind`, `anchor_study_day`, `includes_day_zero`, the classification, the verification status, confidence, refinement count and the six accuracy scores.

When a protocol prints several independent schedules of assessments, every one is extracted in the same pass and returned together as `schedule_variants` — the reviewer never has to pick one and re-upload.

### 6.10 The protocol that fits no template

This is the question that matters most in practice, and the answer is that **there is no special code path for it**. An unfamiliar design is handled by the same machinery — it simply has a lower chance of clearing every gate, which routes it to human review more often.

#### Classification is a hint, never a gate

The classifier guesses a document type and a set of archetypes from ten composable options:

`linear` · `cyclic` · `crossover` · `factorial` · `multi_arm` · `multi_phase` · `event_driven` · `intra_day` · `long_term_extension` · `mixed`

But `_classification_guidance()` injects **all ten rule sets into every synthesis, audit and refine call unconditionally**. The classifier's guess is never used to withhold a rule. The reasoning is written into the code:

> Classify runs first with the least evidence of any stage in the pipeline; if it guesses one shape and later evidence shows another (or a blend — a cyclic regimen inside a multi-arm design, a crossover with an intra-day PK block), a gate here would silently withhold the rules needed to model it correctly.

So a design that blends patterns never needed to be anticipated as its own named category. `mixed` exists explicitly as the instruction to *model each part with the structure the protocol actually prints instead of forcing one shape*.

#### The degradation ladder

When something genuinely does not fit, it descends this ladder rather than falling off it:

| Layer | Behaviour |
|---|---|
| **Extraction** | Anything unclear becomes `unresolved` timing or a recorded conflict, with the source wording preserved. Never a fabricated value. |
| **Payload** | The visit is kept, undated, with `extraction_warning` and `review_status: pending`. |
| **Editor row** | Shows a warning triangle instead of its number, is counted in the "N need review" pill, and is isolated by the **Pending** filter. The Day column falls back through: exact protocol label → day range → signed offset → an em dash. So "Cycle 2 Day 1" or "Day 14–17" prints as written rather than as a wrong number. |
| **Detail sheet** | Shows the exact protocol timing text as an editable field beside the computed placement, and for an undated visit says so plainly, with an **Acknowledge** action for visits the protocol intentionally leaves undated. |
| **Saving** | Saving with any pending row requires an explicit confirmation. |
| **Patient** | Date calculation is wrapped in try/except: on failure the visit instance is still created — dateless, status `manual_review`, carrying the reason text — and sorts to the end of the patient's timeline with that reason shown. |

> At no layer is the response "drop the visit" or "assume baseline". A visit the system cannot place is a visit it shows you and asks about.

### 6.11 Human review is the actual gate

Whatever the verification status says, nothing becomes a real trial schedule until a person opens the editor and saves it, and nothing reaches a patient until a PI approves it.

The editor's job is to make the machine's uncertainty impossible to miss: the verified / needs-review pill, the count of pending rows, the Pending filter, the per-row warning, the collapsible notes panel listing both the extraction's assumptions and the fields the agent flagged, and the save confirmation.

### 6.12 Honest edges

The mechanism above is real, and it is not unlimited.

- **Provider-dependent.** The whole graph — classify, sweep, synthesize, audit, refine — runs only for **Gemini**. The Claude, OpenRouter and Ollama extractors are explicitly single-shot (literally commented *"no classification stage"* in their own `extract_all()`) — no archetype composition, no evidence sweep, no audit/refine loop. Switching provider loses every mechanism in this section.
- **No language-specific handling.** Nothing in any prompt addresses non-English protocols; it rests entirely on the model's own multilingual reading, untested and unguided by any rule in this codebase.
- **No real OCR step.** Raw PDF bytes are attached to the Gemini call, so scanned pages are frequently still readable through the model's own vision — but the deterministic page indexer cannot verify that itself, so it flags those pages for extra scrutiny rather than skipping them.
- **Hard caps apply regardless of design complexity:** 25 MB file size, 400 visits in the expanded output, 12 preview cycles for an open-ended tail.
- **Recurring and conditional visits flatten.** The editor's row model has no field for "repeats" or "conditional", so an open-ended regimen becomes N concrete rows that read as a finite schedule, and a triggered visit reads as an unconditional one. The assumption explains it; the shape does not. See §10.
- **`verified` is a self-consistency claim, not regulatory sign-off.**

---

## 7. From approved schedule to a patient's dates

The extraction produces templates. A patient produces dates. This is the arithmetic in between, and the rules that keep it honest.

### 7.1 The anchor

Every patient date hangs off one anchor, resolved by `_patient_visit_anchor()`: their **baseline date** if set, else their **enrolment date**, else now. Always returned timezone-aware in UTC so the arithmetic is stable across devices.

### 7.2 Materialisation

On enrolment, each applicable template is copied into a visit instance carrying its own scheduled date, window start and end, status, an immutable snapshot of its clinical and admin tasks with stable IDs (derived via `uuid5` from template + kind + position + label, so repeated migration produces the same identity), and an empty comment thread.

Applicability is filtered by the patient's **substudy** and **arm**. An untagged template still matches everyone, so ordinary single-arm trials are unaffected. A template added to the trial later is retro-fitted onto already-enrolled patients under the same rules, never duplicated.

If the date cannot be computed, the instance is still created — dateless, status `manual_review`, carrying `manual_review_reason`.

### 7.3 Windows and status

Windows preserve asymmetry: a "+3 days only" window stores `window_before=0, window_after=3` rather than collapsing to ±3.

Status is derived on read from the window against now, so `planned` becomes `due` when the window opens and `overdue` when it closes — while explicit history (completed, missed, screen fail, withdrawn, drop out) is never overwritten by the passage of time.

### 7.4 Ordering

A patient's timeline reads as a calendar:

- dated visits in date order,
- undated ones **last**, where they cannot be mistaken for the next thing due,
- with the visit sequence number as tie-break.

A visit that is re-dated moves to where that date now falls. An extra visit added for one patient takes an ordering number computed as the **midpoint of its two dated neighbours**, so it lands between them and every protocol visit keeps the number the site already knows it by.

---

## 8. The two schedule engines

There are two models of a schedule in this repository. Knowing which one is live matters more than any other single fact about the codebase.

### 8.1 What is live

The **legacy MongoDB path** runs the product today: visit templates on the trial, visit instances per patient, the sponsor's editor, the PI's approve/reject review, and everything patients see. All of its endpoints are intact and unfenced — `UCTSM_AUTHORITATIVE` is unset in `backend/.env`, so the middleware that would return `410 Gone` for legacy schedule authoring is inactive.

The **UCTSM engine** — 29 PostgreSQL tables, 22 endpoints — is a stricter parallel model:

- an immutable, evidence-linked event graph where a visit is only one `event_type`,
- a typed timing discriminated union: `ABSOLUTE`, `OFFSET`, `RANGE`, `NOMINAL_WITH_WINDOW`, `WITHIN`, `NO_LATER_THAN`, `APPROXIMATE`,
- a command-checked status ladder — `DRAFT → EXTRACTED → VALIDATION_REQUIRED → IN_REVIEW → APPROVED`, with **no transition back out of `APPROVED`** (corrections clone to a new version),
- field-level review decisions with attribution,
- and a pure deterministic patient evaluator that may not call an LLM or infer unknown inputs.

### 8.2 Why the new UI was rolled back

Its screens were briefly wired in as the sponsor and clinical schedule surfaces (commit `a90b07a` reduced both to one-line re-exports). Two things made that unworkable in practice:

1. **The presentation led with process, not the schedule.** Validation state, evidence counts and a multi-step approval ladder — validate, submit for review, confirm reviewed fields, approve — instead of leading with the visit table.
2. **There is no trial linkage.** `uctsm_trials` is a separate table with no link back to the Mongo trial IDs the rest of the app uses, and `GET /trials/{id}/approved-schedules` filters on `status == "APPROVED"` only. A real trial therefore reached the screen and got *"No UCTSM schedule version is linked to this trial yet."*

Both screens have been restored to the previous editor and review UI. **The extraction underneath is still the new pipeline described in §6** — the legacy endpoint's payload builder is documented in code as converting the new extraction to the existing editor contract, which is exactly what makes "new extraction, previous presentation" work.

What remains of the new UI is orphaned:

| Component | Importers |
|---|---|
| `ProtocolScheduleScreen` | **none** — fully unreferenced |
| `PatientScheduleScreen` | only `clinical/patient-schedule.tsx`, which nothing navigates to |
| `ScheduleTable`, `presentation.ts` | only the two screens above |
| `UniversalScheduleWorkbench` | only the dev workbench route |

Its `api.ts` and `types.ts` still correctly describe the live UCTSM endpoints.

---

## 9. Changes in this working session

| Change | Where | Effect |
|---|---|---|
| **Restored the schedule UI** | `sponsor/visit-schedule.tsx`, `clinical/schedule-review.tsx` | Both had been reduced to one-line re-exports. Recovered from git (`a90b07a^`): the 2,105-line editor and the 757-line review screen, running on the new extraction. |
| **Chronological visit ordering** | `server.py` — `GET /patients/{id}`, `GET /patients/{id}/visits`, `GET /visits/mine` | Patient visits were sorted by `seq` everywhere. Now dated visits sort by date and undated ones sort last (`_order_visit_instances`), so a re-dated or added visit lands where it belongs. |
| **Extra dated visits** | `POST` / `DELETE /patients/{id}/visits` + the patient profile | A coordinator can add a one-off visit for a single patient. `_insertion_seq` places it between its dated neighbours; it never touches a template (`visit_template_id: null`), and it can be removed — while protocol visits are cancelled by status and refuse deletion with a `409`. |
| **Surfaced what was being dropped** | Editor notes panel + patient timeline | Extraction `assumptions` reached the API and were discarded by the UI; they are now shown, warning-toned, above the field-level issues. Undated visits now show their `manual_review_reason` instead of a bare dash. |

**Tests added:**

- Six in `tests/test_visit_instances.py` — insertion between neighbours, insertion before/after the whole schedule, re-dating reorders, undated sorts last, per-patient isolation and delete rules, bad date and missing patient.
- One that an undated protocol visit carries its `manual_review_reason` to the client.
- One in `tests/test_schedule_schema_v2.py` pinning the API contract that extraction assumptions survive into the editor payload — previously tested only inside the extractor, never at the boundary.

---

## 10. Open gaps

Known, specific, and worth deciding on rather than discovering later.

### 10.1 Recurring and conditional visits have no representation

The editor row model has no field for "repeats" or "conditional". An open-ended regimen becomes twelve concrete rows that read as a finite schedule, and a triggered visit reads as an unconditional one. This is a **data-model decision, not a UI fix** — it needs a call on how those should read before it can be built.

### 10.2 UCTSM has no link to real trials

`uctsm_trials` is seeded only by the demo seeder. Until legacy trials map to UCTSM trials, the engine cannot drive the sponsor UI whatever its schedules look like.

### 10.3 Orphaned UI

Four component files under `frontend/src/features/uctsm/` are now unreachable (see §8.2). They are harmless but they will read as live code to the next person.

### 10.4 Two pre-existing test failures

- `tests/test_clinical_dashboards.py::test_dashboard_is_scoped_and_normalized[pi]` and `[crc]` fail on an overdue count. Verified present on unmodified `HEAD` as well.
- The action-map audit flags placeholder copy — "Coming soon" — in `frontend/app/(app)/chat.tsx:646`.

---

## Source map

| Responsibility | Location |
|---|---|
| Extraction state graph | `backend/protocol_agent.py` |
| Providers, schema, deterministic expansion | `backend/protocol_extraction.py` |
| Page chunking and document index | `backend/protocol_document_index.py` |
| Canonical schedule schema and validation | `backend/schedule_schema.py` |
| Everything else on the API | `backend/server.py` |
| Platform admin / org admin routes | `backend/admin_routes.py`, `backend/org_routes.py` |
| UCTSM engine | `backend/app/` — `api/uctsm.py`, `db/models.py`, `domain/schedule/` |
| Route table | `frontend/app/` (Expo Router) |
| Timing and window formatters | `frontend/src/lib/visit-timing.ts` |
| Design tokens | `frontend/src/theme/tokens.ts` |
| Deeper background | `docs/` — extraction case handling, schedule formation, UCTSM architecture |

### Key functions worth knowing by name

| Function | File | What it does |
|---|---|---|
| `build_schedule_extraction_graph()` | `protocol_agent.py` | Compiles the nine-node extraction graph |
| `chunk_protocol_pages()` | `protocol_document_index.py` | The full-document partition that guarantees coverage |
| `expand_schedule()` | `protocol_extraction.py` | Deterministic structure → flat visit list |
| `_schedule_extraction_payload()` | `server.py` | Extraction → editor row contract |
| `_has_calculable_template_time()` | `server.py` | Whether a visit can produce a date without guessing baseline |
| `materialize_visit_instances()` | `server.py` | Templates → per-patient dated instances |
| `_patient_visit_anchor()` | `server.py` | Resolves the one date everything hangs from |
| `_effective_visit_status()` | `server.py` | Derives planned / due / overdue on read |
| `_order_visit_instances()` | `server.py` | Calendar ordering, undated last |
| `_insertion_seq()` | `server.py` | Places an added visit between its dated neighbours |
