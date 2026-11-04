# Troubleshooting

## Local sign-in is not configured

From `backend/`, run `python -m app.auth_cli`. Put only its printed verifier in ignored project `.env` as `APP_PASSWORD_HASH=...`, then restart the API.

## Backend/frontend unavailable

Start FastAPI at 127.0.0.1:8000 and Next.js at localhost:3000 using README commands. Check `/api/health`, port conflicts, and that services were not bound to `0.0.0.0`.

## CV rejected or extraction is empty

Only real PDF and DOCX files (under 10 MB) are accepted. Password-protected PDFs, damaged documents, scanned/image-only PDFs and oversized DOCX archives are rejected. Originals may be kept with a failed extraction status; upload a selectable-text copy if needed.

## Job source returned no results

Confirm the Greenhouse board token or Lever company slug, or that the public HTTPS feed is reachable. Check **job search history** / source results for failure status. Search keyword and location filters apply to returned title/company/description/location. LinkedIn and Indeed are manual URL sources only. Network restrictions can prevent a feed/API request.

## REA/browser unavailable

The backend starts an independent REA stdio client from Codex config. Check the `rea` server config, browser proxy and exact allow-listed origin. MCP connected and browser tested are separate states. Restricted tool sandboxes may block localhost though normal WSL may work. Keep CDP loopback-only.

## Documents unavailable

The profile must be marked verified before preview/generation. Verify every claim against the preserved original. Generated files are under ignored `Applications/`.

## Tests/build

Backend requirements should be installed in the project virtualenv. Frontend dependencies use `npm ci`; typecheck/build need available memory and registry-installed dependencies. See current verification results in the project task report; don't infer a successful build from typecheck alone.
