# Implementation plan

## Phase 1 — foundation: complete

- [x] Existing app inspection; local FastAPI/SQLite, Next.js, loopback-only single-user authentication, session and CSRF.
- [x] Additive database migrations; configured REA stdio MCP bridge with dynamic schemas and passive tool allow-list.
- [x] Phase 1 REA target/page/DOM/accessibility inspection and initial frontend/backend checks documented.

## Phase 2 — delivered foundation

- [x] Bounded PDF/DOCX text extraction into an editable unverified draft; preserved originals and hash deduplication.
- [x] Extended editable profile and historical snapshots.
- [x] Greenhouse, Lever, public HTTPS RSS/Atom and manual URL intake; source runs, filters, retries and deduplication.
- [x] Explainable weighted match scoring and list filters.
- [x] Verified-profile-only DOCX/PDF CV, cover letter, screening answers, preview and version storage.
- [x] Dashboard screens for profile/CV, jobs and basic application tracking.
- [x] Phase 2 unit tests (4 passed) and frontend TypeScript check (passed).
- [ ] Resolve installed FastAPI/Starlette/AnyIO in-process integration-test stall; complete full backend/live REA suite and frontend production build.
- [ ] Review UI, test against user-selected public source boards, and extend document and tracker flows.

## Later

- Persistent job scheduler and source-specific rate controls; improved document editing/render preview; interview practice; company research; Gmail OAuth; calendar analytics. LinkedIn/Indeed require a permitted public API or user-authorized visible access. Any future browser submission must pause for explicit per-job approval.
