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
from app.pipeline import beats
from app.pipeline import captions
from app.pipeline import ffmpeg
from app.pipeline import music as music_lib
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


def render(
    job_id: str, edl: list[EDLItem], aspect: str,
    title: str = "", cta: str = "", music_mood: str = "",
) -> tuple[str, float]:
    """Render the final video. Returns (storage_key, actual_output_duration_seconds)."""
    dims = _dims_for(aspect)
    has_audio = _audio_map(edl)
    work = job_temp(job_id, "work")
    os.makedirs(work, exist_ok=True)

    # Pick the soundtrack up front (needed for beat-sync below + the audio pass later).
    music = settings.music_path or music_lib.pick_track(music_mood, job_id) \
        or str(Path(__file__).resolve().parents[1] / "assets" / "music.mp3")

    # Beat-sync (opt-in): nudge clip durations so cuts land on the music's beats.
    if settings.enable_beatsync and len(edl) >= 2 and music and os.path.exists(music):
        try:
            bt = beats.beat_times(music)
            new_durs = beats.snap_durations([e.duration for e in edl], bt, settings.min_clip)
            for e, nd in zip(edl, new_durs):
                e.duration = nd
                e.out_point = round(e.in_point + nd, 3)
            log.info("beat-sync: adjusted %d clip boundaries to beats", len(edl))
        except Exception as e:
            log.warning("beat-sync failed, keeping durations: %s", e)

    repo.set_progress(job_id, "render", 0, len(edl))
    normalized: list[str] = []
    used_items: list[EDLItem] = []   # edl items that normalized successfully (parallel to `normalized`)
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
            used_items.append(item)
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

    durations = [it.duration for it in used_items]
    n = len(normalized)
    # Per-pair dissolve length, clamped so even a short clip still gets a (smaller) crossfade — no
    # hard cuts anywhere. d_list[i] = dissolve between clip i and i+1.
    d_list = [
        max(0.12, min(settings.crossfade_duration, durations[i] * 0.5 - 0.05, durations[i + 1] * 0.5 - 0.05))
        for i in range(n - 1)
    ]
    use_xfade = (
        settings.enable_transitions and n >= 2 and min(durations) > 0.3
        and (sum(durations) - sum(d_list)) >= settings.min_output_sec
    )
    stitched_path = os.path.join(work, "stitched.mp4")
    if use_xfade:
        try:
            ffmpeg.concat_with_transitions(normalized, durations, d_list, stitched_path)
        except Exception as e:
            log.warning("crossfade concat failed, using hard cuts: %s", e)
            use_xfade = False
    if not use_xfade:
        list_file = os.path.join(work, "concat.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            for p in normalized:
                f.write(f"file '{p}'\n")
        try:
            ffmpeg.concat_copy(list_file, stitched_path)
        except Exception:
            ffmpeg.concat_filter(normalized, stitched_path, dims, settings.fps)

    # Output start time of each clip + total, accounting for per-pair crossfade overlaps.
    clip_starts: list[float] = []
    for k in range(len(used_items)):
        start = sum(durations[:k]) - (sum(d_list[:k]) if use_xfade else 0.0)
        clip_starts.append(round(start, 3))
    total = round(sum(durations) - (sum(d_list) if use_xfade else 0.0), 3)
    current = stitched_path

    # Burned captions of the speech (readable on mute) from the clip-relative word timestamps.
    if settings.enable_captions and any(it.words for it in used_items):
        ass_path = os.path.join(work, "captions.ass")
        try:
            if captions.build_ass(used_items, clip_starts, dims, ass_path, settings.caption_fontsize_div):
                capped = os.path.join(work, "captioned.mp4")
                ffmpeg.burn_captions(current, ass_path, capped)
                current = capped
        except Exception as e:
            log.warning("captions failed, skipping: %s", e)

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

    # Soundtrack. "voiceover" = keep clip speech + duck music under it (default); "music" = music-only
    # bed (mute clips); "mix" = music under clip audio (no duck); "clips" = clip audio only.
    output_path = current
    mode = settings.audio_mode
    if mode != "clips" and music and os.path.exists(music):
        mixed = os.path.join(work, "output.mp4")
        try:
            if mode == "voiceover":
                ffmpeg.apply_audio_ducked(current, mixed, total, music, music_volume=0.5)
            elif mode == "music":
                ffmpeg.apply_audio(current, mixed, total, "music", music, 0.85)
            else:  # mix
                ffmpeg.apply_audio(current, mixed, total, "mix", music, settings.music_volume)
            output_path = mixed
            log.info("audio mode=%s track=%s", mode, os.path.basename(music))
        except Exception as e:
            log.warning("audio pass failed, keeping clip audio: %s", e)
    elif mode != "clips":
        log.warning("no music track available; keeping clip audio")

    # Clean ending: fade in at the start + fade to black at the end (avoids the abrupt cut-off).
    if settings.enable_endfade and total > 2.0:
        faded = os.path.join(work, "final.mp4")
        try:
            ffmpeg.fade_video(output_path, faded, total)
            output_path = faded
        except Exception as e:
            log.warning("end-fade failed, skipping: %s", e)

    repo.set_progress(job_id, "upload", 0, 1)
    output_key = f"outputs/{job_id}.mp4"
    storage.save_file(output_key, output_path)
    repo.set_progress(job_id, "upload", 1, 1)
    return output_key, round(total, 2)


def _dims_for(aspect: str) -> tuple[int, int]:
    if aspect == "9:16":
        return (1080, 1920)
    if aspect == "1:1":
        return (1080, 1080)
    return (settings.target_width, settings.target_height)
