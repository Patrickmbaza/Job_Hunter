"""Serial source execution and durable per-source search outcomes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Job, JobSearchRun, JobSource, ProfessionalProfile
from app.services.job_sources import fetch_source, normalize_job_url
from app.services.matcher import score_job


async def execute_search_run(run_id: int) -> None:
    """Execute one source run with bounded retry and no per-source parallelism."""
    from app.database import SessionLocal

    with SessionLocal() as db:
        run = db.get(JobSearchRun, run_id)
        if run is None:
            return
        source = db.get(JobSource, run.source_id) if run.source_id else None
        if source is None or not source.active:
            run.status = "failed"
            run.error_summary = "Source is unavailable or disabled"
            run.completed_at = datetime.now(timezone.utc)
            db.commit()
            return
        query = run.query or {}
        try:
            records = None
            for attempt in range(2):
                try:
                    records = await fetch_source(source)
                    break
                except (OSError, asyncio.TimeoutError, httpx.TransportError):
                    if attempt == 0:
                        await asyncio.sleep(0.5)
                        continue
                    raise
            keywords = [str(word).casefold() for word in query.get("keywords", []) if str(word).strip()]
            locations = [str(word).casefold() for word in query.get("locations", []) if str(word).strip()]
            remote_only = bool(query.get("remote_only"))
            candidates = []
            for record in records or []:
                haystack = f"{record.get('title','')} {record.get('company','')} {record.get('description','')}".casefold()
                if keywords and not any(keyword in haystack for keyword in keywords):
                    continue
                location = f"{record.get('location') or ''} {record.get('description','')}".casefold()
                if locations and not any(item in location for item in locations):
                    continue
                if remote_only and record.get("work_mode") != "remote":
                    continue
                candidates.append(record)
            profile = db.get(ProfessionalProfile, 1)
            inserted = 0
            for record in candidates:
                record["source_url"] = normalize_job_url(record["source_url"])
                existing = db.scalar(select(Job.id).where(Job.source_url == record["source_url"]))
                if existing is None and record.get("source_identifier"):
                    existing = db.scalar(select(Job.id).where(
                        Job.source_name == record["source_name"],
                        Job.source_identifier == record["source_identifier"],
                    ))
                if existing is not None:
                    continue
                row = Job(**record)
                if profile:
                    row.match_score, row.match_report = score_job(row, profile)
                try:
                    with db.begin_nested():
                        db.add(row)
                        db.flush()
                    inserted += 1
                except IntegrityError:
                    continue
            run.result_count = len(candidates)
            run.inserted_count = inserted
            run.status = "completed"
            run.error_summary = None
        except Exception as exc:
            run.status = "failed"
            run.error_summary = f"{source.kind} source failed ({type(exc).__name__}); review the source settings and retry."
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
