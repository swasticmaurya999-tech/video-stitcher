"""Core domain types: enums + dataclasses shared across the pipeline.

These are plain data carriers; persistence lives in db/repo and bytes in storage/.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Stage(str, Enum):
    QUEUED = "queued"
    INGEST = "ingest"
    SEGMENT = "segment"
    ANALYZE = "analyze"
    PLAN = "plan"
    ENFORCE = "enforce"
    RENDER = "render"
    UPLOAD = "upload"
    DONE = "done"


class FileState(str, Enum):
    PENDING = "pending"
    USED = "used"
    SKIPPED = "skipped"


# ----------------------------------------------------------------------------- records


@dataclass
class FileRec:
    id: str
    job_id: str
    original_name: str
    stored_key: str
    size_bytes: int
    duration: float | None = None
    status: str = FileState.PENDING.value
    skip_reason: str | None = None


@dataclass
class Segment:
    """A candidate clip drawn from a source file."""

    id: str
    job_id: str
    source_file_id: str
    in_point: float
    out_point: float
    duration: float
    normalized_key: str | None = None
    score: float = 0.0
    tags: list[str] = field(default_factory=list)
    transcript: str = ""
    status: str = FileState.PENDING.value
    # transient (not persisted): local path + per-source absolute mapping
    local_path: str | None = None
    source_path: str | None = None
    words: list[dict] = field(default_factory=list)  # [{word,start,end}]


@dataclass
class Beat:
    """One step of the storyboard, after planning."""

    role: str
    intent: str
    target_seconds: float
    segment_id: str
    in_point: float
    out_point: float


@dataclass
class Plan:
    detected_genre: str = "highlight reel"
    theme: str = ""
    confidence: float = 0.0
    beats: list[Beat] = field(default_factory=list)
    transitions: list[str] = field(default_factory=list)
    music_mood: str = ""
    title_text: str = ""
    cta_text: str = ""
    rationale: str = ""
    planner_used: str = ""


@dataclass
class EDLItem:
    """Final, enforced edit instruction: cut [in,out] of a segment, in order."""

    segment_id: str
    source_path: str
    in_point: float
    out_point: float
    duration: float
    transition_in: str = "cut"  # cut | crossfade


@dataclass
class Job:
    id: str
    status: str = JobStatus.QUEUED.value
    stage: str = Stage.QUEUED.value
    progress_cur: int = 0
    progress_total: int = 0
    attempts: int = 0
    target_duration: int = 0
    aspect: str = "16:9"
    brief: str | None = None
    detected_genre: str | None = None
    rationale: str | None = None
    planner_used: str | None = None
    total_uploaded: int = 0
    used_count: int = 0
    skipped_count: int = 0
    output_key: str | None = None
    output_duration: float | None = None
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""
