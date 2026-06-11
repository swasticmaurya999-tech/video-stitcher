"""Stage 1 — INGEST: pull inputs to local temp, probe, and content-validate.

Bad files (corrupt / no video stream / zero duration) are SKIPPED with a reason, not fatal —
the job fails only if zero usable videos remain (DESIGN §3 two-tier policy).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from app.config import settings
from app.db import repo
from app.models import FileRec, FileState
from app.pipeline import ffmpeg
from app.storage import storage


@dataclass
class IngestedFile:
    rec: FileRec
    path: str
    has_audio: bool


def job_temp(job_id: str, *parts: str) -> str:
    p = os.path.join(settings.temp_dir, job_id, *parts)
    os.makedirs(os.path.dirname(p) if os.path.splitext(p)[1] else p, exist_ok=True)
    return p


def ingest(job_id: str) -> list[IngestedFile]:
    files = repo.get_files(job_id)
    usable: list[IngestedFile] = []
    repo.set_progress(job_id, "ingest", 0, len(files))

    for i, f in enumerate(files):
        local = job_temp(job_id, "inputs", os.path.basename(f.stored_key))
        try:
            storage.download(f.stored_key, local)
            info = ffmpeg.probe(local)
        except Exception as e:  # corrupt / unreadable
            repo.update_file(f.id, status=FileState.SKIPPED.value, skip_reason=f"unreadable: {e}"[:200])
            repo.set_progress(job_id, "ingest", i + 1, len(files))
            continue

        if not info.has_video:
            repo.update_file(f.id, status=FileState.SKIPPED.value, skip_reason="no decodable video stream")
        elif info.duration <= 0.05:
            repo.update_file(f.id, status=FileState.SKIPPED.value, skip_reason="zero duration")
        else:
            repo.update_file(f.id, duration=info.duration)
            f.duration = info.duration
            usable.append(IngestedFile(rec=f, path=local, has_audio=info.has_audio))
        repo.set_progress(job_id, "ingest", i + 1, len(files))

    return usable
