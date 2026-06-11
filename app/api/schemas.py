"""API response shapes. Single consistent `JobOut` returned by create + status + list."""
from __future__ import annotations

from pydantic import BaseModel

from app.db import repo
from app.models import Job, JobStatus


class Progress(BaseModel):
    stage: str
    current: int
    total: int


class SkippedFile(BaseModel):
    filename: str
    reason: str


class JobOut(BaseModel):
    job_id: str
    status: str
    progress: Progress
    target_duration: int
    aspect: str
    brief: str | None = None
    detected_genre: str | None = None
    rationale: str | None = None
    planner_used: str | None = None
    total_uploaded: int
    used: int
    skipped: int
    skipped_files: list[SkippedFile]
    output_duration: float | None = None
    download_url: str | None = None
    error: str | None = None
    created_at: str
    updated_at: str


def job_to_out(job: Job) -> JobOut:
    skipped = repo.get_skipped(job.id)
    download_url = (
        f"/api/jobs/{job.id}/download"
        if job.status == JobStatus.COMPLETED.value and job.output_key
        else None
    )
    return JobOut(
        job_id=job.id,
        status=job.status,
        progress=Progress(stage=job.stage, current=job.progress_cur, total=job.progress_total),
        target_duration=job.target_duration,
        aspect=job.aspect,
        brief=job.brief,
        detected_genre=job.detected_genre,
        rationale=job.rationale,
        planner_used=job.planner_used,
        total_uploaded=job.total_uploaded,
        used=job.used_count,
        skipped=job.skipped_count,
        skipped_files=[SkippedFile(**s) for s in skipped],
        output_duration=job.output_duration,
        download_url=download_url,
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )
