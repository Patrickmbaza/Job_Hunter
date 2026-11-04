# Next Codex agent handoff

Updated: 2026-11-02

## Objective

Continue Phase 2 of the existing Personal AI Job Hunter in this directory. Do not recreate the project, clear SQLite, or modify the installed REA package/configuration. Keep all app services local and loopback-only. Do not send applications, recruiter messages, or emails. The user requested a durable resume note because their rate limit is nearly exhausted.

Project root: `/mnt/c/Users/patri/Desktop/REA/personal-ai-job-hunter` (WSL case-insensitive path: `/mnt/c/users/patri/desktop/rea/personal-ai-job-hunter`).

## Codex ↔ REA handshake and evidence

The app already has a supported independent MCP bridge at `backend/app/integrations/rea.py`.

1. It reads the existing `~/.codex/config.toml`, `[mcp_servers.rea]` entry. The configured command and arguments are launched via stdio using the official Python MCP SDK.
2. It discovers REA tools and JSON input schemas dynamically; tool inputs are schema-validated. API exposure is deliberately limited to a reviewed read-only allow-list. There is no generic `call_tool` route.
3. FastAPI lifespan owns the MCP process lifecycle and closes the session on shutdown. CDP endpoint configuration must resolve to explicit-port loopback HTTP. Page origins must be allow-listed.
4. Earlier Phase 1 verification confirmed the backend-owned REA process connected, discovered the catalog and inspected the `https://example.com/` browser target through the configured local CDP proxy. MCP and browser connectivity are distinct statuses. See `REA_INTEGRATION.md`.
5. Existing foundation test `backend/tests/test_foundation.py` includes a real REA target and passive inspection round trip. Run it in the normal WSL context if REA/CDP are available. A restricted sandbox may not reach localhost.

Do not invoke REA controls, invent tool schemas, or use authenticated job-site scraping. Current app integration is passive inspection only; it cannot navigate, click, type, submit, upload, or manage browser sessions.

## Work completed in this continuation

- Added additive schema migration logic and profile extension: work evidence, education, certifications, achievements, contact details, NYSC flags, CV source and profile-history snapshots. Existing profile stays unverified.
- Added local PDF/DOCX text extraction, bounded size/content handling, unverified extraction drafts, original CV preservation and content-hash duplicate detection.
- Added public Greenhouse and Lever board adapters, public HTTPS RSS/Atom, manual URL intake, source/search run tracking, duplicate URL/provider-ID checks and retry/error handling.
- Added deterministic match scoring and score explanations, missing skills/concerns, filters, match categories and provisional warning when profile is unverified.
- Added DOCX/PDF CV and cover-letter generation, screening answer drafts, preview and versioned per-job storage. Generation requires verified profile.
- Added basic dashboard flows for CV review, source setup/search, manual job intake, match list, document preview/generation, and application tracker notes/status/follow-up fields.
- Added API routes for document preview, queued generation, generation history, document listing/download. No submit route.
- Updated README, architecture, plan, user guide, REA and security/troubleshooting documentation.

Main touched/new files include `backend/app/main.py`, `models.py`, `schemas.py`, `migrations.py`, `profile_seed.py`, `services/{cv_parser,cv_storage,job_sources,discovery,matcher,documents}.py`, `backend/tests/test_phase2.py`, and `frontend/app/{page.tsx,phase-two.tsx,styles.css}`.

## Verification results actually observed

- `npm run typecheck`: passed after nullable profile edit fixes.
- Isolated Phase 2 tests: **4 passed in 2.20s** with Python path selecting the installed global FastAPI/Starlette versions and only document libraries from `/tmp/pajh-only-docs`.
- `python3 -m compileall -q app tests`: passed.
- Full backend suite was started but interrupted; output showed it had passed the first three tests and then stalled in the existing live REA/API integration. Do not report full suite passed.
- Frontend `npm run build` was started and interrupted while Next.js showed `Creating an optimized production build ...`. Build status is **unknown / not verified**. Run it to completion.
- A test API harness using FastAPI TestClient/ASGI hung on an in-process request with a temporary dependency directory containing FastAPI 0.162/Starlette 1.7. Stop testing with that set. It appears to be dependency incompatibility, not demonstrated app behavior. Avoid upgrading installed app/REA dependencies during diagnosis.
- No migration has been applied to the user's existing database in this continuation. The real DB was not reset.
- No live public source API smoke test was completed.
- No real user CV was uploaded or read by the agent.

Temporary dependency locations (outside project): `/tmp/pajh-phase2-deps` contains a full pip target and **overrides** installed framework versions if placed first on `PYTHONPATH`; `/tmp/pajh-only-docs` contains only docx/pypdf/reportlab/Pillow/lxml modules copied from that target. The Phase 2 unit test invocation that passed was:

```bash
cd backend
LD_LIBRARY_PATH=/tmp/pajh-only-docs/pillow.libs PYTHONPATH=.:/tmp/pajh-only-docs pytest -q tests/test_phase2.py
```

For actual use, install `backend/requirements.txt` in the project `.venv`. Do not use the temporary dependency target as the normal runtime.

## Immediate resume checklist

1. Review the working tree carefully; avoid broad `git status` at the parent workspace (it previously showed unrelated user files). Scope Git commands to this project.
2. Rerun `npm run typecheck`, then `npm run build` and wait for exit status.
3. Run `tests/test_phase2.py`; fix the additive migration test or code if it fails.
4. Run the foundation tests individually: auth/config tests, then API test with REA. Use a supported compatible test harness, and record actual outcomes. Avoid resetting the actual DB.
5. Inspect and test the FastAPI endpoints with an isolated temporary SQLite database. If applying the migration to the real DB is needed, back it up to `/tmp` first, check table/row counts, run only additive migration, then verify row counts/profile values are preserved.
6. Review the UI for profile form data persistence (all arrays/fields), filter fields, source run history display, document download links and application date controls. The current UI is a functional first pass, not a final visual audit.
7. Add tests for discovery filters/run history, application records and document API routes if the test harness can exercise them reliably.
8. Keep docs honest: LinkedIn/Indeed are manual URL only; parser output is unverified; score is not ATS; no scheduled/continuous search or application submission exists.

## Start commands

Backend terminal:

```bash
cd /mnt/c/Users/patri/Desktop/REA/personal-ai-job-hunter/backend
source ../.venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Frontend terminal:

```bash
cd /mnt/c/Users/patri/Desktop/REA/personal-ai-job-hunter/frontend
npm run dev
```

Dashboard: `http://localhost:3000`. Password verifier setup: `cd backend && python -m app.auth_cli`; put only the generated verifier in ignored root `.env` as `APP_PASSWORD_HASH`.

## Safety / data facts

- Existing SQLite is `backend/data/job_hunter.sqlite3`; do not delete/reset it.
- Original uploads: `backend/data/cv_originals/`; generated docs: root `Applications/`; both are ignored by Git.
- No cloud deployment, paid API, model download, or user-facing application submission has been done.
- Never put OAuth credentials, passwords, tokens, CV text or application details in logs or Git.
