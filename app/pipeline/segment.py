"""Stage 2 — SEGMENT: split each video at natural shot boundaries (clean cut points).

Uses PySceneDetect when available; degrades gracefully to "whole file = one segment" (e.g. a
single continuous take, or if the library/decode fails). Tiny scenes are merged to >= MIN_CLIP.
"""
from __future__ import annotations

import uuid

from app.config import settings
from app.db import repo
from app.models import Segment
from app.pipeline.ingest import IngestedFile


def _scene_cuts(path: str, duration: float) -> list[tuple[float, float]]:
    try:
        from scenedetect import ContentDetector, SceneManager, open_video

        video = open_video(path)
        sm = SceneManager()
        sm.add_detector(ContentDetector(threshold=27.0))
        sm.detect_scenes(video, show_progress=False)
        scenes = sm.get_scene_list()
        spans = [(s.get_seconds(), e.get_seconds()) for s, e in scenes]
        if not spans:
            return [(0.0, duration)]
        return spans
    except Exception:
        # Single-take / library-missing / decode error → whole clip is one segment.
        return [(0.0, duration)]


def _merge_short(spans: list[tuple[float, float]], min_clip: float) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for s, e in spans:
        if merged and (e - merged[-1][0]) and (merged[-1][1] - merged[-1][0]) < min_clip:
            merged[-1][1] = e  # extend the previous too-short scene
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged if e - s > 0.05]


def segment(job_id: str, files: list[IngestedFile]) -> list[Segment]:
    segs: list[Segment] = []
    repo.set_progress(job_id, "segment", 0, len(files))
    for i, f in enumerate(files):
        spans = _merge_short(_scene_cuts(f.path, f.rec.duration or 0.0), settings.min_clip)
        for (s, e) in spans:
            seg = Segment(
                id=uuid.uuid4().hex,
                job_id=job_id,
                source_file_id=f.rec.id,
                in_point=round(s, 3),
                out_point=round(e, 3),
                duration=round(e - s, 3),
                source_path=f.path,
            )
            segs.append(seg)
        repo.set_progress(job_id, "segment", i + 1, len(files))
    return segs
