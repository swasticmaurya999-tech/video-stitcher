"""Background worker + janitor threads.

The worker drains the SQLite queue one job at a time (bounded concurrency = protection for a small
box, DESIGN §1). The janitor periodically sweeps expired outputs / orphaned temp. Both start in the
FastAPI lifespan and stop on shutdown.
"""
from __future__ import annotations

import logging
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import settings
from app.db import repo
from app.db.database import init_db
from app.models import JobStatus, Stage
from app.pipeline.orchestrator import JobFailed, run_job
from app.storage import storage

log = logging.getLogger("worker")

_stop = threading.Event()
_threads: list[threading.Thread] = []


def _process(job_id: str) -> None:
    try:
        run_job(job_id)
        log.info("job %s completed", job_id)
    except JobFailed as e:  # terminal — bad input, do not retry
        repo.update_job(job_id, status=JobStatus.FAILED.value, stage=Stage.DONE.value, error=str(e))
        _safe_temp_cleanup(job_id)
        log.info("job %s failed (terminal): %s", job_id, e)
    except Exception as e:  # unexpected — let retry logic decide
        job = repo.get_job(job_id)
        attempts = job.attempts if job else settings.max_job_attempts
        if attempts >= settings.max_job_attempts:
            repo.update_job(
                job_id, status=JobStatus.FAILED.value, stage=Stage.DONE.value,
                error=f"Generation error: {e}"[:300],
            )
            log.exception("job %s failed (no retries left)", job_id)
        else:
            repo.update_job(job_id, status=JobStatus.QUEUED.value, stage=Stage.QUEUED.value)
            log.warning("job %s errored, requeued (attempt %d): %s", job_id, attempts, e)
        _safe_temp_cleanup(job_id)


def _worker_loop() -> None:
    log.info("worker started")
    while not _stop.is_set():
        try:
            job = repo.claim_next_job()
        except Exception as e:  # pragma: no cover
            log.warning("claim failed: %s", e)
            job = None
        if job is None:
            _stop.wait(1.0)
            continue
        _process(job.id)


def _janitor_loop() -> None:
    while not _stop.is_set():
        try:
            _sweep()
        except Exception as e:  # pragma: no cover
            log.warning("janitor sweep failed: %s", e)
        _stop.wait(settings.janitor_interval_sec)


def _sweep() -> None:
    # Expire old outputs/segments by TTL (best-effort; R2 lifecycle rules are the primary mechanism).
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.output_ttl_h)
    for job in repo.list_jobs(limit=500):
        if job.status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value) and job.updated_at:
            try:
                updated = datetime.fromisoformat(job.updated_at)
            except ValueError:
                continue
            if updated < cutoff and job.output_key:
                storage.delete_prefix(f"outputs/{job.id}")
                storage.delete_prefix(f"segments/{job.id}/")
                repo.update_job(job.id, output_key=None)
    # Sweep orphaned local temp dirs.
    tmp_root = Path(settings.temp_dir)
    if tmp_root.exists():
        for d in tmp_root.iterdir():
            if d.is_dir():
                job = repo.get_job(d.name)
                if job is None or job.status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value):
                    shutil.rmtree(d, ignore_errors=True)


def _safe_temp_cleanup(job_id: str) -> None:
    shutil.rmtree(Path(settings.temp_dir) / job_id, ignore_errors=True)


def start() -> None:
    init_db()
    repo.recover_stuck_jobs(settings.max_job_attempts)  # crash recovery on boot
    _stop.clear()
    for target in (_worker_loop, _janitor_loop):
        t = threading.Thread(target=target, daemon=True)
        t.start()
        _threads.append(t)


def stop() -> None:
    _stop.set()
    for t in _threads:
        t.join(timeout=2.0)
    _threads.clear()
