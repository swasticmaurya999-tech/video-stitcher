"""Stage 6 — RENDER: normalize each EDL clip → persist → concat → upload.

Per-clip normalization to a uniform profile (with loudnorm) makes the concat clean and robust;
normalized clips are persisted to storage so reorders/variants are cheap re-concatenation
(DESIGN §5 persisted segments). A single failed clip is skipped, not fatal.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from app.config import settings
from app.db import repo
from app.models import EDLItem
from app.pipeline import ffmpeg
from app.pipeline.ingest import job_temp
from app.storage import storage

log = logging.getLogger("render")


def _audio_map(edl: list[EDLItem]) -> dict[str, bool]:
    """Probe each unique source once for audio presence."""
    out: dict[str, bool] = {}
    for item in edl:
        if item.source_path not in out:
            try:
                out[item.source_path] = ffmpeg.probe(item.source_path).has_audio
            except Exception:
                out[item.source_path] = False
    return out


def render(job_id: str, edl: list[EDLItem], aspect: str, title: str = "", cta: str = "") -> str:
    """Returns the storage key of the final output video."""
    dims = _dims_for(aspect)
    has_audio = _audio_map(edl)
    work = job_temp(job_id, "work")
    os.makedirs(work, exist_ok=True)

    repo.set_progress(job_id, "render", 0, len(edl))
    normalized: list[str] = []
    for i, item in enumerate(edl):
        clip_path = os.path.join(work, f"clip_{i:03d}.mp4")
        try:
            ffmpeg.normalize_clip(
                src=item.source_path,
                in_point=item.in_point,
                duration=item.duration,
                out_path=clip_path,
                dims=dims,
                fps=settings.fps,
                has_audio=has_audio.get(item.source_path, False),
                stabilize=settings.enable_stabilize,
            )
            normalized.append(clip_path)
            # Persist the normalized building block (cheap reorder/variants later).
            seg_key = f"segments/{job_id}/{aspect.replace(':', 'x')}/clip_{i:03d}.mp4"
            try:
                storage.save_file(seg_key, clip_path)
                repo.update_segment(item.segment_id, normalized_key=seg_key)
            except Exception as e:  # persistence is best-effort; render still proceeds
                log.warning("persist segment failed: %s", e)
        except Exception as e:
            log.warning("clip %d failed, skipping: %s", i, e)
        repo.set_progress(job_id, "render", i + 1, len(edl))

    if not normalized:
        raise RuntimeError("All clips failed to render.")

    # Concat (fast copy — clips are already uniform). Crossfades would re-encode (stretch).
    list_file = os.path.join(work, "concat.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for p in normalized:
            f.write(f"file '{p}'\n")
    stitched_path = os.path.join(work, "stitched.mp4")
    try:
        ffmpeg.concat_copy(list_file, stitched_path)
    except Exception:
        # Fallback to a re-encoding concat if copy fails (rare profile mismatch).
        ffmpeg.concat_filter(normalized, stitched_path, dims, settings.fps)

    total = sum(item.duration for item in edl)
    current = stitched_path

    # Brand text overlays (title intro + CTA end card) from the LLM/brief.
    if settings.enable_text and (title.strip() or cta.strip()):
        title_file = cta_file = None
        if title.strip():
            title_file = os.path.join(work, "title.txt")
            with open(title_file, "w", encoding="utf-8") as f:
                f.write(title.strip()[:60])
        if cta.strip():
            cta_file = os.path.join(work, "cta.txt")
            with open(cta_file, "w", encoding="utf-8") as f:
                f.write(cta.strip()[:80])
        titled = os.path.join(work, "titled.mp4")
        try:
            ffmpeg.add_text_overlays(current, titled, total, settings.brand_font, title_file, cta_file)
            current = titled
        except Exception as e:
            log.warning("text overlay failed, skipping: %s", e)

    # Optional music bed (ad soundtrack). Falls back to the silent-mix if music is missing/fails.
    output_path = current
    if settings.enable_music:
        music = settings.music_path or str(Path(__file__).resolve().parents[1] / "assets" / "music.mp3")
        if os.path.exists(music):
            mixed = os.path.join(work, "output.mp4")
            try:
                ffmpeg.mix_music(current, music, mixed, total, settings.music_volume)
                output_path = mixed
            except Exception as e:
                log.warning("music mix failed, using audio as-is: %s", e)
        else:
            log.warning("music file not found at %s; skipping music bed", music)

    repo.set_progress(job_id, "upload", 0, 1)
    output_key = f"outputs/{job_id}.mp4"
    storage.save_file(output_key, output_path)
    repo.set_progress(job_id, "upload", 1, 1)
    return output_key


def _dims_for(aspect: str) -> tuple[int, int]:
    if aspect == "9:16":
        return (1080, 1920)
    if aspect == "1:1":
        return (1080, 1080)
    return (settings.target_width, settings.target_height)
