from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Empty fields are valid during CV review when extraction found no evidence.
    # Document generation still requires explicit verification of the full profile.
    headline: str = Field(max_length=240)
    experience_summary: str = Field(max_length=500)
    location: str = Field(max_length=200)
    skills: list[str] = Field(max_length=100)
    projects: list[str] = Field(max_length=40)
    target_titles: list[str] = Field(max_length=40)
    preferences: dict[str, Any]
    employment_history: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    education: list[str] = Field(default_factory=list, max_length=40)
    certifications: list[str] = Field(default_factory=list, max_length=80)
    achievements: list[str] = Field(default_factory=list, max_length=100)
    contact_details: dict[str, str] = Field(default_factory=dict, max_length=20)
    nysc_completed: bool = True
    nysc_discharge_certificate: bool = True
    verification_status: str = Field(pattern="^(unverified|in_review|verified)$")


class ProfileRead(ProfileUpdate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source_note: str
    updated_at: datetime
    source_cv_id: int | None = None


class JobCreate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    company: str = Field(min_length=1, max_length=240)
    source_url: HttpUrl
    location: str | None = Field(default=None, max_length=240)
    work_mode: str | None = Field(default=None, pattern="^(remote|hybrid|on-site|unknown)$")
    description: str = ""
    application_url: HttpUrl | None = None
    source_name: str = "Manual"
    source_identifier: str | None = Field(default=None, max_length=256)
    employment_type: str | None = Field(default=None, max_length=80)
    salary_min: float | None = Field(default=None, ge=0)
    salary_max: float | None = Field(default=None, ge=0)
    salary_currency: str | None = Field(default=None, min_length=3, max_length=8)
    posted_at: datetime | None = None
    expires_at: datetime | None = None
    required_skills: list[str] = Field(default_factory=list, max_length=100)
    preferred_skills: list[str] = Field(default_factory=list, max_length=100)
    experience_requirements: str | None = Field(default=None, max_length=4000)


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    company: str
    location: str | None
    work_mode: str | None
    source_url: str
    source_name: str
    status: str
    discovered_at: datetime
    match_score: int | None
    description: str = ""
    application_url: str | None = None
    source_identifier: str | None = None
    employment_type: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    experience_requirements: str | None = None
    posted_at: datetime | None = None
    expires_at: datetime | None = None
    match_report: dict[str, Any] = Field(default_factory=dict)


class JobSourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    kind: str = Field(pattern="^(greenhouse|lever|rss)$")
    board_slug: str | None = Field(default=None, min_length=1, max_length=160, pattern="^[A-Za-z0-9_.-]+$")
    feed_url: str | None = Field(default=None, max_length=2048)


class JobSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keywords: list[str] = Field(default_factory=list, max_length=20)
    locations: list[str] = Field(default_factory=list, max_length=20)
    remote_only: bool = False
    source_ids: list[int] = Field(default_factory=list, max_length=20)


class JobStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = Field(pattern="^(Discovered|Matched|Saved|Expired|Closed)$")


class JobSourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: bool


class ApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: int = Field(gt=0)
    status: str = Field(default="Saved", pattern="^(Discovered|Matched|Saved|Prepared|Approved|Applied|Interview|Offer|Rejected)$")
    notes: str = Field(default="", max_length=10000)


class ApplicationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str | None = Field(default=None, pattern="^(Discovered|Matched|Saved|Prepared|Approved|Applied|Interview|Offer|Rejected)$")
    notes: str | None = Field(default=None, max_length=10000)
    applied_at: datetime | None = None
    interview_at: datetime | None = None
    follow_up_at: datetime | None = None
    recruiter_name: str | None = Field(default=None, max_length=240)
    recruiter_email: str | None = Field(default=None, max_length=320)
    recruiter_url: str | None = Field(default=None, max_length=2048)


class CVUploadRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    original_filename: str
    byte_size: int
    document_type: str
    uploaded_at: datetime
    extraction_status: str
    failure_reason: str | None = None


class JobSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    kind: str
    board_slug: str | None
    feed_url: str | None
    active: bool


class ApplicationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    job_id: int
    status: str
    notes: str
    approval_required: bool
    submitted_at: datetime | None
    applied_at: datetime | None
    interview_at: datetime | None
    follow_up_at: datetime | None
    recruiter_name: str | None
    recruiter_email: str | None
    recruiter_url: str | None
    updated_at: datetime


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr = Field(min_length=14, max_length=1024)


class ReaInspectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: str = Field(min_length=1, max_length=256)
    origin: str = Field(min_length=8, max_length=512)
