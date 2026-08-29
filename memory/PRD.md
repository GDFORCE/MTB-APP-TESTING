# My Trial Board — PRD v4 (Production endpoints + i18n)

## Iteration 4 — Production wiring ✅

### New backend endpoints (server.py)
- `PATCH /api/visits/{id}` — Mark complete / reschedule / flag (PI, CRC, sponsor only). Writes audit log.
- `GET/POST/PATCH/DELETE /api/reminders` — patient medication reminders (real CRUD)
- `POST /api/invitations` — invite patient/team via email/phone. Uses `EMAIL_API_KEY` (Resend) when set, else logs invite link.
- `POST /api/shares` + `GET /api/shares/{token}/schedule.pdf` — secure link + on-the-fly PDF (reportlab) of the visit schedule. 7-day expiry, view counter.
- `GET/PATCH /api/preferences` — notification toggles + language
- `POST /api/push/register` — register Expo push token (Emergent push key wired in env at deploy time)
- `GET /api/audit-logs` — for sponsor/CRO/PI

### Frontend wiring
- **Medication Reminder** (patient) — full CRUD: list, toggle, add (med/dose/time), delete.
- **Invite Patient** — calls `/api/invitations`, shows real share link on success.
- **Share Schedule** (sponsor) — picks trial, generates link/PDF via `/api/shares`. PDF opens in browser (`Linking.openURL`).
- **Clinical Visit Detail** — Mark complete / Reschedule call `PATCH /api/visits/{id}`.
- **Profile** — Switch toggles for email/push/SMS notifications + language picker (English / Hindi) persist to `/api/preferences`. App language switches live via i18n.

### i18n
- `i18next` + `react-i18next` set up with English + Hindi resources covering welcome / dashboard / trial / medication / profile keys.
- Saved to AsyncStorage and to user preferences (`PATCH /api/preferences`) so it persists across devices.

## Still left
- **Push notifications** runtime: need `EMERGENT_PUSH_KEY` (auto-injected at deploy) + `google-services.json` (you upload via Publish flow). Token registration endpoint is ready.
- **Email sending**: needs `EMAIL_API_KEY` in `/app/backend/.env` (Resend, free tier 100/day). Without it, invites log the link.
- **Patient Calendar grid** — left for you to code per request.
- **Admin portal (16 screens)** — explicitly excluded per your request.

## Production checklist
| Item | Status |
|---|---|
| JWT auth + bcrypt + refresh tokens | ✅ |
| Real-time WebSocket chat | ✅ |
| Role-based access control | ✅ |
| Audit logging on mutations | ✅ |
| PDF export | ✅ |
| Multi-language (en + hi) | ✅ |
| Visit lifecycle (create/complete/flag) | ✅ |
| Notification prefs persistence | ✅ |
| Push token registration | ✅ (build needs `google-services.json`) |
| Email sending | wired, needs `EMAIL_API_KEY` |
