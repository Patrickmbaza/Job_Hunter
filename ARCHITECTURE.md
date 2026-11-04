# Architecture

## Local modular monolith

Next.js (127.0.0.1:3000) proxies same-origin requests to FastAPI (127.0.0.1:8000). FastAPI uses Pydantic, SQLAlchemy and the existing SQLite database. Source runs execute serially as local background tasks. No worker service, model server, cloud, Docker or GPU is required.

## Phase 2 modules

- `cv_parser.py` extracts bounded selectable PDF/DOCX text into an unverified draft. `cv_storage.py` retains originals by content hash and stores extraction/profile history locally.
- `job_sources.py` implements public Greenhouse/Lever APIs and HTTPS RSS/Atom. `discovery.py` records each search, retries transient transport failure once, filters, canonicalizes URLs, deduplicates and stores listings.
- `matcher.py` produces a deterministic 0–100 internal estimate across skills, experience, responsibilities, seniority, location and qualifications. It is not an employer ATS score.
- `documents.py` produces ATS-friendly DOCX/PDF CV, cover letter, screening draft and job description only from a verified profile. It archives previous generated versions.
- The tracker stores statuses, notes, recruiter details, dates and status history. There is no application submission, recruiter messaging or email sending route.

## REA

`backend/app/integrations/rea.py` launches the installed Codex-configured REA MCP server over stdio with the official Python MCP SDK. It discovers live tools/schemas and permits only reviewed passive inspection. It does not reimplement REA or expose arbitrary tool invocation. See `REA_INTEGRATION.md`.

## Database and resources

SQLite changes are additive. Migrations add missing columns and tables without resetting existing rows. Existing profile claims remain unverified. Uploads, parsing, RSS/JSON responses and concurrency are bounded. See `SECURITY.md` for data storage.
