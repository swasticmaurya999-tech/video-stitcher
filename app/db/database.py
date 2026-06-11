"""SQLite access: WAL mode, thread-local connections, schema bootstrap.

We keep one connection per thread (API thread + worker thread). WAL lets readers and a single
writer proceed concurrently; `busy_timeout` absorbs brief lock contention. The worker is the only
writer of job *state transitions*, which keeps contention low.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from app.config import settings

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    status          TEXT NOT NULL,
    stage           TEXT NOT NULL,
    progress_cur    INTEGER NOT NULL DEFAULT 0,
    progress_total  INTEGER NOT NULL DEFAULT 0,
    attempts        INTEGER NOT NULL DEFAULT 0,
    target_duration INTEGER NOT NULL DEFAULT 0,
    aspect          TEXT NOT NULL DEFAULT '16:9',
    brief           TEXT,
    detected_genre  TEXT,
    rationale       TEXT,
    planner_used    TEXT,
    total_uploaded  INTEGER NOT NULL DEFAULT 0,
    used_count      INTEGER NOT NULL DEFAULT 0,
    skipped_count   INTEGER NOT NULL DEFAULT 0,
    output_key      TEXT,
    output_duration REAL,
    error           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id            TEXT PRIMARY KEY,
    job_id        TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    original_name TEXT NOT NULL,
    stored_key    TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    duration      REAL,
    status        TEXT NOT NULL DEFAULT 'pending',
    skip_reason   TEXT
);

CREATE TABLE IF NOT EXISTS segments (
    id             TEXT PRIMARY KEY,
    job_id         TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    source_file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    in_point       REAL NOT NULL,
    out_point      REAL NOT NULL,
    duration       REAL NOT NULL,
    normalized_key TEXT,
    score          REAL NOT NULL DEFAULT 0,
    tags           TEXT,
    transcript     TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',
    created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status   ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_files_job     ON files(job_id);
CREATE INDEX IF NOT EXISTS idx_segments_job  ON segments(job_id);
"""


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(settings.db_path, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
