"""Data-access layer for jobs / files / segments + the durable job queue.

All SQL lives here; the rest of the app speaks in dataclasses (`app.models`).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.db.database import get_conn
from app.models import FileRec, Job, JobStatus, Segment, Stage


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_from_row(r) -> Job:
    return Job(**{k: r[k] for k in r.keys()})


# ----------------------------------------------------------------------------- jobs


def create_job(job_id: str, target_duration: int, aspect: str, brief: str | None) -> Job:
    conn = get_conn()
    ts = _now()
    conn.execute(
        """INSERT INTO jobs (id, status, stage, target_duration, aspect, brief, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (job_id, JobStatus.QUEUED.value, Stage.QUEUED.value, target_duration, aspect, brief, ts, ts),
    )
    conn.commit()
    return get_job(job_id)


def get_job(job_id: str) -> Job | None:
    row = get_conn().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _job_from_row(row) if row else None


def list_jobs(limit: int = 50) -> list[Job]:
    rows = get_conn().execute(
        "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_job_from_row(r) for r in rows]


def update_job(job_id: str, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k}=?" for k in fields)
    conn = get_conn()
    conn.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))
    conn.commit()


def set_progress(job_id: str, stage: str, cur: int = 0, total: int = 0) -> None:
    update_job(job_id, stage=stage, progress_cur=cur, progress_total=total)


def claim_next_job() -> Job | None:
    """Atomically pick the oldest queued job and mark it processing (single-writer safe)."""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id FROM jobs WHERE status=? ORDER BY created_at LIMIT 1",
            (JobStatus.QUEUED.value,),
        ).fetchone()
        if not row:
            conn.commit()
            return None
        jid = row["id"]
        conn.execute(
            "UPDATE jobs SET status=?, stage=?, attempts=attempts+1, updated_at=? WHERE id=?",
            (JobStatus.PROCESSING.value, Stage.INGEST.value, _now(), jid),
        )
        conn.commit()
        return get_job(jid)
    except Exception:
        conn.rollback()
        raise


def recover_stuck_jobs(max_attempts: int) -> None:
    """On boot, requeue interrupted jobs (or fail them if they've exhausted attempts)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, attempts FROM jobs WHERE status=?", (JobStatus.PROCESSING.value,)
    ).fetchall()
    for r in rows:
        if r["attempts"] >= max_attempts:
            update_job(
                r["id"],
                status=JobStatus.FAILED.value,
                stage=Stage.DONE.value,
                error="Interrupted and exceeded retry attempts.",
            )
        else:
            update_job(r["id"], status=JobStatus.QUEUED.value, stage=Stage.QUEUED.value)


# ----------------------------------------------------------------------------- files


def add_file(f: FileRec) -> None:
    conn = get_conn()
    conn.execute(
        """INSERT INTO files (id, job_id, original_name, stored_key, size_bytes, duration, status, skip_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (f.id, f.job_id, f.original_name, f.stored_key, f.size_bytes, f.duration, f.status, f.skip_reason),
    )
    conn.commit()


def get_files(job_id: str) -> list[FileRec]:
    rows = get_conn().execute("SELECT * FROM files WHERE job_id=?", (job_id,)).fetchall()
    return [FileRec(**{k: r[k] for k in r.keys()}) for r in rows]


def update_file(file_id: str, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    conn = get_conn()
    conn.execute(f"UPDATE files SET {cols} WHERE id=?", (*fields.values(), file_id))
    conn.commit()


def get_skipped(job_id: str) -> list[dict]:
    rows = get_conn().execute(
        "SELECT original_name, skip_reason FROM files WHERE job_id=? AND status='skipped'", (job_id,)
    ).fetchall()
    return [{"filename": r["original_name"], "reason": r["skip_reason"]} for r in rows]


# ----------------------------------------------------------------------------- segments


def add_segment(s: Segment) -> None:
    conn = get_conn()
    conn.execute(
        """INSERT INTO segments
           (id, job_id, source_file_id, in_point, out_point, duration, normalized_key, score, tags, transcript, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            s.id, s.job_id, s.source_file_id, s.in_point, s.out_point, s.duration,
            s.normalized_key, s.score, json.dumps(s.tags), s.transcript, s.status, _now(),
        ),
    )
    conn.commit()


def update_segment(seg_id: str, **fields) -> None:
    if not fields:
        return
    if "tags" in fields and isinstance(fields["tags"], list):
        fields["tags"] = json.dumps(fields["tags"])
    cols = ", ".join(f"{k}=?" for k in fields)
    conn = get_conn()
    conn.execute(f"UPDATE segments SET {cols} WHERE id=?", (*fields.values(), seg_id))
    conn.commit()


def get_segments(job_id: str) -> list[Segment]:
    rows = get_conn().execute("SELECT * FROM segments WHERE job_id=?", (job_id,)).fetchall()
    out = []
    for r in rows:
        d = {k: r[k] for k in r.keys()}
        d["tags"] = json.loads(d["tags"]) if d.get("tags") else []
        d.pop("created_at", None)
        out.append(Segment(**d))
    return out
