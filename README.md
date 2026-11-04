# Personal AI Job Hunter

Private, local-first job search software for one user. Phase 2 adds local PDF/DOCX CV import and review, public Greenhouse/Lever/Ashby/RSS discovery, explicitly configured company career pages publishing Schema.org JobPosting JSON-LD, manual job URLs, deterministic explainable matching, application document generation, and an application tracker. It uses no paid AI or cloud services.

## Current capabilities and limits

- CV originals are retained locally; parser output is an editable, explicitly unverified draft. The parser handles selectable PDF text and DOCX text, not OCR.
- Profile details and profile snapshots are stored in SQLite. Documents can only be generated after the profile is marked verified.
- Searches can be started manually against configured public Greenhouse, Lever and Ashby boards, HTTPS RSS/Atom feeds, and one explicitly configured public company careers URL when it publishes JobPosting JSON-LD. Career pages are not crawled. Job URLs/descriptions can also be added manually.
- LinkedIn and Indeed have no general public search adapter configured. This app does not scrape logged-in pages. Public listings can be added by URL or inspected through the existing read-only REA connection when the page is already open and allow-listed.
- Matching is a deterministic internal estimate, not an employer ATS score. No application submission, recruiter message, or email sending route exists.
- The dashboard is a local single-user app. The connected REA bridge uses the existing Codex MCP configuration and only its reviewed passive inspection tools.

## Install and run (WSL Ubuntu)

```bash
cd /mnt/c/Users/patri/Desktop/REA/personal-ai-job-hunter
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
cd backend
python -m app.auth_cli
```

Copy the printed verifier (never the password) into the project `.env` as `APP_PASSWORD_HASH=...`. The backend defaults to `127.0.0.1:8000` and initializes the existing SQLite database with additive schema changes.

Terminal 1:

```bash
cd /mnt/c/Users/patri/Desktop/REA/personal-ai-job-hunter/backend
source ../.venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Terminal 2:

```bash
cd /mnt/c/Users/patri/Desktop/REA/personal-ai-job-hunter/frontend
npm ci
npm run dev
```

Open <http://localhost:3000>. Stop either process with `Ctrl+C`. Windows Chrome can use the same localhost URL. Keep both services on loopback.

Production frontend build and serving:

```bash
cd frontend
npm run typecheck
npm run build
npm run start
```

## Checks

```bash
cd backend
PYTHONPATH=.:/tmp/pajh-phase2-deps pytest -q tests/test_phase2.py
cd ../frontend
npm run typecheck
npm run build
```

Install backend requirements into `.venv` for normal use; the `/tmp` path above is only relevant to the development test session. The persistent database is `backend/data/job_hunter.sqlite3`; originals are in the ignored `backend/data/cv_originals/`, generated applications in ignored `Applications/`. Do not delete either directory to reset the app.

## Source documentation

- Greenhouse public job board API: <https://docs.greenhouse.io/job-board.html>
- Lever public postings API: <https://github.com/lever/postings-api>
- Ashby public job postings API: <https://developers.ashbyhq.com/docs/public-job-posting-api>
- Details, setup, privacy and limitations: [USER_GUIDE.md](USER_GUIDE.md), [ARCHITECTURE.md](ARCHITECTURE.md), [REA_INTEGRATION.md](REA_INTEGRATION.md), [SECURITY.md](SECURITY.md), [TROUBLESHOOTING.md](TROUBLESHOOTING.md), [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).
