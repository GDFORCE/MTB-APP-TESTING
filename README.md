# MTB-APP

Clinical-trials app: Expo (React Native) frontend + FastAPI/MongoDB backend.

Active development happens on the `full-app-build` branch.

## Prerequisites
- Git
- Node.js 20+ (`node -v`)
- Python 3.11+ (`python --version`)
- **Expo Go** app installed on the phone (Play Store / App Store)
- A MongoDB Atlas connection string
- Phone and laptop on the **same Wi-Fi network**

## 1. Clone
```
git clone https://github.com/GDFORCE/MTB-APP.git
cd MTB-APP
git checkout full-app-build
```

## 2. Backend (FastAPI)
```
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows   (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env        # Windows   (Mac/Linux: cp .env.example .env)
```
Open `backend/.env` and fill in at least `MONGO_URL`, `JWT_SECRET`,
`JWT_REFRESH_SECRET` (see comments in the file). Keep `DEV_OTP_MODE=true`
for testing — signup OTPs are then the fixed `DEV_OTP_CODE`.

Start it (bound to all interfaces so the phone can reach it):
```
uvicorn server:app --host 0.0.0.0 --port 8000
```

### Verified Gemini protocol extraction

With `PROTOCOL_EXTRACTION_PROVIDER=gemini`, schedule extraction runs as a
bounded LangGraph evaluator/optimizer workflow. Gemini maps the document,
collects traceable timing and visit/activity evidence, builds one schedule, and
independently reconstructs a second confirmation schedule without seeing the
builder output. Deterministic code expands and compares both schedules before
an adjudicating audit. A repair pass fixes evidence-backed omissions or errors
before both confirmation and audit run again. Configure the maximum number of
repair passes in `backend/.env`:

```dotenv
PROTOCOL_EXTRACTION_PROVIDER=gemini
GEMINI_API_KEY=your-key
PROTOCOL_EXTRACTION_MAX_REFINEMENTS=2
```

The API returns `verification.status`, audit confidence, refinement count,
remaining issues, and separate accuracy values for visit coverage, timing,
windows, visit types, procedure mapping, and the overall schedule. Every
applicable dimension must score at least 95%, all populated fields must link to
high-confidence evidence, and the builder and confirmer must agree. Missing
windows remain unknown instead of becoming a fabricated default. A schedule
that reaches the limit with unresolved findings is marked `needs_review`;
extraction remains a draft requiring human approval.

The Add Trial PDF upload uses this same workflow to populate both screens. It
returns trial metadata and stores the audited schedule for two
hours under a user-scoped extraction ID. After the trial is created, the Visit
Schedule screen consumes that prepared result without uploading the PDF or
calling the AI again.

### Local protocol extraction (no AI API key)

For offline development on Windows, install Ollama, then download the small
Qwen model:

```powershell
[Environment]::SetEnvironmentVariable('OLLAMA_MODELS', 'D:\ollama_models', 'User')
ollama pull qwen3-vl:4b-instruct-q4_K_M
```

Restart Ollama after setting `OLLAMA_MODELS`, then use these values in
`backend/.env`:

```dotenv
PROTOCOL_EXTRACTION_PROVIDER=ollama
OLLAMA_HOST=http://localhost:11434
OLLAMA_PROTOCOL_EXTRACTION_MODEL=qwen3-vl:4b-instruct-q4_K_M
OLLAMA_PDF_BATCH_PAGES=2
OLLAMA_EXTRACTION_CACHE_DIR=D:\OllamaExtractionCache
```

The local provider renders two pages at a time and checkpoints every batch, so
scanned and 250-page documents work without separate OCR or loading the entire
PDF into model memory. Retrying the same PDF resumes from its saved SHA-256
checkpoint. Processing is sequential and can take hours on an 8 GB CPU-only PC.

## 3. Frontend (Expo)
In a second terminal:
```
cd frontend
npm install
copy .env.example .env        # Windows   (Mac/Linux: cp .env.example .env)
```
Open `frontend/.env` and set `EXPO_PUBLIC_BACKEND_URL` to the laptop's LAN IP,
e.g. `http://192.168.1.5:8000` (find the IP with `ipconfig` / `ifconfig`).

Start Expo:
```
npx expo start
```
Scan the QR code with the phone — Expo Go opens the app.

## Troubleshooting
- **Phone can't connect / network error in app**: laptop firewall is usually
  blocking Node or Python. Allow them through the firewall (Windows prompts on
  first run — choose Allow), and double-check both devices share the same Wi-Fi.
- **QR scan opens but bundle never loads**: try `npx expo start --tunnel`
  (works across networks, slower).
- **Changed `.env`**: restart `expo start` — Expo only reads env vars at startup.
