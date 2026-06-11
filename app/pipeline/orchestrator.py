"""Runs the full pipeline for one job, stage by stage, with progress + error handling.

Raises on failure; the worker decides retry vs. fail. Pure orchestration — each stage lives in
its own module.
"""
from __future__ import annotations

import logging
import os
import shutil

from app.config import settings
from app.db import repo
from app.models import FileState, JobStatus, Stage
from app.pipeline import analyze, catalog, enforce, ingest, render, segment
from app.pipeline.allocate import compute_target
from app.pipeline.enforce import InsufficientFootage
from app.pipeline.plan.chain import make_plan
from app.storage import storage

log = logging.getLogger("pipeline")


class JobFailed(Exception):
    """A terminal, non-retryable failure (bad input). Worker marks failed without retrying."""


def run_job(job_id: str) -> None:
    job = repo.get_job(job_id)
    if job is None:
        return

    # 1. INGEST + validate
    usable = ingest.ingest(job_id)
    skipped = len(repo.get_skipped(job_id))
    repo.update_job(job_id, skipped_count=skipped)
    if not usable:
        raise JobFailed(f"No usable videos: all {job.total_uploaded} uploads failed validation.")

    # 2. SEGMENT
    segments = segment.segment(job_id, usable)

    # 3. ANALYZE (scores, tags, transcript) — returns top-K candidates
    segments = analyze.analyze(job_id, usable, segments)

    # Target: user-supplied (validated upstream) else computed from #usable videos.
    user_target = job.target_duration or None
    target = compute_target(
        len(usable), settings.clip_seconds, settings.min_output_sec, settings.max_output_sec, user_target
    )

    # 4. PLAN (failover chain → never fails)
    repo.set_progress(job_id, Stage.PLAN.value, 0, 1)
    cat = catalog.build_catalog(segments)
    plan = make_plan(cat, segments, job.brief, target)
    repo.set_progress(job_id, Stage.PLAN.value, 1, 1)

    # 5. ENFORCE → validated EDL with the guaranteed duration
    repo.set_progress(job_id, Stage.ENFORCE.value, 0, 1)
    try:
        edl, final_duration = enforce.build_edl(plan, segments, target)
    except InsufficientFootage as e:
        raise JobFailed(str(e))
    repo.set_progress(job_id, Stage.ENFORCE.value, 1, 1)

    # mark which source files were actually used
    used_files = {s.source_file_id for s in segments if s.id in {e.segment_id for e in edl}}
    for f in usable:
        if f.rec.id in used_files:
            repo.update_file(f.rec.id, status=FileState.USED.value)
    repo.update_job(
        job_id,
        detected_genre=plan.detected_genre,
        rationale=plan.rationale,
        planner_used=plan.planner_used,
        used_count=len(used_files),
    )

    # 6. RENDER → output (brand title + CTA come from the LLM plan; rendered as on-screen text)
    output_key = render.render(job_id, edl, job.aspect, title=plan.title_text, cta=plan.cta_text)

    # 7. DONE
    repo.update_job(
        job_id,
        status=JobStatus.COMPLETED.value,
        stage=Stage.DONE.value,
        output_key=output_key,
        output_duration=final_duration,
    )
    _cleanup(job_id, keep_output=True)


def _cleanup(job_id: str, keep_output: bool) -> None:
    # Proactively drop the raw uploads (only the output/segments matter now).
    try:
        storage.delete_prefix(f"uploads/{job_id}/")
    except Exception as e:  # pragma: no cover
        log.warning("upload cleanup failed: %s", e)
    # Always clear local temp scratch.
    tmp = os.path.join(settings.temp_dir, job_id)
    shutil.rmtree(tmp, ignore_errors=True)
