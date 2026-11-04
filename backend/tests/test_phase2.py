from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from docx import Document
from reportlab.pdfgen.canvas import Canvas
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Job, JobSource, ProfessionalProfile
from app.migrations import upgrade_schema
from app.models import CVUpload
from app.services.cv_parser import extract_text, parse_cv
from app.services.cv_storage import store_cv
from app.services.documents import build_documents, generate_files
from app.services.job_sources import (_ashby_items, _career_page_items, _greenhouse_items, _lever_items,
                                      normalize_job_url, validate_public_https_url)
from app.services.matcher import score_job


def test_cv_parser_pdf_and_docx_are_review_drafts():
    source = """Patrick Example\nPort Harcourt, Nigeria\npatrick@example.com\n\nEXPERIENCE\nDevOps Engineer | Example Ltd | 2021 - Present\nManaged AWS, Terraform and Kubernetes infrastructure.\n\nEDUCATION\nBachelor of Technology, Electrical Engineering\n\nSKILLS\nAWS, Terraform, Kubernetes, Python\n"""
    document = Document()
    for line in source.splitlines():
        document.add_paragraph(line)
    docx_buffer = io.BytesIO()
    document.save(docx_buffer)
    kind, text = extract_text(docx_buffer.getvalue(), "cv.docx")
    draft = parse_cv(text)
    assert kind == "docx"
    assert draft["review_required"] is True
    assert draft["draft"]["verification_status"] == "unverified"
    assert "Terraform" in draft["draft"]["skills"]
    assert draft["draft"]["contact_details"]["email"] == "patrick@example.com"
    assert draft["draft"]["employment_history"][0]["raw_text"].startswith("DevOps Engineer")

    pdf = io.BytesIO()
    canvas = Canvas(pdf)
    canvas.drawString(72, 720, "EXPERIENCE")
    canvas.drawString(72, 700, "AWS Terraform DevOps Engineer")
    canvas.save()
    assert extract_text(pdf.getvalue(), "cv.pdf")[0] == "pdf"
    with pytest.raises(ValueError):
        extract_text(b"not a pdf", "cv.pdf")


def test_cv_storage_preserves_original_and_deduplicates(tmp_path, monkeypatch):
    import app.services.cv_storage as storage

    settings = SimpleNamespace(data_directory=tmp_path, max_cv_upload_bytes=10 * 1024 * 1024)
    monkeypatch.setattr(storage, "get_settings", lambda: settings)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    document = Document()
    document.add_paragraph("Engineer")
    document.add_paragraph("EXPERIENCE")
    document.add_paragraph("AWS Terraform")
    content = io.BytesIO(); document.save(content)
    with Session(engine) as db:
        row, duplicate = store_cv(content.getvalue(), "original.docx", db)
        original = Path(row.storage_path)
        assert not duplicate and original.is_file()
        assert row.extraction_status == "complete"
        assert row.extracted_data["draft"]["verification_status"] == "unverified"
        first_bytes = original.read_bytes()
        repeated, duplicate = store_cv(content.getvalue(), "renamed-copy.docx", db)
        assert duplicate and repeated.id == row.id and original.read_bytes() == first_bytes
        assert db.query(CVUpload).count() == 1
    engine.dispose()


def test_source_mapping_canonicalization_and_explainable_score():
    gh_source = JobSource(id=1, name="Acme Greenhouse", kind="greenhouse", board_slug="acme")
    lever_source = JobSource(id=2, name="Acme Lever", kind="lever", board_slug="acme")
    greenhouse = _greenhouse_items({"jobs": [{"id": 123, "title": "DevOps Engineer", "company_name": "Acme", "location": {"name": "Remote"}, "absolute_url": "https://boards.greenhouse.io/acme/jobs/123?gh_src=abc", "content": "AWS Terraform Kubernetes CI/CD", "updated_at": "2026-01-01T00:00:00Z"}]}, gh_source)
    lever = _lever_items([{"id": "abc", "text": "Cloud Engineer", "hostedUrl": "https://jobs.lever.co/acme/abc", "categories": {"location": "Remote"}, "descriptionPlain": "AWS Terraform"}], lever_source)
    assert len(greenhouse) == 1 and greenhouse[0]["source_identifier"] == "123"
    assert len(lever) == 1 and lever[0]["title"] == "Cloud Engineer"
    assert normalize_job_url("https://example.org/job/1?utm_source=feed#section") == "https://example.org/job/1"

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        profile = ProfessionalProfile(id=1, skills=["AWS", "Terraform", "Kubernetes"], target_titles=["DevOps Engineer"], verification_status="verified", location="Port Harcourt, Nigeria", experience_summary="DevOps experience")
        job = Job(title="DevOps Engineer", company="Acme", source_url="https://example.org/job/1", description="AWS Terraform Kubernetes CI/CD infrastructure automation", required_skills=["AWS", "Terraform", "Kubernetes"], work_mode="remote")
        score, report = score_job(job, profile)
        assert 0 <= score <= 100
        assert "not an employer ATS score" in report["estimate_disclaimer"]
        assert set(["technical_skills", "relevant_experience", "responsibilities", "seniority", "location", "qualifications"]).issubset(report["factors"])
        assert report["matching_skills"]
    engine.dispose()


def test_ashby_and_career_page_jsonld_mapping_and_public_url_validation():
    ashby = JobSource(id=9, name="Example Ashby", kind="ashby", board_slug="example")
    mapped = _ashby_items({"jobs": [
        {"id": "listed", "isListed": True, "title": "Platform Engineer", "jobUrl": "https://jobs.ashbyhq.com/example/123",
         "applyUrl": "https://jobs.ashbyhq.com/example/123/application", "location": "Remote - US",
         "workplaceType": "Hybrid", "publishedAt": "2026-10-30T00:00:00Z", "employmentType": "FullTime",
         "descriptionPlain": "Required: AWS Terraform Kubernetes", "compensation": {"summaryComponents": [
             {"compensationType": "Salary", "currencyCode": "USD", "minValue": 100000, "maxValue": 140000}]}},
        {"id": "hidden", "isListed": False, "title": "Hidden", "jobUrl": "https://jobs.ashbyhq.com/example/hidden"},
    ]}, ashby)
    assert len(mapped) == 1
    assert mapped[0]["work_mode"] == "hybrid" and mapped[0]["salary_min"] == 100000
    assert mapped[0]["company"] == "Example Ashby" and mapped[0]["posted_at"] is not None
    assert mapped[0]["required_skills"] == ["AWS", "Terraform", "Kubernetes"]

    page = JobSource(id=10, name="Configured Company", kind="career_page", page_url="https://careers.example.org/jobs")
    html = b'''<html><script type="application/ld+json">{"@context":"https://schema.org","@graph":[
    {"@type":"Organization","name":"Configured Company"},
    {"@type":"JobPosting","title":"Cloud Engineer","url":"https://careers.example.org/cloud",
    "identifier":{"value":"cloud-1"},"hiringOrganization":{"name":"Actual Company"},
    "jobLocationType":"TELECOMMUTE","datePosted":"2026-10-31","validThrough":"2026-11-30",
    "employmentType":"FULL_TIME","baseSalary":{"currency":"USD","value":{"minValue":90000,"maxValue":120000}},
    "description":"<p>Required: AWS Terraform</p>"}]}</script><div>ignored text</div></html>'''
    career = _career_page_items(html, page)
    assert len(career) == 1 and career[0]["title"] == "Cloud Engineer"
    assert career[0]["company"] == "Actual Company" and career[0]["work_mode"] == "remote"
    assert career[0]["expires_at"] is not None and career[0]["salary_max"] == 120000
    assert validate_public_https_url("https://careers.example.org/jobs") == "https://careers.example.org/jobs"
    for url in ("http://example.org/jobs", "https://localhost/jobs", "https://127.0.0.1/jobs", "https://user:pass@example.org/jobs", "https://example.org:8443/jobs"):
        with pytest.raises(ValueError):
            validate_public_https_url(url)


def test_document_generation_verified_only_and_versioned(tmp_path, monkeypatch):
    import app.services.documents as documents

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(documents, "get_settings", lambda: SimpleNamespace(applications_directory=tmp_path))
    with Session(engine) as db:
        job = Job(title="Cloud Engineer", company="Acme / Cloud", source_url="https://example.org/jobs/cloud", description="AWS Terraform", match_report={"matching_skills": ["AWS"]})
        profile = ProfessionalProfile(id=1, headline="Cloud Engineer", experience_summary="Verified summary", location="Nigeria", skills=["AWS", "Terraform"], verification_status="unverified")
        db.add_all([job, profile]); db.commit(); db.refresh(job)
        with pytest.raises(ValueError, match="Verify"):
            build_documents(job, profile)
        profile.verification_status = "verified"
        version, rows = generate_files(job, profile, db)
        db.commit()
        assert version == 1 and len(rows) == 6
        folder = tmp_path / "Acme-Cloud-Cloud-Engineer"
        assert (folder / "Tailored-CV.docx").is_file()
        assert (folder / "Tailored-CV.pdf").read_bytes().startswith(b"%PDF")
        assert "Verified summary" in build_documents(job, profile)["cv"]
        profile.experience_summary = "Updated verified summary"
        second, _ = generate_files(job, profile, db); db.commit()
        assert second == 2
        assert (folder / "versions" / "v001" / "Tailored-CV.docx").is_file()
    engine.dispose()


def test_additive_migration_keeps_legacy_rows():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE professional_profiles (id INTEGER PRIMARY KEY, headline VARCHAR(240), experience_summary VARCHAR(500), location VARCHAR(200), skills JSON, projects JSON, target_titles JSON, preferences JSON, verification_status VARCHAR(40), source_note TEXT, updated_at DATETIME)")
        connection.exec_driver_sql("INSERT INTO professional_profiles (id, headline, experience_summary, location, skills, projects, target_titles, preferences, verification_status, source_note) VALUES (1, 'My legacy title', 'legacy summary', 'Port Harcourt', '[]', '[]', '[]', '{}', 'unverified', 'keep this')")
        connection.exec_driver_sql("CREATE TABLE jobs (id INTEGER PRIMARY KEY, title VARCHAR(240), company VARCHAR(240), location VARCHAR(240), work_mode VARCHAR(30), description TEXT, source_url VARCHAR(2048), application_url VARCHAR(2048), source_name VARCHAR(120), status VARCHAR(40), discovered_at DATETIME, posted_at DATETIME, expires_at DATETIME, match_score INTEGER, match_report JSON)")
        connection.exec_driver_sql("CREATE TABLE applications (id INTEGER PRIMARY KEY, job_id INTEGER, status VARCHAR(40), notes TEXT, approval_required BOOLEAN, submitted_at DATETIME, confirmation_evidence TEXT)")
    upgrade_schema(engine)
    upgrade_schema(engine)
    with engine.connect() as connection:
        row = connection.exec_driver_sql("SELECT headline, experience_summary, verification_status, source_note, education FROM professional_profiles WHERE id=1").one()
        assert row[:4] == ("My legacy title", "legacy summary", "unverified", "keep this")
        assert row[4] == "[]"
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM schema_migrations WHERE version=1").scalar_one() == 1
    engine.dispose()


def test_source_page_url_migration_preserves_configured_sources():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        connection.exec_driver_sql("INSERT INTO schema_migrations(version) VALUES (1)")
        connection.exec_driver_sql("CREATE TABLE job_sources (id INTEGER PRIMARY KEY, name VARCHAR(160), kind VARCHAR(40), board_slug VARCHAR(160), feed_url VARCHAR(2048), active BOOLEAN, created_at DATETIME)")
        connection.exec_driver_sql("INSERT INTO job_sources (id, name, kind, board_slug, feed_url, active) VALUES (12, 'Existing source', 'greenhouse', 'example', NULL, 1)")
    upgrade_schema(engine)
    upgrade_schema(engine)
    with engine.connect() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("job_sources")}
        row = connection.exec_driver_sql("SELECT name, board_slug, page_url FROM job_sources WHERE id=12").one()
        assert "page_url" in columns and row == ("Existing source", "example", None)
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM schema_migrations WHERE version=3").scalar_one() == 1
    engine.dispose()


