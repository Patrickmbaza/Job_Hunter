# Security and privacy

## Current controls

- Backend/frontend and CDP are loopback-only; non-loopback host settings are rejected.
- Single-user PBKDF2-SHA256 password verifier (600,000 rounds), signed expiring HttpOnly session, signed CSRF token, SameSite=Strict, origin checks and in-process login throttling. No default credentials.
- Data APIs require authentication; mutations require CSRF. OpenAPI/Swagger/ReDoc are disabled.
- CV upload is limited to 10 MB and PDF/DOCX by extension/signature. DOCX decompression, PDF pages and extracted text are bounded. Originals are private best-effort on disk, content-deduplicated and never overwritten.
- RSS is HTTPS only, DNS checked against private IPs, redirects and environment proxies disabled; fetch time and payload are bounded.
- Documents require an explicitly verified profile. No application submit, recruiter message or email send route exists. REA exposes passive inspection only.
- `.env`, SQLite, CV originals, generated Applications and dependency folders are gitignored. Secrets and CV text are not logged.

## Local data and limitations

Set `APP_PASSWORD_HASH` in ignored `.env` with the verifier printed by `cd backend && python -m app.auth_cli`; never store the password there. Keep services on 127.0.0.1. Loopback HTTP uses `secure=False` cookies and is only for local use; remote access is unsupported.

SQLite, originals and generated files contain personal data. Stop the backend before backup. Windows-mounted filesystem modes may not equal Unix ACLs; protect the Windows account and disk. Sessions are stateless until expiry; throttling is process-local. Before any remote exposure, implement TLS, revocable sessions, security headers, encrypted credential storage and a security review.
