# Prompt Change Log

A running record of every prompt made to Claude in this repo — not just ones
that changed code, but investigations, diagnoses, and answered questions too
— along with what was done in response, which files changed (if any), and
any user-facing behavior that changed as a result. Newest entries at the
bottom.

---

## 2026-08-19 — Terms & Conditions checkbox on registration

**Prompt (verbatim):**
> see in each of the registrarion screen we have to giev a Terms & Conditions - missing in Org. Registration Page. in the end as a tick box when they tick that box a pop of terms and condition shoudl show and onces they acept done

**Response summary:**
Investigated the existing registration flow and found `register.tsx` is the
single shared screen for every registration type (patient, sponsor, CRO, SMO,
site/PI/CRC) — so "Org. Registration Page" is this same screen. It already
had entry-time "Declaration" modals (a canned confirmation statement shown
before the form), but no visible checkbox and no real Terms & Conditions
content anywhere in the registration flow. Added a dedicated checkbox at the
end of the form, separate from the existing declaration modals, that opens a
popup showing the real Terms & Conditions (fetched from the same `/legal/terms`
endpoint the app already uses in the clinical profile screen), with an
"I Accept" button that ticks the box and closes the popup. Continue is now
blocked until it's ticked.

**Files changed:**
- `frontend/app/(auth)/register.tsx` — added `termsAccepted`/`showTerms`/
  `termsDoc`/`termsLoading` state, a fetch effect for `GET /legal/terms`,
  the checkbox row (with tappable "Terms & Conditions" link) placed above
  the footer, a new `ModalCard` rendering the fetched terms with an "I Accept"
  button, and added `termsAccepted` to the existing `canContinue` gate.

**Feature impact:**
- All registration variants (not just organizations) now require ticking
  "I have read and agree to the Terms & Conditions" before Continue is
  enabled — this is a new hard requirement that did not exist before.
- No backend changes; reused the existing public `GET /api/legal/terms`
  endpoint (no auth required, so it works pre-login during registration).
- Verified: `tsc --noEmit` passes clean project-wide; endpoint tested live
  against the running backend and confirmed it returns real content.

---

## 2026-08-19 — Terms & Conditions popup stuck on "Loading latest document…"

**Prompt (verbatim):**
> from where itis loading this and why it is loading fomr a min

**Response summary:**
User hit the new Terms & Conditions popup (above) hanging indefinitely on a
phone test. Traced it to `frontend/src/api/client.ts`: the shared `api`
axios instance is created with no `timeout` at all (`axios.create({ baseURL:
API_BASE })`), so any request that hits a network hiccup hangs forever
instead of failing — the popup's loading state had no way to time out and
fall back to its built-in summary text. Did not add a global timeout, since
the protocol-extraction upload legitimately takes several minutes and a
short global timeout would break it. Instead scoped a 15-second timeout to
just the Terms & Conditions fetch, which should never legitimately take
that long.

**Files changed:**
- `frontend/app/(auth)/register.tsx` — added `{ timeout: 15000 }` to the
  `api.get("/legal/terms")` call.

**Feature impact:**
- The Terms & Conditions popup now fails over to its built-in fallback text
  after 15s instead of spinning forever if the network hiccups.
- No other endpoints were touched; the app-wide lack of a default request
  timeout on `api` is still present everywhere else and is worth a follow-up
  decision (a moderate global default plus an explicit longer override for
  slow endpoints like protocol extraction), but that wasn't done here since
  it touches every API call in the app.

---

## 2026-08-19 — Terms & Conditions changed from live-fetched to static content

**Prompt (verbatim):**
> see i do't want this to do any damage to the protocl extract due to this new time limt thing so i decide insted of laoding let' keep stiact how the intial pop up will come when we click on any registon

**Response summary:**
Even though the 15s timeout added in the previous entry was already scoped
to only the Terms & Conditions call and could not affect the protocol
extraction endpoint, the user preferred to remove the network dependency
entirely for extra safety. Removed the `GET /legal/terms` fetch, the
`termsDoc`/`termsLoading` state, and the loading-spinner branch from the
popup. The Terms & Conditions modal now renders a fixed, hardcoded set of
sections (version "2.1", effective "01 Jan 2025") the moment it opens — no
network call, no loading state, no timeout, nothing that can hang.

**Files changed:**
- `frontend/app/(auth)/register.tsx` — replaced the fetch effect and
  `termsDoc`/`termsLoading` state with static `TERMS_BLOCKS`/`TERMS_VERSION`/
  `TERMS_EFFECTIVE` constants; simplified the modal body to render them
  directly.

**Feature impact:**
- The Terms & Conditions popup now opens instantly and identically every
  time, with no dependency on backend reachability.
- Content is now static in the app bundle rather than reflecting the
  backend's `/legal/terms` document — if that document is updated later
  (e.g. via the admin-side legal-content flow), this popup will not pick up
  the change until the app code is updated too. Worth knowing since the
  same live content is still used elsewhere in the app (e.g. the clinical
  profile screen's Terms & Conditions viewer).
- Verified: `tsc --noEmit` passes clean project-wide.

---

## 2026-08-19 — Removed the "Create a New Organization?" confirmation popup

**Prompt (verbatim):**
> see This Pop-up is not required for initial registration
> Requires only if the organization already exists
> Remove this popup

**Response summary:**
On Continue, registration ran an authoritative duplicate-organization check
(`GET /organizations/registration-check`) and always showed one of two
popups: "Organization Already Registered" (`orgCheck === "exists"`) or
"Create a New Organization?" (`orgCheck === "new"`), the latter requiring an
extra tap to confirm even for a completely new, first-time org registration.
Per the request, the "new" branch no longer shows a popup at all — when no
existing match is found, registration now proceeds directly. The
"Organization Already Registered" popup (with the platform-admin contact
card) is untouched, since that's the case the user said should still
require a stop.

**Files changed:**
- `frontend/app/(auth)/register.tsx` — in `handleContinue`, the "no match"
  branch now calls `proceed()` directly instead of `setOrgCheck("new")`;
  removed the now-unreachable "new" branch's JSX from the org-existence
  `ModalCard`; narrowed the `orgCheck` state type from
  `"exists" | "new" | null` to `"exists" | null`.

**Feature impact:**
- Registering a brand-new organization (sponsor/CRO/SMO/site) is now one tap
  shorter — no confirmation popup, straight to submission.
- The existing-organization warning and platform-admin-contact flow are
  unchanged.
- Verified: `tsc --noEmit` passes clean project-wide; confirmed no other
  file referenced the removed testIDs (`cancel-create-organization`,
  `confirm-create-organization`) or the `"new"` org-check state.

---

## 2026-08-19 — Security Questions now come after phone/email verification for every registration path

**Prompt (verbatim):**
> see in the registiorn work flow
>
> 'Security questions' screen should be displayed after verifying Phone number & email ID

**Response summary:**
Traced the registration flow across `register.tsx` → `verify-otp.tsx` →
`security-questions.tsx` → `set-password.tsx` and found the step order
differed by path: invited users already verified phone/email *before*
security questions, but self-registering users (patient, sponsor, CRO, SMO,
site/PI/CRC) answered security questions *before* verification — the order
the user flagged as wrong. Unified both paths to match the already-correct
invited order: register → verify phone/email → security questions → set
password.

This required both frontend and backend changes, since the backend had two
checks that assumed security questions could only ever be submitted by
invited users:
- `POST /auth/register/security-questions` hard-rejected any pending
  registration without an `invitation_id` ("Security questions must be
  submitted during registration").
- `POST /auth/register/complete` only enforced
  `security_questions_completed` when `invitation_id` was present, so a
  self-registered user could previously reach the password step without
  ever answering security questions if the frontend allowed it.

**Files changed:**
- `frontend/app/(auth)/register.tsx` — unified the invite and non-invite
  branches of `proceed()`: both now call `POST /auth/register/start`
  (with `security_questions: []`) and navigate to `verify-otp` immediately,
  instead of the non-invite path going straight to `security-questions`
  with an unsubmitted payload.
- `frontend/app/(auth)/verify-otp.tsx` — always navigates to
  `security-questions` after a successful verify (previously only did this
  for `invited === "1"`; otherwise it skipped straight to `set-password`).
  Step indicator fixed to a constant "Step 3 of 5".
- `frontend/app/(auth)/security-questions.tsx` — removed the dead branch
  that called `/auth/register/start` and navigated to `verify-otp` (no
  longer reachable now that verification always happens first); the screen
  now always calls `POST /auth/register/security-questions` against an
  already-verified `registration_id`. Removed the now-unused `payload`/
  `CORE` machinery. Step indicator fixed to a constant "Step 4 of 5".
- `backend/server.py` — `register_security_questions` no longer requires
  `invitation_id` on the pending registration; `register_complete`'s
  `security_questions_completed` check now applies to every registration,
  not only invited ones.
- `backend/tests/test_registration_validation.py` — added
  `test_self_registration_also_requires_security_questions_before_password`,
  mirroring the existing invited-path test
  (`test_email_invitee_verifies_phone_only`) for a self-registered sponsor:
  verifies both OTP channels, confirms `/register/complete` is blocked
  before security questions are answered, confirms
  `/register/security-questions` now succeeds without an `invitation_id`.

**Feature impact:**
- Every registration path now has the identical step order: Tell us about
  you → Verify phone/email → Security questions → Set password. Step
  numbers in the header are now consistent across paths (previously they
  swapped depending on invited vs. self-registration).
- A self-registered user can no longer reach the password step without
  answering security questions — this was silently possible before if the
  frontend ever allowed it, since the backend didn't enforce it for
  non-invited registrations.
- Verified: `tsc --noEmit` passes clean project-wide;
  `test_registration_validation.py` passes 14/14 (13 existing + 1 new) run
  in isolation. Note: this suite is flaky when run in the same pytest
  invocation as `test_foundation.py`/`test_patient_phone_only.py`
  ("Event loop is closed") — confirmed pre-existing and unrelated to this
  change, since all three pass individually both before and after.

---

## 2026-08-20 — Split phone/email verification into two screens with per-channel resend cooldowns

**Prompt (verbatim):**
> Create separate screens for Phone & email Verification and we need to show the resned option only onces the timmer got complted
>
> and the timmer for resend to apper is here
>
> 60-second countdown (for SMS) & 120-second countdown (for email) for the "Resend Code"

**Response summary:**
Asked two clarifying questions first since the design had real branch points:
screen order (chose phone-then-email) and whether the new resend cooldown
should replace or run alongside the old shared 2-minute "expires in" timer
(chose replace). Then found and fixed a real backend blocker before this
could work at all: `POST /auth/register/verify` required every still-
unverified required channel's code in the *same* request, so submitting
just the phone code (as the new phone screen does) would have failed with
"Email verification code is required" for any two-channel registration.
Relaxed it to accept one channel's code at a time, remembering what's
already verified across calls — a self-contained bug I had to find and fix
mid-implementation, not something requested directly.

Replaced the single combined `verify-otp.tsx` (deleted) with two screens
sharing a new `OtpVerifyScreen` component, each with its own resend
cooldown (60s phone / 120s email) that hides the Resend option entirely
until it reaches zero, instead of showing a disabled button throughout a
shared 2-minute window like before.

**Files changed:**
- `frontend/src/features/auth/otp-verify.tsx` (new) — shared single-channel
  OTP UI: digit cells, per-channel resend-cooldown countdown/track, verify/
  resend network calls, and the locked/expired/session-missing states
  (moved from the old combined screen).
- `frontend/app/(auth)/verify-phone.tsx` (new) — Step 3, 60s cooldown. On
  success, checks the verify response's `verified` flag: if the whole
  registration is now verified (phone-only flows — patients, invited
  users), goes straight to security questions; otherwise (email still
  needed) goes to the new email screen.
- `frontend/app/(auth)/verify-email.tsx` (new) — Step 4, 120s cooldown, only
  reached when both channels are required. Always proceeds to security
  questions on success.
- `frontend/app/(auth)/verify-otp.tsx` — deleted (fully replaced).
- `frontend/app/(auth)/register.tsx` — pushes to `verify-phone` instead of
  the old `verify-otp`; step header is now `Step 2 of 5` (phone-only flows)
  or `Step 2 of 6` (both channels), computed from role/invite status.
- `frontend/app/(auth)/security-questions.tsx`,
  `frontend/app/(auth)/set-password.tsx` — now thread a `channels` param
  through so their step numbers adjust to 5-of-5/6-of-6 correctly instead
  of a hardcoded step 4/5.
- `frontend/src/lib/upload.ts` — updated a stale comment describing the old
  flow order.
- `backend/server.py` — `register_verify` no longer requires every pending
  channel's OTP in one call; a channel is validated only when its code is
  actually supplied, and skipped (not errored) otherwise, so it can be
  verified in a later, separate call.
- `backend/tests/test_registration_validation.py` — added
  `test_verify_accepts_one_channel_per_call`, covering: neither code
  supplied (still errors), phone-only submission (accepted, returns
  `phone_verified: true` / `verified: false`), then a separate email-only
  submission (returns `verified: true`).

**Feature impact:**
- Phone and email verification are now two distinct screens instead of one
  combined screen — matches the screenshot the user was looking at, now
  split.
- The Resend option is hidden (not just disabled) until its channel's own
  cooldown reaches zero: 60s for phone, 120s for email. The old shared
  2-minute "expires in" countdown is gone; the underlying OTP code itself
  is still valid for the backend's real TTL (10 minutes by default) even
  though the UI no longer displays that separately.
- `POST /auth/register/verify` can now be called once per channel instead
  of requiring both codes together — a genuine capability change to the
  API contract, not just this app's own client.
- Verified: `tsc --noEmit` passes clean project-wide;
  `test_registration_validation.py` passes 15/15 in isolation; confirmed no
  remaining references to the deleted `verify-otp` route anywhere in the
  frontend; confirmed the running backend (`--reload`) picked up the change
  and stayed healthy.

---

## 2026-08-20 — OTP code validity duration (question, no change)

**Prompt (verbatim):**
> and till how much time the otp of both phone and email will be valid before expireing

**Response summary:**
Answered from `backend/.env` and `otp_service.py`: `OTP_TTL_MIN=10` — both
phone and email codes are valid for 10 minutes, one shared setting for both
channels (not configurable per channel). Noted this is now enforced purely
server-side after yesterday's change, since the UI no longer shows a
countdown for it (only the 60s/120s resend cooldown). Follow-up: user
confirmed 10 minutes is fine as-is — no change made.

**Files changed:** none.

**Feature impact:** none — informational only.

---

## 2026-08-20 — Removed the "Development Accounts" panel from Sign In

**Prompt (verbatim):**
> here in this page can u Remove the bottom panel "Development Accounts"

**Response summary:**
Removed the dev-credentials hint box from the bottom of the sign-in screen.
It was already gated behind `__DEV__` (so it never showed in production
builds), but the user wanted it gone from the screen entirely regardless.

**Files changed:**
- `frontend/app/(auth)/sign-in.tsx` — removed the `__DEV__`-gated
  "Development accounts" box and its now-unused `demoBox` style.

**Feature impact:**
- The Sign In screen no longer shows the "Development Accounts" hint under
  any circumstance.
- Verified: `tsc --noEmit` passes clean project-wide.
