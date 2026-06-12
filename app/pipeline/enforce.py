"""Deterministic enforcement layer — turns a (possibly LLM-authored) Plan into a validated EDL
that is GUARANTEED to fall in [MIN_OUTPUT, MAX_OUTPUT] or fails honestly (DESIGN §4F).

The LLM is never trusted blindly: every beat is validated against real segment bounds, cuts are
snapped to speech/silence boundaries, and clip lengths are re-balanced with water-fill.
"""
from __future__ import annotations

from difflib import SequenceMatcher

from app.config import settings
from app.models import EDLItem, Plan, Segment
from app.pipeline.allocate import weighted_water_fill


class InsufficientFootage(Exception):
    pass


def _snap_point(t: float, words: list[dict], lo: float, hi: float, tol: float = 0.5) -> float:
    """Snap a cut point to the nearest SILENCE within `tol` seconds, clamped to [lo, hi].

    Cutting in a pause (gap between words) instead of mid-speech makes transitions far less abrupt.
    Candidates: the midpoint of each inter-word gap, plus the very start/end of speech.
    """
    if not words:
        return min(hi, max(lo, t))
    sw = sorted(words, key=lambda w: w.get("start", 0.0))
    candidates: list[float] = [sw[0].get("start", t), sw[-1].get("end", t)]
    for a, b in zip(sw, sw[1:]):
        gap = b.get("start", 0.0) - a.get("end", 0.0)
        if gap > 0.08:  # a real pause
            candidates.append((a["end"] + b["start"]) / 2)
    best = min(candidates, key=lambda c: abs(c - t), default=t)
    if abs(best - t) <= tol:
        t = best
    return min(hi, max(lo, t))


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _is_repeat(text: str, seen: list[str], threshold: float = 0.72) -> bool:
    """True if `text` says substantially the same thing as something already selected."""
    t = _norm(text)
    if len(t.split()) < 4:  # too short to judge repetition reliably
        return False
    return any(
        len(s.split()) >= 4 and SequenceMatcher(None, t, s).ratio() >= threshold for s in seen
    )


def build_edl(plan: Plan, segments: list[Segment], target: int) -> tuple[list[EDLItem], float]:
    """Validate the plan against real segments, snap cuts, enforce the duration window.

    `target` is the desired output length (already clamped to [MIN,MAX] by the caller).
    Returns (edl, final_duration). Raises InsufficientFootage if < MIN_OUTPUT is achievable.
    """
    by_id = {s.id: s for s in segments}

    # 1. Resolve beats → valid (segment, in, out) within source bounds; drop invalid/missing.
    target = max(settings.min_output_sec, min(settings.max_output_sec, int(target)))

    # 1. Resolve beats → (segment, start_point, transition). The LLM picks WHICH clip, WHERE to
    #    start, and the ORDER; the deterministic allocator decides each clip's LENGTH. So a clip can
    #    be extended up to its segment's end (cap = seg.out - start), not limited to the LLM's span.
    resolved: list[list] = []   # [segment, start, transition, weight]
    used_ids: set[str] = set()
    beat_weights: list[float] = []
    seen_texts: list[str] = []   # transcripts already selected → skip repeated messages
    for beat in plan.beats:
        seg = by_id.get(beat.segment_id)
        if seg is None or seg.source_path is None:
            continue
        if _is_repeat(seg.transcript, seen_texts):  # the LLM picked a clip that repeats a message
            continue
        lo, hi = seg.in_point, seg.out_point
        start = _snap_point(min(max(beat.in_point, lo), hi), seg.words, lo, hi)
        if hi - start < 0.3:        # almost nothing left from here → start at segment beginning
            start = lo
        weight = max(0.5, float(getattr(beat, "target_seconds", 0) or 0) or settings.clip_seconds)
        beat_weights.append(weight)
        resolved.append([seg, start, getattr(beat, "transition_in", "cut"), weight])
        used_ids.add(seg.id)
        if _norm(seg.transcript):
            seen_texts.append(_norm(seg.transcript))

    # Default weight for any topped-up clips = the average pacing the LLM asked for.
    default_weight = (sum(beat_weights) / len(beat_weights)) if beat_weights else settings.clip_seconds

    def _avail(item) -> float:
        return item[0].out_point - item[1]

    def _total() -> float:
        return sum(_avail(r) for r in resolved)

    # 2. Top up: if the plan is empty or its footage is short of the target, append the best
    #    unused segments (highest score first) until we reach the target or exhaust footage.
    unused = sorted(
        (s for s in segments if s.id not in used_ids and s.source_path is not None),
        key=lambda s: s.score, reverse=True,
    )
    for s in unused:
        if _total() >= target:
            break
        if _is_repeat(s.transcript, seen_texts):  # don't top up with a repeated message either
            continue
        resolved.append([s, s.in_point, "cut", default_weight])
        used_ids.add(s.id)
        if _norm(s.transcript):
            seen_texts.append(_norm(s.transcript))

    if not resolved:
        raise InsufficientFootage("No usable segments to assemble.")

    # 3. Allocate clip lengths via water-fill, capped by each clip's available footage.
    caps = [_avail(r) for r in resolved]
    total_available = sum(caps)
    if total_available < settings.min_output_sec - 1e-6:
        raise InsufficientFootage(
            f"Insufficient footage: {total_available:.1f}s available, minimum output is "
            f"{settings.min_output_sec}s."
        )

    # `effective_target` is clamped to [min, min(max, total_available)] so it is always in the legal
    # window whenever total footage allows. Use the EXACT water-fill output (no lossy 0.1 rounding):
    # each allocation is already <= the clip's available footage, so nothing gets trimmed below target.
    effective_target = max(
        settings.min_output_sec, min(target, settings.max_output_sec, total_available)
    )
    weights = [r[3] for r in resolved]
    alloc = weighted_water_fill(caps, weights, effective_target)

    # 4. Build EDL, each clip from its (snapped) start with the allocated length.
    edl: list[EDLItem] = []
    for (seg, start, trans, _w), length in zip(resolved, alloc):
        length = min(length, seg.out_point - start)
        if length < 0.05:
            continue
        # Snap the END cut to a pause/silence so we don't cut in the middle of someone speaking.
        out = _snap_point(start + length, seg.words, start + min(settings.min_clip, length), seg.out_point, tol=1.0)
        length = round(out - start, 3)
        if length < 0.05:
            continue
        # Words inside this clip's final window, rebased to clip-relative time (for captions).
        clip_words = [
            {"word": w.get("word", "").strip(), "start": round(w["start"] - start, 3),
             "end": round(w["end"] - start, 3)}
            for w in seg.words
            if w.get("start", 0) >= start - 0.15 and w.get("end", 0) <= start + length + 0.15
            and w.get("word", "").strip()
        ]
        edl.append(
            EDLItem(
                segment_id=seg.id,
                source_path=seg.source_path,
                in_point=round(start, 3),
                out_point=round(start + length, 3),
                duration=round(length, 3),
                transition_in=trans,
                words=clip_words,
            )
        )
    if not edl:
        raise InsufficientFootage("No usable footage after assembly.")

    # 5. Guarantee the lower bound: if tiny rounding left us a hair under MIN and any clip has spare
    #    footage, extend it. We only reach here with total_available >= MIN, so headroom exists.
    final = sum(e.duration for e in edl)
    floor = settings.min_output_sec
    if final < floor:
        deficit = (floor - final) + 0.05  # small buffer so we clear the floor, not land exactly on it
        for e in edl:
            if deficit <= 1e-6:
                break
            seg = by_id[e.segment_id]
            head = round(seg.out_point - e.out_point, 3)
            if head <= 1e-3:
                continue
            add = min(head, deficit)
            e.out_point = round(e.out_point + add, 3)
            e.duration = round(e.duration + add, 3)
            deficit -= add
        final = sum(e.duration for e in edl)

    # 6. Guarantee the upper bound (defensive): never exceed MAX, trimming the last clips if needed.
    cap_max = settings.max_output_sec
    if final > cap_max:
        overflow = final - cap_max
        for e in reversed(edl):
            if overflow <= 1e-6:
                break
            trim = min(overflow, e.duration - 0.05)
            if trim <= 0:
                continue
            e.out_point = round(e.out_point - trim, 3)
            e.duration = round(e.duration - trim, 3)
            overflow -= trim
        final = sum(e.duration for e in edl)

    final_duration = round(final, 2)
    return edl, final_duration
