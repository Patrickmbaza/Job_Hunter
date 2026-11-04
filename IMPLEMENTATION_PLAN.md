# Implementation plan

## Phase 1 — foundation: complete

- [x] Existing app inspection; local FastAPI/SQLite, Next.js, loopback-only single-user authentication, session and CSRF.
- [x] Additive database migrations; configured REA stdio MCP bridge with dynamic schemas and passive tool allow-list.
- [x] Phase 1 REA target/page/DOM/accessibility inspection and initial frontend/backend checks documented.

## Phase 2 — delivered foundation

- [x] Bounded PDF/DOCX text extraction into an editable unverified draft; preserved originals and hash deduplication.
- [x] Extended editable profile and historical snapshots.
- [x] Greenhouse, Lever, Ashby public postings API, public HTTPS RSS/Atom, explicitly configured careers pages with JobPosting JSON-LD, and manual URL intake; source runs, filters, retries and deduplication.
- [x] Explainable weighted match scoring and list filters.
- [x] Verified-profile-only DOCX/PDF CV, cover letter, screening answers, preview and version storage.
- [x] Dashboard screens for profile/CV, jobs and basic application tracking.
- [x] Phase 2 unit tests (4 passed) and frontend TypeScript check (passed).
- [ ] Resolve installed FastAPI/Starlette/AnyIO in-process integration-test stall; complete full backend/live REA suite and frontend production build.
- [ ] Review UI, test against user-selected public source boards, and extend document and tracker flows.

## Later

- Persistent job scheduler and source-specific rate controls; improved document editing/render preview; interview practice; company research; Gmail OAuth; calendar analytics. LinkedIn/Indeed require a permitted public API or user-authorized visible access. Any future browser submission must pause for explicit per-job approval.

## Phase 3 — automated discovery and notifications

- [x] Stage 1: Add credential-free Ashby public board discovery and explicitly configured public career pages that publish JobPosting JSON-LD; add the source URL column additively and expose both source types in the existing dashboard.
- [ ] Stage 2: Verify discovery-to-matcher ranking and deduplication for the new connectors.
- [ ] Stage 3: Add configurable Windows Task Scheduler/WSL scanning with missed-run and overlap handling.
- [ ] Stage 4: Add opt-in Gmail OAuth notifications, digest, retry handling and audit history.
- [ ] Stage 5: Add scan and notification controls/history to the dashboard.
- LinkedIn and Indeed remain manual URL sources unless a permitted integration becomes available. Search remains user-triggered until Stage 3.
