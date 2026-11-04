"""Bounded, local PDF/DOCX text extraction into an unverified review draft."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import PurePath

from docx import Document
from pypdf import PdfReader

MAX_CV_BYTES = 10 * 1024 * 1024
MAX_UNPACKED_DOCX_BYTES = 40 * 1024 * 1024
MAX_CV_TEXT_CHARS = 500_000
MAX_PDF_PAGES = 80

SKILL_TERMS = (
    "AWS", "EC2", "S3", "RDS", "IAM", "VPC", "ALB", "ECS", "EKS", "Lambda",
    "Terraform", "CloudFormation", "Ansible", "Docker", "Kubernetes", "Jenkins",
    "Git", "GitHub", "CI/CD", "Prometheus", "Grafana", "CloudWatch", "Kibana",
    "Python", "Bash", "Shell", "Groovy", "Linux", "FastAPI", "AWS Bedrock",
    "Infrastructure as Code", "SSL/TLS", "DevSecOps", "MLOps",
)
SECTION_PATTERNS = {
    "summary": re.compile(r"^(professional\s+)?(summary|profile|objective|about\s+me)$", re.I),
    "experience": re.compile(r"^(work\s+)?(experience|employment|professional\s+experience|work\s+history)$", re.I),
    "education": re.compile(r"^(education|academic\s+background|qualifications)$", re.I),
    "certifications": re.compile(r"^(certifications?|licenses?|professional\s+certifications?)$", re.I),
    "skills": re.compile(r"^(technical\s+)?(skills|competencies|technologies|technical\s+expertise)$", re.I),
    "projects": re.compile(r"^(selected\s+)?(projects|personal\s+projects|portfolio)$", re.I),
    "achievements": re.compile(r"^(key\s+)?(achievements|accomplishments|results)$", re.I),
}
DATE_RANGE = re.compile(
    r"(?<!\d)(?:19|20)\d{2}\s*(?:[-–—]|to)\s*(?:(?:19|20)\d{2}|present|current|now)(?!\d)|"
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+(?:19|20)\d{2}\s*[-–—to]+\s*(?:[A-Za-z]+\.?\s+)?(?:(?:19|20)\d{2}|present|current|now)\b",
    re.I,
)


def _extract_docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > 600 or sum(item.file_size for item in infos) > MAX_UNPACKED_DOCX_BYTES:
                raise ValueError("DOCX expands beyond the safe size limit")
            names = {item.filename for item in infos}
            if "word/document.xml" not in names or "[Content_Types].xml" not in names:
                raise ValueError("The file is not a valid DOCX document")
            if any(item.flag_bits & 0x1 for item in infos):
                raise ValueError("Encrypted DOCX documents are not supported")
    except zipfile.BadZipFile:
        raise ValueError("The DOCX file is damaged or invalid") from None
    try:
        document = Document(io.BytesIO(data))
    except Exception:
        raise ValueError("The DOCX document could not be read") from None
    chunks = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            chunks.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(chunks)


def _extract_pdf(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise ValueError("Password-protected PDFs are not supported")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError(f"PDF exceeds the {MAX_PDF_PAGES}-page extraction limit")
        pages: list[str] = []
        for page in reader.pages:
            pages.append(page.extract_text(extraction_mode="layout") or "")
            if sum(map(len, pages)) > MAX_CV_TEXT_CHARS:
                raise ValueError("Extracted CV text exceeds the safe length limit")
        return "\n".join(pages)
    except ValueError:
        raise
    except Exception:
        raise ValueError("The PDF could not be read; scanned/image-only PDFs need OCR before import") from None


def extract_text(data: bytes, filename: str) -> tuple[str, str]:
    if not data or len(data) > MAX_CV_BYTES:
        raise ValueError("CV file must be between 1 byte and 10 MB")
    suffix = PurePath(filename).suffix.lower()
    if suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError("File content does not match PDF format")
        document_type, text = "pdf", _extract_pdf(data)
    elif suffix == ".docx":
        if not data.startswith(b"PK\x03\x04"):
            raise ValueError("File content does not match DOCX format")
        document_type, text = "docx", _extract_docx(data)
    else:
        raise ValueError("Only .pdf and .docx CV files are supported")
    text = text.replace("\x00", "")
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > MAX_CV_TEXT_CHARS:
        raise ValueError("Extracted CV text exceeds the safe length limit")
    if not text:
        raise ValueError("No selectable text was found; scanned/image-only PDFs need OCR")
    return document_type, text


def _split_sections(text: str) -> tuple[dict[str, list[str]], list[str]]:
    sections: dict[str, list[str]] = {name: [] for name in SECTION_PATTERNS}
    outside: list[str] = []
    active: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip().strip("•●▪◦*- ")
        if not line:
            continue
        header = next((key for key, pattern in SECTION_PATTERNS.items() if pattern.match(line.rstrip(":"))), None)
        if header:
            active = header
            continue
        (sections[active] if active else outside).append(line)
    return sections, outside


def _skill_matches(text: str) -> list[str]:
    lowered = text.casefold()
    found = []
    aliases = {"shell": r"\b(shell|bash)\b", "AWS": r"\baws\b"}
    for term in SKILL_TERMS:
        pattern = aliases.get(term, rf"(?<![\w]){re.escape(term)}(?![\w])")
        if re.search(pattern, lowered, flags=re.I) and term not in found:
            found.append(term)
    return found


def parse_cv(text: str) -> dict:
    sections, outside = _split_sections(text)
    full_text = text[:MAX_CV_TEXT_CHARS]
    experience_lines = sections["experience"]
    project_lines = sections["projects"]
    employment: list[dict[str, str | list[str]]] = []
    current: list[str] = []
    for line in experience_lines:
        if DATE_RANGE.search(line) and current:
            employment.append({"raw_text": "\n".join(current), "dates_found": DATE_RANGE.findall("\n".join(current))})
            current = []
        current.append(line)
    if current:
        employment.append({"raw_text": "\n".join(current), "dates_found": DATE_RANGE.findall("\n".join(current))})
    education = sections["education"]
    certifications = sections["certifications"]
    projects = project_lines
    bullets = [line for line in experience_lines + project_lines + sections["achievements"] if len(line) > 15]
    skills = _skill_matches("\n".join(sections["skills"]) + "\n" + full_text)
    headline = outside[0] if outside else ""
    location = next((line for line in outside[1:6] if re.search(r"\b(Nigeria|Port Harcourt|Lagos|Abuja)\b", line, re.I)), "")
    date_lines = [match.group(0) for match in DATE_RANGE.finditer(full_text)]
    email_match = re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", full_text, re.I)
    phone_match = re.search(r"(?<!\w)(?:\+?\d[\d ().-]{7,18}\d)(?!\w)", "\n".join(outside[:12]))
    link_match = re.search(r"https?://(?:www\.)?(?:linkedin\.com/in/|github\.com/)[^\s<>]+", "\n".join(outside[:12]), re.I)
    contact = {
        "full_name": headline,
        "email": email_match.group(0) if email_match else "",
        "phone": phone_match.group(0).strip() if phone_match else "",
        "profile_url": link_match.group(0).rstrip(".,)") if link_match else "",
    }
    missing = []
    if not experience_lines:
        missing.append("Employment history section was not detected")
    elif not date_lines:
        missing.append("Employment dates were not detected; verify all dates manually")
    if not education:
        missing.append("Education section was not detected")
    if not certifications:
        missing.append("Certifications section was not detected")
    if not skills:
        missing.append("No known technical skills were detected")
    if not location:
        missing.append("Location was not detected")
    return {
        "draft": {
            "headline": headline[:240],
            "experience_summary": "",
            "location": location[:200],
            "skills": skills,
            "projects": projects[:40],
            "target_titles": [],
            "preferences": {},
            "employment_history": employment[:100],
            "education": education[:40],
            "certifications": certifications[:80],
            "achievements": bullets[:100],
            "contact_details": contact,
            "nysc_completed": True,
            "nysc_discharge_certificate": True,
            "verification_status": "unverified",
        },
        "raw_sections": {key: value[:500] for key, value in sections.items()},
        "extracted_text": full_text,
        "date_strings_found": date_lines[:100],
        "missing_information": missing,
        "review_required": True,
        "extraction_method": "local_text_only_no_ocr_no_llm",
    }
