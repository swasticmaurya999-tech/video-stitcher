"""Beat-synced cuts — nudge clip boundaries onto the music's beats (opt-in, experimental).

Snaps are deliberately small (<= MAX_SNAP) so durations barely change: the total is preserved
(duration guarantee intact) and clips never extend meaningfully past their available footage.
"""
from __future__ import annotations

import logging
from functools import lru_cache

log = logging.getLogger("beats")

MAX_SNAP = 0.35  # max seconds a boundary may move to reach a beat


@lru_cache(maxsize=8)
def beat_times(music_path: str) -> tuple[float, ...]:
    """Beat onset times (seconds) for a music file, or () if librosa is unavailable/fails."""
    try:
        import librosa

        y, sr = librosa.load(music_path, sr=22050, mono=True, duration=180)
        _tempo, frames = librosa.beat.beat_track(y=y, sr=sr)
        return tuple(float(t) for t in librosa.frames_to_time(frames, sr=sr))
    except Exception as e:  # pragma: no cover
        log.info("beat detection unavailable: %s", e)
        return ()


def snap_durations(durations: list[float], beats: tuple[float, ...], min_clip: float) -> list[float]:
    """Return new clip durations whose cumulative boundaries sit on nearby beats.

    Total is preserved; each clip stays >= min_clip; no boundary moves more than MAX_SNAP.
    """
    n = len(durations)
    if n < 2 or len(beats) < 2:
        return durations
    cum, acc = [], 0.0
    for d in durations:
        acc += d
        cum.append(acc)
    total = cum[-1]

    new_bounds: list[float] = []
    prev = 0.0
    for b in cum[:-1]:  # internal boundaries only (keep the final = total)
        nearest = min(beats, key=lambda x: abs(x - b))
        snapped = nearest if abs(nearest - b) <= MAX_SNAP else b
        snapped = max(prev + min_clip, min(snapped, total - min_clip))
        new_bounds.append(round(snapped, 3))
        prev = snapped
    new_bounds.append(round(total, 3))

    out, prev = [], 0.0
    for b in new_bounds:
        out.append(round(b - prev, 3))
        prev = b
    return out
