"""Private original CV retention and extraction record management."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CVUpload
from app.services.cv_parser import MAX_CV_BYTES, extract_text, parse_cv


def store_cv(data: bytes, original_filename: str, db: Session) -> tuple[CVUpload, bool]:
    clean_name = Path(original_filename.replace("\\", "/")).name
    clean_name = re.sub(r"[\x00-\x1f\x7f]", "", clean_name).strip()[:255] or "uploaded-cv"
    if len(data) > min(MAX_CV_BYTES, get_settings().max_cv_upload_bytes):
        raise ValueError("CV file exceeds the configured 10 MB limit")
    document_type = Path(clean_name).suffix.lower().lstrip(".")
    if document_type not in {"pdf", "docx"}:
        raise ValueError("Only .pdf and .docx CV files are supported")
    if document_type == "pdf" and not data.startswith(b"%PDF-"):
        raise ValueError("File content does not match PDF format")
    if document_type == "docx" and not data.startswith(b"PK\x03\x04"):
        raise ValueError("File content does not match DOCX format")
    digest = hashlib.sha256(data).hexdigest()
    existing = db.scalar(select(CVUpload).where(CVUpload.sha256 == digest))
    if existing:
        return existing, True

    directory = get_settings().data_directory / "cv_originals"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    stored_path = directory / f"{uuid4().hex}.{document_type}"
    with stored_path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.chmod(stored_path, 0o600)
    except OSError:
        pass

    row = CVUpload(
        original_filename=clean_name,
        storage_path=str(stored_path),
        sha256=digest,
        byte_size=len(data),
        document_type=document_type,
        extraction_status="failed",
        extracted_data={},
    )
    db.add(row)
    try:
        parsed_type, text = extract_text(data, clean_name)
        row.document_type = parsed_type
        row.extracted_data = parse_cv(text)
        row.extraction_status = "complete"
        row.failure_reason = None
    except ValueError as exc:
        # Keep the original, even if the parser cannot read it; no silent loss.
        row.failure_reason = str(exc)[:500]
    db.commit()
    db.refresh(row)
    return row, False


def profile_snapshot(profile) -> dict:
    fields = (
        "headline", "experience_summary", "location", "skills", "projects",
        "target_titles", "preferences", "employment_history", "education",
        "certifications", "achievements", "nysc_completed",
        "contact_details", "nysc_discharge_certificate", "source_cv_id", "verification_status",
        "source_note",
    )
    return {field: getattr(profile, field) for field in fields}
