"""Music library — pick a mood-matched track that varies per output.

Tracks live under app/assets/music/<mood>/*.mp3 (bundled CC0/royalty-free). The LLM's
`music_mood` is mapped to a bucket; within the bucket we rotate deterministically by job id so
different jobs get different tracks but a retry of the same job is stable.
"""
from __future__ import annotations

import glob
from pathlib import Path

from app.config import settings

# keyword (substring of the LLM's music_mood) → bucket folder
_MOOD_MAP = {
    "upbeat": "upbeat", "happy": "upbeat", "fun": "upbeat", "playful": "upbeat",
    "cheerful": "upbeat", "bright": "upbeat", "festive": "upbeat",
    "energetic": "energetic", "energy": "energetic", "exciting": "energetic",
    "dynamic": "energetic", "intense": "energetic", "action": "energetic",
    "sale": "energetic", "hype": "energetic", "powerful": "energetic",
    "corporate": "corporate", "professional": "corporate", "business": "corporate",
    "tech": "corporate", "modern": "corporate", "inspiring": "corporate",
    "inspirational": "corporate", "motivational": "corporate", "clean": "corporate",
    "calm": "calm", "relaxed": "calm", "gentle": "calm", "soft": "calm",
    "emotional": "calm", "warm": "calm", "ambient": "calm", "mellow": "calm",
}


def _root() -> Path:
    p = Path(settings.music_library_dir)
    if not p.is_absolute():
        # resolve relative to the app package (robust in container + dev)
        p = Path(__file__).resolve().parents[1] / "assets" / "music"
    return p


def _bucket(mood: str) -> str:
    m = (mood or "").lower()
    for kw, bucket in _MOOD_MAP.items():
        if kw in m:
            return bucket
    return ""


def pick_track(mood: str, job_id: str = "") -> str | None:
    """Return a path to a mood-matched track, or None if the library is empty."""
    root = _root()
    if not root.exists():
        return None
    bucket = _bucket(mood)
    candidates: list[str] = []
    if bucket:
        candidates = sorted(glob.glob(str(root / bucket / "*.mp3")))
    if not candidates:  # no bucket match → any track
        candidates = sorted(glob.glob(str(root / "**" / "*.mp3"), recursive=True))
    if not candidates:
        return None
    try:
        seed = int(job_id[:8], 16) if job_id else 0
    except ValueError:
        seed = 0
    return candidates[seed % len(candidates)]
