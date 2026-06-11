"""Stage 4a — CATALOG: compact, text-only description of the footage for the planner.

Keeping this small and text-only is what keeps LLM token use tiny (one cheap call/job).
"""
from __future__ import annotations

from app.models import Segment


def build_catalog(segments: list[Segment]) -> list[dict]:
    catalog = []
    for s in segments:
        catalog.append(
            {
                "segment_id": s.id,
                "duration": round(s.duration, 2),
                "score": round(s.score, 3),
                "tags": s.tags,
                "transcript": s.transcript[:200],
                "in": round(s.in_point, 2),
                "out": round(s.out_point, 2),
            }
        )
    return catalog
