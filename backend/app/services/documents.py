"""Factual DOCX/PDF application documents from a verified profile only."""

from __future__ import annotations

import html
import os
import re
import shutil
import tempfile
import unicodedata
from pathlib import Path

from docx import Document
from docx.shared import Inches, Pt
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import GeneratedDocument, Job, ProfessionalProfile


def _slug(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    result = re.sub(r"[^A-Za-z0-9]+", "-", folded).strip("-")
    return result[:80] or "Role"


def _ordered_skills(job: Job, profile: ProfessionalProfile) -> list[str]:
    current = list(profile.skills or [])
    wanted = list(job.required_skills or []) + list(job.preferred_skills or [])
    selected = [skill for term in wanted for skill in current if term.casefold() in skill.casefold() or skill.casefold() in term.casefold()]
    return list(dict.fromkeys(selected + current))


def _employment_blocks(profile: ProfessionalProfile) -> list[str]:
    output = []
    for item in profile.employment_history or []:
        if isinstance(item, dict):
            raw = item.get("raw_text")
            if raw:
                output.append(str(raw).strip())
            else:
                fields = [str(item[key]).strip() for key in ("title", "employer", "dates", "details") if item.get(key)]
                if fields:
                    output.append(" | ".join(fields))
    return output


def build_documents(job: Job, profile: ProfessionalProfile) -> dict[str, str]:
    if profile.verification_status != "verified":
        raise ValueError("Verify the professional profile against the original CV before generating application documents")
    contact = profile.contact_details or {}
    name = str(contact.get("full_name", "")).strip() or "[Your name]"
    contact_line = " | ".join(str(contact[key]).strip() for key in ("email", "phone", "profile_url") if contact.get(key))
    skills = _ordered_skills(job, profile)
    employment = _employment_blocks(profile)
    achievements = list(profile.achievements or [])
    projects = list(profile.projects or [])
    education = list(profile.education or [])
    certifications = list(profile.certifications or [])
    sections = [name]
    if contact_line:
        sections.append(contact_line)
    sections.extend([str(profile.headline), str(profile.location), "", "PROFESSIONAL SUMMARY", profile.experience_summary])
    if skills:
        sections.extend(["", "TECHNICAL SKILLS", ", ".join(skills)])
    if employment:
        sections.extend(["", "PROFESSIONAL EXPERIENCE"])
        sections.extend(employment)
    if achievements:
        sections.extend(["", "SELECTED ACHIEVEMENTS"])
        sections.extend(f"• {item}" for item in achievements)
    if projects:
        sections.extend(["", "PROJECTS"])
        sections.extend(f"• {item}" for item in projects)
    if education:
        sections.extend(["", "EDUCATION"])
        sections.extend(education)
    if certifications:
        sections.extend(["", "CERTIFICATIONS"])
        sections.extend(certifications)
    if profile.nysc_completed and profile.nysc_discharge_certificate:
        sections.extend(["", "NATIONAL SERVICE", "NYSC completed; discharge certificate held."])
    cv_text = "\n".join(str(line) for line in sections if line is not None).strip()

    cover_lines = [f"Re: {job.title} — {job.company}", "", "Dear Hiring Team,", "",
                   f"I am applying for the {job.title} opportunity at {job.company}."]
    if profile.experience_summary.strip():
        cover_lines.append(f"My verified profile states: {profile.experience_summary.strip()}")
    if skills:
        matched = [skill for skill in skills if skill.casefold() in (job.description or "").casefold()]
        if matched:
            cover_lines.append("Relevant skills recorded in my profile include " + ", ".join(matched[:8]) + ".")
    cover_lines += ["", "I have attached a CV tailored by reordering verified profile information for this role. I would welcome the opportunity to discuss the position.", "", "Regards,", name]
    cover_text = "\n".join(cover_lines)

    report = job.match_report or {}
    answers = [
        "# Screening answer draft",
        "",
        f"Role: {job.title} — {job.company}",
        "",
        "These answers use only verified profile details. Review each response before use.",
        "",
        f"**Relevant experience:** {profile.experience_summary.strip() or '[Add your accurate experience summary.]'}",
        f"**Matching skills:** {', '.join(report.get('matching_skills', [])) or '[Review the job requirements and enter accurate skills.]'}",
        "**Work authorization:** Not recorded in the verified profile. Enter your accurate response.",
        "**Salary expectations:** Not recorded. Enter your expectation after reviewing the role and local market.",
        "**Availability / start date:** Not recorded. Enter your accurate availability.",
        "**Other screening questions:** Draft from the verified facts above; do not guess dates, credentials, or work authorization.",
    ]
    return {"cv": cv_text, "cover_letter": cover_text, "screening_answers": "\n".join(answers)}


def _docx(path: Path, title: str, text: str) -> None:
    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.65)
    section.bottom_margin = Inches(0.65)
    section.left_margin = Inches(0.75)
    section.right_margin = Inches(0.75)
    normal = document.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(10)
    normal.paragraph_format.space_after = Pt(4)
    for index, line in enumerate(text.splitlines()):
        if not line:
            continue
        if line.isupper() and len(line) < 45:
            document.add_heading(line.title(), level=2)
        elif line.startswith("• "):
            document.add_paragraph(line[2:], style="List Bullet")
        elif index == 0:
            paragraph = document.add_paragraph()
            run = paragraph.add_run(line)
            run.bold = True
            run.font.size = Pt(16 if title == "Tailored CV" else 12)
        else:
            document.add_paragraph(line)
    document.save(path)


def _pdf_font() -> str:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ):
        if os.path.isfile(path):
            try:
                pdfmetrics.registerFont(TTFont("JobHunterSans", path))
                return "JobHunterSans"
            except Exception:
                continue
    return "Helvetica"


def _pdf(path: Path, title: str, text: str) -> None:
    font = _pdf_font()
    styles = getSampleStyleSheet()
    body = ParagraphStyle("JobBody", parent=styles["BodyText"], fontName=font, fontSize=9.5, leading=13, alignment=TA_LEFT, textColor=colors.HexColor("#172238"), spaceAfter=5)
    heading = ParagraphStyle("JobHeading", parent=body, fontSize=13, leading=17, spaceBefore=9, spaceAfter=5, textColor=colors.HexColor("#142b55"))
    title_style = ParagraphStyle("JobTitle", parent=body, fontSize=17 if title == "Tailored CV" else 12, leading=21, spaceAfter=8)
    story = []
    for index, line in enumerate(text.splitlines()):
        if not line:
            story.append(Spacer(1, 3))
            continue
        escaped = html.escape(line)
        if index == 0:
            story.append(Paragraph(escaped, title_style))
        elif line.isupper() and len(line) < 45:
            story.append(Paragraph(escaped.title(), heading))
        elif line.startswith("• "):
            story.append(Paragraph("• " + html.escape(line[2:]), body))
        else:
            story.append(Paragraph(escaped, body))
    document = SimpleDocTemplate(str(path), pagesize=LETTER, leftMargin=0.72 * inch, rightMargin=0.72 * inch, topMargin=0.58 * inch, bottomMargin=0.58 * inch, title=title)
    document.build(story)


def generate_files(job: Job, profile: ProfessionalProfile, db: Session) -> tuple[int, list[GeneratedDocument]]:
    bundle = build_documents(job, profile)
    root = get_settings().applications_directory / f"{_slug(job.company)}-{_slug(job.title)}"
    root.mkdir(parents=True, exist_ok=True)
    latest = {
        "tailored_cv_docx": ("Tailored-CV.docx", "Tailored CV", bundle["cv"], _docx),
        "tailored_cv_pdf": ("Tailored-CV.pdf", "Tailored CV", bundle["cv"], _pdf),
        "cover_letter_docx": ("Cover-Letter.docx", "Cover Letter", bundle["cover_letter"], _docx),
        "cover_letter_pdf": ("Cover-Letter.pdf", "Cover Letter", bundle["cover_letter"], _pdf),
        "screening_answers": ("Screening-Answers.md", "Screening Answers", bundle["screening_answers"], None),
        "job_description": ("Job-Description.txt", "Job Description", job.description or "", None),
    }
    previous = db.scalar(select(GeneratedDocument.version).where(GeneratedDocument.job_id == job.id).order_by(GeneratedDocument.version.desc()).limit(1)) or 0
    version = previous + 1
    staging = Path(tempfile.mkdtemp(prefix=".document-build-", dir=root))
    try:
        for key, (filename, title, text, generator) in latest.items():
            target = staging / filename
            if generator:
                generator(target, title, text)
            else:
                target.write_text(text, encoding="utf-8")
        if previous:
            version_dir = root / "versions" / f"v{previous:03d}"
            version_dir.mkdir(parents=True, exist_ok=True)
            for filename, *_ in latest.values():
                current = root / filename
                if current.is_file() and not (version_dir / filename).exists():
                    shutil.copy2(current, version_dir / filename)
        for filename, *_ in latest.values():
            os.replace(staging / filename, root / filename)
        rows = []
        for kind, (filename, *_rest) in latest.items():
            path = root / filename
            digest = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
            row = GeneratedDocument(job_id=job.id, kind=kind, version=version, storage_path=str(path), sha256=digest)
            db.add(row)
            rows.append(row)
        db.flush()
        return version, rows
    finally:
        shutil.rmtree(staging, ignore_errors=True)
