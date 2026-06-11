"""Stage 3 — ANALYZE: score every segment and attach lightweight understanding.

All local/free. Quality scoring (sharpness, brightness, motion, audio energy) uses sampled,
downscaled frames. Optional CLIP tags / object detection / Whisper transcript are toggled and
degrade gracefully. Output: each Segment gets `score`, `tags`, `transcript`, `words`.
"""
from __future__ import annotations

import logging

from app.config import settings
from app.db import repo
from app.models import Segment
from app.pipeline.ingest import IngestedFile

log = logging.getLogger("analyze")


def _safe_cv2():
    try:
        import cv2  # noqa
        import numpy as np  # noqa

        return cv2, np
    except Exception:
        return None, None


def _score_segment(path: str, in_p: float, out_p: float) -> tuple[float, float]:
    """Return (quality_score in 0..1, motion in 0..1) from sampled frames."""
    cv2, np = _safe_cv2()
    if cv2 is None:
        return 0.5, 0.0  # neutral when OpenCV unavailable
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return 0.4, 0.0
    dur = max(0.1, out_p - in_p)
    n_samples = min(6, max(2, int(dur * settings.analyze_fps)))
    sharp_vals, bright_vals, prev_small, motion_vals = [], [], None, []
    for k in range(n_samples):
        t = in_p + dur * (k + 0.5) / n_samples
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        h, w = frame.shape[:2]
        scale = settings.analyze_maxdim / max(h, w) if max(h, w) > settings.analyze_maxdim else 1.0
        small = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale))))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        sharp_vals.append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))
        bright_vals.append(float(gray.mean()))
        if prev_small is not None:
            motion_vals.append(float(np.abs(gray.astype("int16") - prev_small.astype("int16")).mean()))
        prev_small = gray
    cap.release()
    if not sharp_vals:
        return 0.4, 0.0

    # Normalize each signal into 0..1 with sensible references, then combine.
    sharp = min(1.0, (sum(sharp_vals) / len(sharp_vals)) / 300.0)
    bright_mean = sum(bright_vals) / len(bright_vals)
    # Penalize too-dark (<40) and blown-out (>220); reward mid-exposure.
    exposure = max(0.0, 1.0 - abs(bright_mean - 130.0) / 130.0)
    motion = min(1.0, (sum(motion_vals) / len(motion_vals)) / 25.0) if motion_vals else 0.0
    quality = 0.5 * sharp + 0.3 * exposure + 0.2 * min(1.0, motion + 0.3)
    return round(max(0.0, min(1.0, quality)), 4), round(motion, 4)


def _transcribe(path: str) -> list[dict]:
    """Whisper transcription with word timestamps for the whole source file (cached per call)."""
    from app.models_ml import get_whisper

    model = get_whisper()
    if model is None:
        return []
    try:
        segments, _ = model.transcribe(path, word_timestamps=True, vad_filter=True)
        words = []
        for seg in segments:
            for w in getattr(seg, "words", None) or []:
                words.append({"word": w.word, "start": float(w.start or 0), "end": float(w.end or 0)})
        return words
    except Exception as e:  # pragma: no cover
        log.warning("transcribe failed: %s", e)
        return []


def _clip_tags(path: str, mid_t: float) -> list[str]:
    from app.models_ml import CLIP_LABELS, get_clip

    clip = get_clip()
    cv2, _ = _safe_cv2()
    if clip is None or cv2 is None:
        return []
    try:
        cap = cv2.VideoCapture(path)
        cap.set(cv2.CAP_PROP_POS_MSEC, mid_t * 1000.0)
        ok, frame = cap.read()
        cap.release()
        if not ok:
            return []
        from PIL import Image

        torch = clip["torch"]
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        image = clip["preprocess"](img).unsqueeze(0)
        text = clip["tokenizer"](CLIP_LABELS)
        with torch.no_grad():
            i_feat = clip["model"].encode_image(image)
            t_feat = clip["model"].encode_text(text)
            i_feat /= i_feat.norm(dim=-1, keepdim=True)
            t_feat /= t_feat.norm(dim=-1, keepdim=True)
            sims = (i_feat @ t_feat.T).softmax(dim=-1)[0]
        top = sims.topk(min(3, len(CLIP_LABELS)))
        return [CLIP_LABELS[i].replace("a ", "").replace("an ", "") for i in top.indices.tolist()]
    except Exception:
        return []


def _detect_objects(path: str, mid_t: float) -> tuple[list[str], float]:
    """Object/person detection on the mid-frame (YOLO). Returns (labels, score_boost).

    Footage with people/products is usually more ad-worthy, so we nudge its score up.
    """
    from app.models_ml import get_detector

    det = get_detector()
    cv2, _ = _safe_cv2()
    if det is None or cv2 is None:
        return [], 0.0
    try:
        cap = cv2.VideoCapture(path)
        cap.set(cv2.CAP_PROP_POS_MSEC, mid_t * 1000.0)
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            return [], 0.0
        res = det.predict(frame, verbose=False)[0]
        names = res.names
        labels = []
        if res.boxes is not None:
            labels = sorted({names[int(c)] for c in res.boxes.cls.tolist()})
        boost = 0.12 if "person" in labels else (0.05 if labels else 0.0)
        return labels[:5], boost
    except Exception:
        return [], 0.0


def _words_in(words: list[dict], in_p: float, out_p: float) -> list[dict]:
    return [w for w in words if w["start"] >= in_p - 0.2 and w["end"] <= out_p + 0.2]


def analyze(job_id: str, files: list[IngestedFile], segments: list[Segment]) -> list[Segment]:
    # Transcribe each source once, then map to its segments.
    transcripts: dict[str, list[dict]] = {}
    if settings.enable_whisper:
        for f in files:
            transcripts[f.path] = _transcribe(f.path)

    repo.set_progress(job_id, "analyze", 0, len(segments))
    for i, seg in enumerate(segments):
        seg.score, _motion = _score_segment(seg.source_path, seg.in_point, seg.out_point)
        mid = (seg.in_point + seg.out_point) / 2
        if settings.enable_clip:
            seg.tags = _clip_tags(seg.source_path, mid)
        if settings.enable_detect:
            labels, boost = _detect_objects(seg.source_path, mid)
            seg.tags = list(dict.fromkeys(seg.tags + labels))  # merge, dedupe, keep order
            seg.score = round(min(1.0, seg.score + boost), 4)
        words = transcripts.get(seg.source_path, [])
        if words:
            seg.words = _words_in(words, seg.in_point, seg.out_point)
            seg.transcript = " ".join(w["word"].strip() for w in seg.words)[:500]
        repo.set_progress(job_id, "analyze", i + 1, len(segments))

    # Persist segment metadata.
    for seg in segments:
        repo.add_segment(seg)

    # Bound the candidate set for planning + render (top-K by score).
    segments.sort(key=lambda s: s.score, reverse=True)
    return segments[: settings.max_segments_considered]
