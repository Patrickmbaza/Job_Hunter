# User guide

## Import and verify your CV

Open **CV studio**, upload a PDF/DOCX up to 10 MB, then review extracted contact, skills, work evidence and sections. Only selectable PDF text and DOCX are supported; no OCR or AI service is used. Parser output is an unverified draft. Edit fields and compare dates, employers, responsibilities and achievements against the preserved original. Mark verified only after this review.

Structured profile arrays are editable as JSON in the current form. Originals are in `backend/data/cv_originals/`; they are never overwritten.

## Discover jobs

Under **Find jobs**, configure public Greenhouse board slug, Lever company slug, or HTTPS RSS/Atom feed. Enter keywords/locations and run a local search. Runs store per-source outcomes. Alternatively paste a public job URL and description. LinkedIn/Indeed have no public search adapter here; no login scraping or protections bypass. REA supports passive inspection only for an already-open allow-listed page.

**Matched jobs** lets you filter by title/company and minimum score, inspect match factors, missing skills and description, and save an opportunity. Score is an internal estimate, not an employer ATS result.

## Prepare and track

Preview/generate CV and cover-letter docs only when profile status is verified. Outputs and screening answers are stored under `Applications/Company-Role/`; later generations preserve previous files in `versions/`. Review every file before sharing.

Save a job to create a tracker. Update status, notes and follow-up date in **Applications**. Mark **Applied** only after you submit and confirm it yourself; no submit button or route exists. Status changes are recorded.

## Backup

Stop the backend and privately copy `backend/data/job_hunter.sqlite3`, `backend/data/cv_originals/` and `Applications/`. These contain sensitive CV/application content.
