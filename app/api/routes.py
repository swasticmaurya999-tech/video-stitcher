"""HTTP routes — upload, status, list, download, health, (local) file serving.

The upload handler does *structural* validation (count, extension, size) while streaming to disk;
content validation (ffprobe) happens later in the pipeline (DESIGN §3 two-tier).
"""
from __future__ import annotations

import os
import shutil
import uuid

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from app import errors
from app.api.schemas import JobOut, job_to_out
from app.config import settings
from app.db import repo
from app.models import FileRec, JobStatus
from app.storage import storage

router = APIRouter()
CHUNK = 1024 * 1024


def _free_mb() -> int:
    try:
        return shutil.disk_usage(settings.temp_dir).free // (1024 * 1024)
    except Exception:
        return settings.disk_min_free_mb + 1


def _stream_to_temp(upload: UploadFile, dest: str, max_file: int, remaining_total: int) -> int:
    """Stream an upload to `dest` in chunks, enforcing per-file and remaining-total caps.

    Returns bytes written. Raises AppError(413) on cap breach (and removes the partial file).
    """
    written = 0
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        while True:
            chunk = upload.file.read(CHUNK)
            if not chunk:
                break
            written += len(chunk)
            if written > max_file:
                f.close()
                os.remove(dest)
                raise errors.payload_too_large(
                    f"'{upload.filename}' exceeds the {settings.max_file_size_mb} MB per-file limit."
                )
            if written > remaining_total:
                f.close()
                os.remove(dest)
                raise errors.payload_too_large(
                    f"Total upload exceeds the {settings.max_total_size_mb} MB limit."
                )
            f.write(chunk)
    return written


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.post("/api/jobs", status_code=202)
async def create_job(
    files: list[UploadFile] = File(...),
    target_duration: int | None = Form(None),
    aspect: str = Form("16:9"),
    brief: str | None = Form(None),
) -> JobOut:
    # --- structural validation ---
    if not files:
        raise errors.no_files()
    if len(files) > settings.max_files:
        raise errors.too_many_files(len(files), settings.max_files)
    if target_duration is not None and not (
        settings.min_output_sec <= target_duration <= settings.max_output_sec
    ):
        raise errors.invalid_duration(settings.min_output_sec, settings.max_output_sec)
    if aspect not in ("16:9", "9:16", "1:1"):
        aspect = "16:9"
    if _free_mb() < settings.disk_min_free_mb:
        raise errors.storage_unavailable()
    for up in files:
        ext = os.path.splitext(up.filename or "")[1].lower()
        if ext not in settings.allowed_ext:
            raise errors.unsupported_media(up.filename or "file", settings.allowed_ext)

    job_id = uuid.uuid4().hex
    tmp_dir = os.path.join(settings.temp_dir, job_id, "upload")
    saved: list[FileRec] = []
    total = 0
    try:
        for up in files:
            ext = os.path.splitext(up.filename or "")[1].lower()
            file_id = uuid.uuid4().hex
            tmp_path = os.path.join(tmp_dir, f"{file_id}{ext}")
            n = _stream_to_temp(up, tmp_path, settings.max_file_size, settings.max_total_size - total)
            total += n
            key = f"uploads/{job_id}/{file_id}{ext}"
            storage.save_file(key, tmp_path)
            saved.append(
                FileRec(
                    id=file_id, job_id=job_id, original_name=up.filename or f"{file_id}{ext}",
                    stored_key=key, size_bytes=n,
                )
            )
    except errors.AppError:
        storage.delete_prefix(f"uploads/{job_id}/")
        shutil.rmtree(os.path.join(settings.temp_dir, job_id), ignore_errors=True)
        raise
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # --- persist job + files, enqueue ---
    job = repo.create_job(job_id, target_duration or 0, aspect, brief)
    for f in saved:
        repo.add_file(f)
    repo.update_job(job_id, total_uploaded=len(saved), status=JobStatus.QUEUED.value)
    return job_to_out(repo.get_job(job_id))


@router.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> JobOut:
    job = repo.get_job(job_id)
    if job is None:
        raise errors.job_not_found(job_id)
    return job_to_out(job)


@router.get("/api/jobs")
async def list_jobs() -> list[JobOut]:
    return [job_to_out(j) for j in repo.list_jobs(limit=50)]


@router.get("/api/jobs/{job_id}/download")
async def download(job_id: str):
    job = repo.get_job(job_id)
    if job is None:
        raise errors.job_not_found(job_id)
    if job.status == JobStatus.FAILED.value:
        raise errors.job_failed(job.error or "")
    if job.status != JobStatus.COMPLETED.value:
        raise errors.not_ready()
    if not job.output_key or not storage.exists(job.output_key):
        raise errors.output_expired()
    url = storage.presigned_get(job.output_key, settings.presign_ttl)
    return RedirectResponse(url, status_code=302)


@router.get("/api/files/{key:path}")
async def serve_local_file(key: str):
    """Serves objects for the LOCAL storage backend only (R2 uses presigned URLs directly)."""
    if settings.storage_backend != "local":
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "n/a"}})
    from app.storage.local import LocalStorage

    local: LocalStorage = storage  # type: ignore
    if not local.exists(key):
        raise errors.output_expired()
    path = local.local_path_for(key)
    filename = f"stitched-{os.path.basename(key)}"
    return FileResponse(
        path, media_type="video/mp4", filename=filename,
        headers={"Accept-Ranges": "bytes"},
    )
