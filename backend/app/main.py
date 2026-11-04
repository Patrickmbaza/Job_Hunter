from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.parse import urlparse

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    authentication_configured,
    check_login_rate_limit,
    create_csrf_token,
    create_session,
    require_csrf,
    require_same_origin,
    require_session,
    session_valid,
    verify_password,
)
from app.config import get_settings
from app.database import Base, SessionLocal, engine, get_db
from app.integrations.rea import ReaBridgeError, mcp_result_json, rea_service
from app.migrations import upgrade_schema
from app.models import Application, ApplicationHistory, CVUpload, DocumentGenerationRun, GeneratedDocument, Job, JobSearchRun, JobSource, ProfileHistory, ProfessionalProfile, utc_now
from app.profile_seed import EDUCATION, PREFERENCES, PROJECTS, SKILLS, TARGET_TITLES
from app.schemas import ApplicationCreate, ApplicationRead, ApplicationUpdate, CVUploadRead, JobCreate, JobRead, JobSearchRequest, JobSourceCreate, JobSourceRead, JobSourceUpdate, JobStatusUpdate, LoginRequest, ProfileRead, ProfileUpdate, ReaInspectRequest
from app.services.cv_parser import MAX_CV_BYTES
from app.services.cv_storage import profile_snapshot, store_cv
from app.services.discovery import execute_search_run
from app.services.documents import build_documents, generate_files
from app.services.job_sources import normalize_job_url, validate_public_https_url
from app.services.matcher import score_job

settings = get_settings()


def initialize_database() -> None:
    upgrade_schema(engine)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        profile_seed_applied = db.execute(text("SELECT 1 FROM schema_migrations WHERE version = 2")).first() is not None
        if db.get(ProfessionalProfile, 1) is None:
            db.add(ProfessionalProfile(
                id=1,
                skills=SKILLS,
                projects=PROJECTS,
                target_titles=TARGET_TITLES,
                education=EDUCATION,
                preferences=PREFERENCES,
            ))
            db.commit()
        elif not profile_seed_applied:
            profile = db.get(ProfessionalProfile, 1)
            changes = {}
            for field, seeded in (("education", EDUCATION), ("skills", SKILLS), ("target_titles", TARGET_TITLES), ("projects", PROJECTS)):
                current = list(getattr(profile, field) or [])
                merged = current + [item for item in seeded if item not in current]
                if merged != current:
                    changes[field] = merged
            if changes:
                db.add(ProfileHistory(profile_id=profile.id, reason="user_provided_phase2_seed", snapshot=profile_snapshot(profile)))
                for field, value in changes.items():
                    setattr(profile, field, value)
                profile.updated_at = utc_now()
                db.commit()
        if not profile_seed_applied:
            db.execute(text("INSERT OR IGNORE INTO schema_migrations(version) VALUES (2)"))
            db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    try:
        yield
    finally:
        await rea_service.close()


app = FastAPI(
    title="Personal AI Job Hunter",
    version="0.2.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)


def _set_auth_cookie(response: Response, name: str, value: str, *, httponly: bool, max_age: int) -> None:
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        httponly=httponly,
        secure=False,  # Loopback HTTP only; switch to HTTPS termination before any TLS deployment.
        samesite="strict",
        path="/api",
    )


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {
        "status": "ok",
        "database": "ok",
        "mode": "local",
        "version": app.version,
        "authentication_configured": authentication_configured(),
    }


@app.get("/api/auth/session")
def auth_session(request: Request, response: Response):
    configured = authentication_configured()
    token = request.cookies.get(SESSION_COOKIE)
    authenticated = configured and session_valid(token)
    csrf_token = None
    if authenticated and token:
        existing = request.cookies.get(CSRF_COOKIE)
        # Rotate if absent or malformed; no session token is exposed to JS.
        csrf_token = existing if existing and request.headers.get("x-csrf-token") == existing else None
        if csrf_token is None:
            csrf_token = create_csrf_token(token)
            _set_auth_cookie(response, CSRF_COOKIE, csrf_token, httponly=False, max_age=settings.session_ttl_seconds)
    return {"configured": configured, "authenticated": bool(authenticated), "csrf_token": csrf_token}


@app.post("/api/auth/login")
def login(payload: LoginRequest, request: Request, response: Response):
    require_same_origin(request)
    if not authentication_configured():
        raise HTTPException(status_code=503, detail="Set APP_PASSWORD_HASH before signing in")
    client_id = request.client.host if request.client else "unknown"
    check_login_rate_limit(client_id)
    if not verify_password(payload.password.get_secret_value(), settings.app_password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_session()
    csrf_token = create_csrf_token(token)
    _set_auth_cookie(response, SESSION_COOKIE, token, httponly=True, max_age=settings.session_ttl_seconds)
    _set_auth_cookie(response, CSRF_COOKIE, csrf_token, httponly=False, max_age=settings.session_ttl_seconds)
    return {"authenticated": True, "csrf_token": csrf_token, "expires_in": settings.session_ttl_seconds}


@app.post("/api/auth/logout", dependencies=[Depends(require_csrf)])
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/api", httponly=True, samesite="strict")
    response.delete_cookie(CSRF_COOKIE, path="/api", httponly=False, samesite="strict")
    return {"authenticated": False}


@app.get("/api/dashboard", dependencies=[Depends(require_session)])
def dashboard(db: Session = Depends(get_db)):
    counts = {status: count for status, count in db.execute(
        select(Job.status, func.count(Job.id)).group_by(Job.status)
    ).all()}
    return {
        "jobs_discovered": sum(counts.values()),
        "high_match_jobs": db.scalar(select(func.count(Job.id)).where(Job.match_score >= 85)) or 0,
        "jobs_matched": db.scalar(select(func.count(Job.id)).where(Job.match_score.is_not(None))) or 0,
        "jobs_saved": db.scalar(select(func.count(Job.id)).where(Job.status == "Saved")) or 0,
        "applications_prepared": db.scalar(select(func.count(Application.id)).where(Application.status.in_(["Prepared", "Approved"]))) or 0,
        "applications_submitted": db.scalar(select(func.count(Application.id)).where(Application.status == "Applied")) or 0,
        "interviews": db.scalar(select(func.count(Application.id)).where(Application.status == "Interview")) or 0,
        "offers": db.scalar(select(func.count(Application.id)).where(Application.status == "Offer")) or 0,
        "rejections": db.scalar(select(func.count(Application.id)).where(Application.status == "Rejected")) or 0,
        "pending_follow_ups": db.scalar(select(func.count(Application.id)).where(Application.follow_up_at.is_not(None), Application.follow_up_at <= utc_now())) or 0,
        "recent_jobs": [JobRead.model_validate(row).model_dump(mode="json") for row in db.scalars(select(Job).order_by(Job.discovered_at.desc()).limit(5))],
        "profile_verification": db.scalar(select(ProfessionalProfile.verification_status).where(ProfessionalProfile.id == 1)) or "unverified",
    }


@app.get("/api/profile", response_model=ProfileRead, dependencies=[Depends(require_session)])
def read_profile(db: Session = Depends(get_db)):
    profile = db.get(ProfessionalProfile, 1)
    if profile is None:
        raise HTTPException(status_code=503, detail="Profile initialization is incomplete")
    return profile


@app.put("/api/profile", response_model=ProfileRead, dependencies=[Depends(require_csrf)])
def update_profile(payload: ProfileUpdate, db: Session = Depends(get_db)):
    profile = db.get(ProfessionalProfile, 1)
    if profile is None:
        raise HTTPException(status_code=503, detail="Profile initialization is incomplete")
    before = profile_snapshot(profile)
    values = payload.model_dump()
    after = {**before, **values}
    if before != after:
        db.add(ProfileHistory(
            profile_id=profile.id,
            source_cv_id=profile.source_cv_id,
            reason="profile_edit",
            snapshot=before,
        ))
    for field, value in values.items():
        setattr(profile, field, value)
    profile.updated_at = utc_now()
    db.commit()
    db.refresh(profile)
    return profile


@app.post("/api/cv", status_code=201, dependencies=[Depends(require_csrf)])
async def upload_cv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    filename = file.filename or "uploaded-cv"
    data = await file.read(min(MAX_CV_BYTES, settings.max_cv_upload_bytes) + 1)
    await file.close()
    try:
        row, duplicate = store_cv(data, filename, db)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {
        "cv": CVUploadRead.model_validate(row),
        "duplicate": duplicate,
        "draft": row.extracted_data.get("draft") if row.extraction_status == "complete" else None,
        "missing_information": row.extracted_data.get("missing_information", []) if row.extraction_status == "complete" else [],
    }


@app.get("/api/cv", response_model=list[CVUploadRead], dependencies=[Depends(require_session)])
def list_cv_uploads(db: Session = Depends(get_db)):
    return list(db.scalars(select(CVUpload).order_by(CVUpload.uploaded_at.desc()).limit(50)))


@app.get("/api/cv/{cv_id}", dependencies=[Depends(require_session)])
def read_cv_upload(cv_id: int, db: Session = Depends(get_db)):
    row = db.get(CVUpload, cv_id)
    if row is None:
        raise HTTPException(status_code=404, detail="CV record was not found")
    return {"cv": CVUploadRead.model_validate(row), "extracted_data": row.extracted_data}


@app.get("/api/cv/{cv_id}/original", dependencies=[Depends(require_session)])
def download_original_cv(cv_id: int, db: Session = Depends(get_db)):
    row = db.get(CVUpload, cv_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Stored CV was not found")
    path = Path(row.storage_path).resolve()
    root = (settings.data_directory / "cv_originals").resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Stored CV was not found")
    return FileResponse(
        path,
        media_type="application/pdf" if row.document_type == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=row.original_filename,
    )


@app.post("/api/cv/{cv_id}/apply-profile", dependencies=[Depends(require_csrf)])
def apply_cv_profile(cv_id: int, payload: ProfileUpdate, db: Session = Depends(get_db)):
    cv = db.get(CVUpload, cv_id)
    if cv is None or cv.extraction_status != "complete":
        raise HTTPException(status_code=404, detail="A successfully parsed CV is required")
    profile = db.get(ProfessionalProfile, 1)
    if profile is None:
        raise HTTPException(status_code=503, detail="Profile initialization is incomplete")
    previous = profile_snapshot(profile)
    db.add(ProfileHistory(profile_id=profile.id, source_cv_id=cv.id, reason="cv_import_reviewed", snapshot=previous))
    values = payload.model_dump()
    values["verification_status"] = "in_review"
    for field, value in values.items():
        setattr(profile, field, value)
    profile.source_cv_id = cv.id
    profile.source_note = f"Imported from CV #{cv.id}; review is required against the preserved original before verification."
    profile.updated_at = utc_now()
    db.commit()
    db.refresh(profile)
    return profile


@app.get("/api/profile/history", dependencies=[Depends(require_session)])
def profile_history(db: Session = Depends(get_db)):
    rows = db.scalars(select(ProfileHistory).order_by(ProfileHistory.changed_at.desc()).limit(100))
    return [{"id": row.id, "changed_at": row.changed_at, "reason": row.reason, "source_cv_id": row.source_cv_id, "snapshot": row.snapshot} for row in rows]


@app.get("/api/jobs", response_model=list[JobRead], dependencies=[Depends(require_session)])
def list_jobs(
    search: str | None = Query(default=None, max_length=100),
    title: str | None = Query(default=None, max_length=100),
    company: str | None = Query(default=None, max_length=100),
    location: str | None = Query(default=None, max_length=100),
    status: str | None = Query(default=None, max_length=40),
    work_mode: str | None = Query(default=None, pattern="^(remote|hybrid|on-site|unknown)$"),
    source: str | None = Query(default=None, max_length=120),
    match_min: int | None = Query(default=None, ge=0, le=100),
    match_max: int | None = Query(default=None, ge=0, le=100),
    salary_min: float | None = Query(default=None, ge=0),
    posted_after: datetime | None = None,
    sort: str = Query(default="match", pattern="^(match|newest|discovered)$"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    query = select(Job)
    if search:
        term = f"%{search.strip()}%"
        query = query.where(Job.title.ilike(term) | Job.company.ilike(term))
    if title:
        query = query.where(Job.title.ilike(f"%{title.strip()}%"))
    if company:
        query = query.where(Job.company.ilike(f"%{company.strip()}%"))
    if location:
        query = query.where(Job.location.ilike(f"%{location.strip()}%"))
    if status:
        query = query.where(Job.status == status)
    if work_mode:
        query = query.where(Job.work_mode == work_mode)
    if source:
        query = query.where(Job.source_name.ilike(f"%{source.strip()}%"))
    if match_min is not None:
        query = query.where(Job.match_score >= match_min)
    if match_max is not None:
        query = query.where(Job.match_score <= match_max)
    if salary_min is not None:
        query = query.where(Job.salary_max >= salary_min)
    if posted_after:
        query = query.where(Job.posted_at >= posted_after)
    ordering = Job.match_score.desc().nullslast() if sort == "match" else Job.posted_at.desc().nullslast() if sort == "newest" else Job.discovered_at.desc()
    rows = list(db.scalars(query.order_by(ordering, Job.discovered_at.desc()).limit(limit).offset(offset)))
    now = utc_now()
    output = []
    for row in rows:
        read = JobRead.model_validate(row)
        if row.expires_at and row.expires_at <= now:
            read = read.model_copy(update={"status": "Expired"})
        output.append(read)
    return output


@app.post("/api/jobs", response_model=JobRead, status_code=201, dependencies=[Depends(require_csrf)])
def create_job(payload: JobCreate, db: Session = Depends(get_db)):
    values = payload.model_dump(mode="json")
    # JSON mode stringifies datetimes for response serialization; the ORM expects
    # native datetime objects for SQLite DateTime columns.
    values["posted_at"] = payload.posted_at
    values["expires_at"] = payload.expires_at
    try:
        values["source_url"] = normalize_job_url(str(values["source_url"]))
        if values.get("application_url"):
            values["application_url"] = normalize_job_url(str(values["application_url"]))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    duplicate = db.scalar(select(Job.id).where(Job.source_url == values["source_url"]))
    if duplicate is None and values.get("source_identifier"):
        duplicate = db.scalar(select(Job.id).where(Job.source_name == values["source_name"], Job.source_identifier == values["source_identifier"]))
    if duplicate:
        raise HTTPException(status_code=409, detail="This job is already saved")
    job = Job(**values)
    profile = db.get(ProfessionalProfile, 1)
    if profile:
        job.match_score, job.match_report = score_job(job, profile)
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A job with this source URL is already saved") from None
    db.refresh(job)
    return job


@app.get("/api/jobs/{job_id}", response_model=JobRead, dependencies=[Depends(require_session)])
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job was not found")
    read = JobRead.model_validate(job)
    if job.expires_at and job.expires_at <= utc_now():
        read = read.model_copy(update={"status": "Expired"})
    return read


@app.patch("/api/jobs/{job_id}/status", response_model=JobRead, dependencies=[Depends(require_csrf)])
def update_job_status(job_id: int, payload: JobStatusUpdate, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job was not found")
    job.status = payload.status
    job.updated_at = utc_now()
    db.commit()
    db.refresh(job)
    return job


@app.post("/api/jobs/{job_id}/match", dependencies=[Depends(require_csrf)])
def match_one_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    profile = db.get(ProfessionalProfile, 1)
    if job is None or profile is None:
        raise HTTPException(status_code=404, detail="Job or profile was not found")
    job.match_score, job.match_report = score_job(job, profile)
    if job.status == "Discovered":
        job.status = "Matched"
    db.commit()
    return {"job_id": job.id, "match_score": job.match_score, "match_report": job.match_report}


@app.post("/api/matches/recalculate", dependencies=[Depends(require_csrf)])
def recalculate_matches(db: Session = Depends(get_db)):
    profile = db.get(ProfessionalProfile, 1)
    if profile is None:
        raise HTTPException(status_code=503, detail="Profile initialization is incomplete")
    count = 0
    for job in db.scalars(select(Job)):
        job.match_score, job.match_report = score_job(job, profile)
        if job.status == "Discovered":
            job.status = "Matched"
        count += 1
    db.commit()
    return {"updated": count, "estimate_only": True}


@app.get("/api/job-sources/capabilities", dependencies=[Depends(require_session)])
def job_source_capabilities():
    return [
        {"kind": "greenhouse", "available": True, "description": "Public Greenhouse Job Board GET API; configure the public board slug."},
        {"kind": "lever", "available": True, "description": "Public Lever Postings GET API; configure a company site slug."},
        {"kind": "ashby", "available": True, "description": "Credential-free Ashby public job postings API; configure the public job board name."},
        {"kind": "rss", "available": True, "description": "Public HTTPS RSS/Atom feeds, including remote job boards and company feeds."},
        {"kind": "career_page", "available": True, "description": "Fetch one explicitly configured public HTTPS careers URL and import Schema.org JobPosting JSON-LD only; no crawling."},
        {"kind": "manual_url", "available": True, "description": "Paste a job URL and its description; import details from a currently open page with REA."},
        {"kind": "linkedin", "available": False, "description": "No general public search API is configured. Use an authorized LinkedIn interface and manual/REA review; no login scraping."},
        {"kind": "indeed", "available": False, "description": "No permitted public search API is configured. Submit a public job URL manually; no login scraping."},
    ]


@app.get("/api/job-sources", response_model=list[JobSourceRead], dependencies=[Depends(require_session)])
def list_job_sources(db: Session = Depends(get_db)):
    return list(db.scalars(select(JobSource).order_by(JobSource.created_at.desc()).limit(100)))


@app.post("/api/job-sources", response_model=JobSourceRead, status_code=201, dependencies=[Depends(require_csrf)])
def create_job_source(payload: JobSourceCreate, db: Session = Depends(get_db)):
    if payload.kind in {"greenhouse", "lever", "ashby"} and not payload.board_slug:
        raise HTTPException(status_code=422, detail="A public job board slug is required")
    if payload.kind in {"rss", "career_page"}:
        url = payload.feed_url if payload.kind == "rss" else payload.page_url
        if not url:
            label = "feed" if payload.kind == "rss" else "career page"
            raise HTTPException(status_code=422, detail=f"An HTTPS {label} URL is required")
        try:
            validate_public_https_url(url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
    payload = payload.model_copy(update={
        "board_slug": payload.board_slug if payload.kind in {"greenhouse", "lever", "ashby"} else None,
        "feed_url": payload.feed_url if payload.kind == "rss" else None,
        "page_url": payload.page_url if payload.kind == "career_page" else None,
    })
    query = select(JobSource.id).where(JobSource.kind == payload.kind)
    if payload.kind == "rss":
        query = query.where(JobSource.feed_url == payload.feed_url)
    elif payload.kind == "career_page":
        query = query.where(JobSource.page_url == payload.page_url)
    else:
        query = query.where(JobSource.board_slug == payload.board_slug)
    if db.scalar(query):
        raise HTTPException(status_code=409, detail="This source is already configured")
    if db.scalar(select(func.count(JobSource.id))) >= 20:
        raise HTTPException(status_code=409, detail="The local source limit is 20")
    row = JobSource(**payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@app.patch("/api/job-sources/{source_id}", response_model=JobSourceRead, dependencies=[Depends(require_csrf)])
def update_job_source(source_id: int, payload: JobSourceUpdate, db: Session = Depends(get_db)):
    row = db.get(JobSource, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job source was not found")
    row.active = payload.active
    db.commit()
    db.refresh(row)
    return row


@app.post("/api/job-searches", status_code=202, dependencies=[Depends(require_csrf)])
def start_job_search(payload: JobSearchRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    query = payload.model_dump()
    if payload.source_ids:
        sources = list(db.scalars(select(JobSource).where(JobSource.id.in_(payload.source_ids), JobSource.active.is_(True))))
        if len(sources) != len(set(payload.source_ids)):
            raise HTTPException(status_code=422, detail="One or more selected sources are unavailable or disabled")
    else:
        sources = list(db.scalars(select(JobSource).where(JobSource.active.is_(True)).order_by(JobSource.id).limit(20)))
    if not sources:
        raise HTTPException(status_code=422, detail="Configure or select at least one active public source")
    runs = []
    for source in sources:
        run = JobSearchRun(source_id=source.id, query=query)
        db.add(run)
        db.flush()
        runs.append({"id": run.id, "source_id": source.id, "source_name": source.name, "status": run.status})
    db.commit()
    for item in runs:
        background_tasks.add_task(execute_search_run, item["id"])
    return {"runs": runs, "message": "Search queued locally; poll /api/job-searches for source outcomes."}


@app.get("/api/job-searches", dependencies=[Depends(require_session)])
def list_job_searches(limit: int = Query(default=50, ge=1, le=100), db: Session = Depends(get_db)):
    runs = db.scalars(select(JobSearchRun).order_by(JobSearchRun.started_at.desc()).limit(limit))
    return [{"id": run.id, "source_id": run.source_id, "query": run.query,
             "started_at": run.started_at, "completed_at": run.completed_at,
             "result_count": run.result_count, "inserted_count": run.inserted_count,
             "status": run.status, "error_summary": run.error_summary} for run in runs]


@app.post("/api/applications", response_model=ApplicationRead, status_code=201, dependencies=[Depends(require_csrf)])
def create_application(payload: ApplicationCreate, db: Session = Depends(get_db)):
    if db.get(Job, payload.job_id) is None:
        raise HTTPException(status_code=404, detail="Job was not found")
    if db.scalar(select(Application.id).where(Application.job_id == payload.job_id)):
        raise HTTPException(status_code=409, detail="An application tracker already exists for this job")
    row = Application(job_id=payload.job_id, status=payload.status, notes=payload.notes, approval_required=True)
    db.add(row)
    db.flush()
    db.add(ApplicationHistory(application_id=row.id, old_status=None, new_status=row.status, note="Tracker created"))
    db.commit()
    db.refresh(row)
    return row


@app.get("/api/applications", dependencies=[Depends(require_session)])
def list_applications(status: str | None = Query(default=None, max_length=40), db: Session = Depends(get_db)):
    query = select(Application, Job).join(Job, Application.job_id == Job.id).order_by(Application.updated_at.desc())
    if status:
        query = query.where(Application.status == status)
    return [{"application": ApplicationRead.model_validate(application).model_dump(mode="json"),
             "job": JobRead.model_validate(job).model_dump(mode="json")} for application, job in db.execute(query).all()]


@app.patch("/api/applications/{application_id}", response_model=ApplicationRead, dependencies=[Depends(require_csrf)])
def update_application(application_id: int, payload: ApplicationUpdate, db: Session = Depends(get_db)):
    row = db.get(Application, application_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Application tracker was not found")
    values = payload.model_dump(exclude_unset=True)
    new_status = values.get("status")
    if new_status and new_status != row.status:
        db.add(ApplicationHistory(application_id=row.id, old_status=row.status, new_status=new_status,
                                  note="Status changed by the user"))
    for field, value in values.items():
        setattr(row, field, value)
    row.updated_at = utc_now()
    db.commit()
    db.refresh(row)
    return row


@app.get("/api/applications/{application_id}/history", dependencies=[Depends(require_session)])
def application_history(application_id: int, db: Session = Depends(get_db)):
    if db.get(Application, application_id) is None:
        raise HTTPException(status_code=404, detail="Application tracker was not found")
    rows = db.scalars(select(ApplicationHistory).where(ApplicationHistory.application_id == application_id)
                      .order_by(ApplicationHistory.changed_at.desc()).limit(100))
    return [{"id": row.id, "changed_at": row.changed_at, "old_status": row.old_status,
             "new_status": row.new_status, "note": row.note} for row in rows]


def execute_document_generation(run_id: int) -> None:
    with SessionLocal() as db:
        run = db.get(DocumentGenerationRun, run_id)
        if run is None:
            return
        try:
            job = db.get(Job, run.job_id)
            profile = db.get(ProfessionalProfile, 1)
            if job is None or profile is None:
                raise ValueError("Job or profile is unavailable")
            version, _ = generate_files(job, profile, db)
            run.version = version
            run.status = "completed"
            run.completed_at = utc_now()
            db.commit()
        except Exception as exc:
            db.rollback()
            run = db.get(DocumentGenerationRun, run_id)
            if run is not None:
                run.status = "failed"
                run.completed_at = utc_now()
                run.error_summary = type(exc).__name__
                db.commit()


@app.post("/api/jobs/{job_id}/documents/preview", dependencies=[Depends(require_csrf)])
def preview_documents(job_id: int, db: Session = Depends(get_db)):
    job, profile = db.get(Job, job_id), db.get(ProfessionalProfile, 1)
    if job is None:
        raise HTTPException(status_code=404, detail="Job was not found")
    if profile is None:
        raise HTTPException(status_code=503, detail="Profile initialization is incomplete")
    try:
        return {"job_id": job.id, "documents": build_documents(job, profile), "preview_only": True}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@app.post("/api/jobs/{job_id}/documents", status_code=202, dependencies=[Depends(require_csrf)])
def generate_job_documents(job_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    if db.get(Job, job_id) is None:
        raise HTTPException(status_code=404, detail="Job was not found")
    profile = db.get(ProfessionalProfile, 1)
    if profile is None or profile.verification_status != "verified":
        raise HTTPException(status_code=409, detail="Verify the professional profile before generating documents")
    run = DocumentGenerationRun(job_id=job_id)
    db.add(run)
    db.commit()
    background_tasks.add_task(execute_document_generation, run.id)
    return {"id": run.id, "status": run.status}


@app.get("/api/document-runs", dependencies=[Depends(require_session)])
def list_document_runs(limit: int = Query(default=50, ge=1, le=100), db: Session = Depends(get_db)):
    rows = db.scalars(select(DocumentGenerationRun).order_by(DocumentGenerationRun.created_at.desc()).limit(limit))
    return [{"id": row.id, "job_id": row.job_id, "status": row.status, "version": row.version,
             "created_at": row.created_at, "completed_at": row.completed_at,
             "error_summary": row.error_summary} for row in rows]


@app.get("/api/jobs/{job_id}/documents", dependencies=[Depends(require_session)])
def list_job_documents(job_id: int, db: Session = Depends(get_db)):
    if db.get(Job, job_id) is None:
        raise HTTPException(status_code=404, detail="Job was not found")
    rows = db.scalars(select(GeneratedDocument).where(GeneratedDocument.job_id == job_id)
                      .order_by(GeneratedDocument.version.desc(), GeneratedDocument.kind))
    return [{"id": row.id, "kind": row.kind, "version": row.version,
             "created_at": row.created_at, "download_url": f"/api/documents/{row.id}"} for row in rows]


@app.get("/api/documents/{document_id}", dependencies=[Depends(require_session)])
def download_generated_document(document_id: int, db: Session = Depends(get_db)):
    row = db.get(GeneratedDocument, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Document was not found")
    path = Path(row.storage_path).resolve()
    root = settings.applications_directory.resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Stored document was not found")
    return FileResponse(path, filename=path.name)


@app.get("/api/rea/status", dependencies=[Depends(require_session)])
async def rea_status():
    return await rea_service.status()


@app.get("/api/rea/tools", dependencies=[Depends(require_session)])
async def rea_tools():
    try:
        await rea_service.ensure_connected()
    except ReaBridgeError as exc:
        raise HTTPException(status_code=503, detail={"code": exc.code, "message": str(exc)}) from None
    return {"items": rea_service.public_tools()}


def _allowed_origin(origin: str | None) -> str:
    if not settings.allowed_origins:
        raise HTTPException(status_code=503, detail="Configure REA_ALLOWED_ORIGINS before browser inspection")
    if origin is None:
        if len(settings.allowed_origins) != 1:
            raise HTTPException(status_code=422, detail="Select one allow-listed page origin")
        return settings.allowed_origins[0]
    parsed = urlparse(origin)
    normalized = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment or normalized != origin or origin not in settings.allowed_origins:
        raise HTTPException(status_code=403, detail="Page origin is not on the configured REA allow-list")
    return origin


@app.get("/api/rea/targets", dependencies=[Depends(require_session)])
async def rea_targets(origin: str | None = Query(default=None, max_length=512)):
    selected_origin = _allowed_origin(origin) if origin else None
    origins = [selected_origin] if selected_origin else settings.allowed_origins
    try:
        result = await rea_service.discover_targets(origins)
    except ReaBridgeError as exc:
        raise HTTPException(status_code=502, detail={"code": exc.code, "message": str(exc)}) from None
    if getattr(result, "isError", False):
        raise HTTPException(status_code=502, detail="REA browser target discovery returned a tool error")
    return mcp_result_json(result)


@app.post("/api/rea/inspect", dependencies=[Depends(require_csrf)])
async def rea_inspect(payload: ReaInspectRequest):
    selected_origin = _allowed_origin(payload.origin)
    if not rea_service.target_was_discovered(payload.target_id, selected_origin):
        raise HTTPException(status_code=409, detail="Discover this allow-listed target before inspecting it")
    try:
        result = await rea_service.inspect_page(payload.target_id, [selected_origin])
    except ReaBridgeError as exc:
        raise HTTPException(status_code=502, detail={"code": exc.code, "message": str(exc)}) from None
    if getattr(result, "isError", False):
        raise HTTPException(status_code=502, detail="REA page inspection returned a tool error")
    return mcp_result_json(result)


@app.get("/api/application-statuses", dependencies=[Depends(require_session)])
def application_statuses():
    return ["Discovered", "Matched", "Prepared", "Awaiting Approval", "Applied", "Interview", "Offer", "Rejected", "Withdrawn"]
