from __future__ import annotations

import io
import socket
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from docx import Document
from reportlab.pdfgen.canvas import Canvas
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import hash_password
from app.config import get_settings
from app.database import Base, get_db
from app.main import app
from app.models import CVUpload, GeneratedDocument, Job, JobSearchRun, ProfessionalProfile


@pytest.fixture
def isolated_api(tmp_path, monkeypatch):
    from app import main
    from app.services import cv_storage, documents

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as db:
        db.add(ProfessionalProfile(id=1, skills=["AWS", "Terraform"], target_titles=["DevOps Engineer"],
                                   education=["Bachelor of Technology"], experience_summary="4 years DevOps",
                                   location="Nigeria", verification_status="unverified"))
        db.commit()

    def override_db():
        with factory() as db:
            yield db

    password_hash = hash_password("test-only-password-long-enough")
    get_settings().app_password_hash = password_hash
    file_settings = SimpleNamespace(
        data_directory=tmp_path / "data", applications_directory=tmp_path / "Applications",
        max_cv_upload_bytes=10 * 1024 * 1024, app_password_hash=password_hash, session_ttl_seconds=3600,
    )
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main, "settings", file_settings)
    monkeypatch.setattr(main, "SessionLocal", factory)
    monkeypatch.setattr(cv_storage, "get_settings", lambda: file_settings)
    monkeypatch.setattr(documents, "get_settings", lambda: file_settings)
    yield factory
    app.dependency_overrides.clear()
    get_settings().app_password_hash = ""
    engine.dispose()


@pytest.fixture
def api_client(isolated_api):
    # Use an actual loopback HTTP server: the installed Starlette/httpx in-process
    # transports hang before dispatching requests in both supported environments.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off",
                                           log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "Local test API failed to start"
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}")
    yield client
    client.close()
    server.should_exit = True
    thread.join(timeout=10)
    listener.close()


def _docx_cv() -> bytes:
    document = Document()
    for line in (
        "Patrick Example", "Port Harcourt, Nigeria", "patrick@example.com", "EXPERIENCE",
        "DevOps Engineer | Example Ltd | 2021 - Present", "Managed AWS and Terraform infrastructure.",
        "EDUCATION", "Bachelor of Technology in Electrical Engineering", "SKILLS", "AWS, Terraform",
    ):
        document.add_paragraph(line)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def _pdf_cv() -> bytes:
    out = io.BytesIO()
    pdf = Canvas(out)
    pdf.drawString(72, 720, "Patrick Example | DevOps Engineer")
    pdf.drawString(72, 700, "EXPERIENCE")
    pdf.drawString(72, 680, "DevOps Engineer | Example Ltd | 2021 - Present")
    pdf.drawString(72, 660, "Managed AWS and Terraform infrastructure.")
    pdf.save()
    return out.getvalue()


def _logged_in_client(client):
    response = client.post("/api/auth/login", json={"password": "test-only-password-long-enough"},
                           headers={"Origin": get_settings().frontend_origin})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def test_api_auth_and_csrf_protection(api_client):
    client = api_client
    assert client.get("/api/profile").status_code == 401
    assert client.post("/api/auth/login", json={"password": "test-only-password-long-enough"},
                       headers={"Origin": "http://evil.example"}).status_code == 403
    csrf = _logged_in_client(client)
    headers = {"Origin": get_settings().frontend_origin, "X-CSRF-Token": "bad"}
    assert client.post("/api/job-sources", json={}, headers=headers).status_code == 403
    assert client.get("/api/profile").status_code == 200
    assert client.post("/api/auth/logout", headers={"Origin": get_settings().frontend_origin,
                                                     "X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/api/profile").status_code == 401


def test_cv_upload_profile_job_filter_application_and_document_workflow(api_client, monkeypatch):
    client = api_client
    csrf = _logged_in_client(client)
    headers = {"Origin": get_settings().frontend_origin, "X-CSRF-Token": csrf}
    upload = client.post("/api/cv", files={"file": ("patrick.docx", _docx_cv(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}, headers=headers)
    assert upload.status_code == 201
    data = upload.json()
    assert data["draft"]["verification_status"] == "unverified"
    assert "AWS" in data["draft"]["skills"]
    cv_id = data["cv"]["id"]
    original = client.get(f"/api/cv/{cv_id}/original")
    assert original.status_code == 200 and original.content.startswith(b"PK")
    repeat = client.post("/api/cv", files={"file": ("copy.docx", _docx_cv(), "application/octet-stream")}, headers=headers)
    assert repeat.status_code == 201 and repeat.json()["duplicate"] is True
    pdf_upload = client.post("/api/cv", files={"file": ("patrick.pdf", _pdf_cv(), "application/pdf")}, headers=headers)
    assert pdf_upload.status_code == 201
    assert pdf_upload.json()["cv"]["document_type"] == "pdf"
    assert pdf_upload.json()["draft"]["employment_history"]

    profile = client.get("/api/profile").json() | data["draft"]
    profile_payload = {key: profile[key] for key in (
            "headline", "experience_summary", "location", "skills", "projects", "target_titles", "preferences",
            "employment_history", "education", "certifications", "achievements", "contact_details",
            "nysc_completed", "nysc_discharge_certificate", "verification_status",
    )}
    applied = client.post(f"/api/cv/{cv_id}/apply-profile", json=profile_payload, headers=headers)
    assert applied.status_code == 200, applied.text
    assert applied.json()["verification_status"] == "in_review"
    assert client.get("/api/profile/history").json()
    verified_profile = {key: applied.json()[key] for key in profile_payload} | {"verification_status": "verified"}
    assert client.put("/api/profile", json=verified_profile, headers=headers).status_code == 200

    payload = {"title": "DevOps Engineer", "company": "Example Co", "source_url": "https://example.org/jobs/devops",
                   "location": "Nigeria", "work_mode": "remote", "source_name": "Manual",
                   "description": "AWS Terraform CI/CD Kubernetes", "required_skills": ["AWS", "Terraform"],
               "salary_min": 1000, "salary_max": 2000, "posted_at": "2026-10-01T00:00:00Z"}
    created = client.post("/api/jobs", json=payload, headers=headers)
    assert created.status_code == 201
    assert created.json()["match_score"] is not None
    report = created.json()["match_report"]
    assert "AWS" in report["matching_skills"]
    assert "not an employer ATS score" in report["estimate_disclaimer"]
    assert client.post("/api/jobs", json=payload, headers=headers).status_code == 409
    filtered = client.get("/api/jobs?title=DevOps&location=Nigeria&work_mode=remote&salary_min=500&posted_after=2026-09-01T00:00:00Z")
    assert [row["id"] for row in filtered.json()] == [created.json()["id"]]

    from app import main
    def complete_search(run_id):
        with main.SessionLocal() as db:
            row = db.get(JobSearchRun, run_id)
            row.status = "completed"
            row.result_count = 2
            row.inserted_count = 1
            db.commit()
    monkeypatch.setattr(main, "execute_search_run", complete_search)
    source = client.post("/api/job-sources", json={"name": "Example board", "kind": "greenhouse", "board_slug": "example"}, headers=headers)
    assert source.status_code == 201
    ashby = client.post("/api/job-sources", json={"name": "Ashby board", "kind": "ashby", "board_slug": "example-ashby"}, headers=headers)
    assert ashby.status_code == 201 and ashby.json()["page_url"] is None
    career = client.post("/api/job-sources", json={"name": "Company careers", "kind": "career_page", "page_url": "https://careers.example.org/jobs"}, headers=headers)
    assert career.status_code == 201 and career.json()["page_url"] == "https://careers.example.org/jobs"
    assert client.post("/api/job-sources", json={"name": "Duplicate career page", "kind": "career_page", "page_url": "https://careers.example.org/jobs"}, headers=headers).status_code == 409
    assert client.post("/api/job-sources", json={"name": "Unsafe page", "kind": "career_page", "page_url": "http://127.0.0.1/jobs"}, headers=headers).status_code == 422
    queued = client.post("/api/job-searches", json={"keywords": ["DevOps"], "locations": ["Nigeria"], "source_ids": [source.json()["id"]]}, headers=headers)
    assert queued.status_code == 202
    run_id = queued.json()["runs"][0]["id"]
    for _ in range(100):
        run = next(item for item in client.get("/api/job-searches").json() if item["id"] == run_id)
        if run["status"] != "running":
            break
        time.sleep(0.02)
    assert (run["status"], run["result_count"], run["inserted_count"]) == ("completed", 2, 1)

    app_row = client.post("/api/applications", json={"job_id": created.json()["id"], "status": "Saved"}, headers=headers)
    assert app_row.status_code == 201
    application_id = app_row.json()["id"]
    updated = client.patch(f"/api/applications/{application_id}", json={
            "status": "Applied", "applied_at": "2026-10-02T09:00:00", "follow_up_at": "2026-10-09T09:00:00",
            "recruiter_name": "Example Recruiter", "recruiter_email": "recruiter@example.org",
    }, headers=headers)
    assert updated.status_code == 200
    assert updated.json()["recruiter_email"] == "recruiter@example.org"
    assert updated.json()["follow_up_at"].startswith("2026-10-09")
    assert any(row["new_status"] == "Applied" for row in client.get(f"/api/applications/{application_id}/history").json())

    generation = client.post(f"/api/jobs/{created.json()['id']}/documents", headers=headers)
    assert generation.status_code == 202
    for _ in range(100):
        run = client.get("/api/document-runs").json()[0]
        if run["status"] in {"completed", "failed"}:
            break
        time.sleep(0.05)
    assert run["status"] == "completed", run
    docs = client.get(f"/api/jobs/{created.json()['id']}/documents")
    assert {row["kind"] for row in docs.json()} >= {"tailored_cv_docx", "tailored_cv_pdf", "cover_letter_docx", "cover_letter_pdf", "job_description"}
    download = client.get(docs.json()[0]["download_url"])
    assert download.status_code == 200 and len(download.content) > 0
    dashboard = client.get("/api/dashboard").json()
    assert dashboard["jobs_discovered"] == 1
    assert dashboard["jobs_matched"] == 1
    assert dashboard["applications_submitted"] == 1
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    assert client.get(f"/api/cv/{cv_id}/original").status_code == 401
    assert client.get(docs.json()[0]["download_url"]).status_code == 401


def test_cv_invalid_and_oversized_uploads_are_rejected(api_client):
    client = api_client
    headers = {"Origin": get_settings().frontend_origin, "X-CSRF-Token": _logged_in_client(client)}
    invalid = client.post("/api/cv", files={"file": ("cv.txt", b"unsupported", "text/plain")}, headers=headers)
    assert invalid.status_code == 422
    invalid_pdf = client.post("/api/cv", files={"file": ("cv.pdf", b"this is not a PDF", "application/pdf")}, headers=headers)
    assert invalid_pdf.status_code == 422
    oversized = client.post("/api/cv", files={"file": ("cv.docx", b"x" * (10 * 1024 * 1024 + 1), "application/octet-stream")}, headers=headers)
    assert oversized.status_code == 422


def test_original_and_generated_document_downloads_reject_outside_paths(isolated_api, api_client, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    with isolated_api() as db:
        job = Job(title="Role", company="Company", source_url="https://example.org/role")
        db.add(job)
        db.flush()
        cv = CVUpload(original_filename="outside.pdf", storage_path=str(outside), sha256="a" * 64,
                      byte_size=7, document_type="pdf", extraction_status="complete")
        doc = GeneratedDocument(job_id=job.id, kind="job_description", version=1,
                                storage_path=str(outside), sha256="b" * 64)
        db.add_all([cv, doc]); db.commit()
        cv_id, doc_id = cv.id, doc.id

    _logged_in_client(api_client)
    assert api_client.get(f"/api/cv/{cv_id}/original").status_code == 404
    assert api_client.get(f"/api/documents/{doc_id}").status_code == 404
