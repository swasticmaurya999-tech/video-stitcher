"""Planner protocol + shared parsing of the structured plan JSON into a `Plan` dataclass.

Every planner (Gemini / Groq / Heuristic) returns the SAME validated `Plan`, so the downstream
enforcer/renderer is provider-agnostic.
"""
from __future__ import annotations

from typing import Protocol

from app.models import Beat, Plan


class Planner(Protocol):
    name: str

    def plan(self, catalog: list[dict], brief: str | None, target: int) -> Plan:
        ...


class PlannerError(RuntimeError):
    pass


def parse_plan(data: dict, valid_ids: set[str], planner_name: str) -> Plan:
    """Validate raw plan JSON against real segment ids. Raises PlannerError on unusable output."""
    if not isinstance(data, dict):
        raise PlannerError("plan is not an object")
    raw_beats = data.get("beats") or []
    if not isinstance(raw_beats, list) or not raw_beats:
        raise PlannerError("plan has no beats")

    beats: list[Beat] = []
    for b in raw_beats:
        if not isinstance(b, dict):
            continue
        sid = b.get("segment_id")
        if sid not in valid_ids:
            continue  # drop hallucinated segment references
        try:
            in_p = float(b.get("in"))
            out_p = float(b.get("out"))
            tgt = float(b.get("target_seconds", max(0.5, out_p - in_p)))
        except (TypeError, ValueError):
            continue
        if out_p <= in_p:
            continue
        beat = Beat(
            role=str(b.get("role", "")),
            intent=str(b.get("intent", "")),
            target_seconds=tgt,
            segment_id=sid,
            in_point=in_p,
            out_point=out_p,
        )
        beat.transition_in = str(b.get("transition", "cut")).lower()  # dynamic attr used by enforce
        beats.append(beat)

    if not beats:
        raise PlannerError("no beats referenced valid segments")

    return Plan(
        detected_genre=str(data.get("detected_genre", "highlight reel"))[:80],
        theme=str(data.get("theme", ""))[:160],
        confidence=float(data.get("confidence", 0.5) or 0.5),
        beats=beats,
        transitions=[str(t) for t in (data.get("transitions") or [])][:50],
        music_mood=str(data.get("music_mood", ""))[:60],
        title_text=str(data.get("title_text", ""))[:120],
        cta_text=str(data.get("cta_text", ""))[:120],
        rationale=str(data.get("rationale", ""))[:1000],
        planner_used=planner_name,
    )
