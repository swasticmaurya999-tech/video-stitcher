"""Deterministic planner — the always-available fallback (never fails).

Builds a sensible montage with an energy arc when no LLM is available/usable: feature the highest-
scoring clips, lead with the strongest as a hook, vary the rest. Produces the same `Plan` shape.
"""
from __future__ import annotations

import math

from app.config import settings
from app.models import Beat, Plan, Segment


class HeuristicPlanner:
    name = "heuristic"

    def plan_from_segments(self, segments: list[Segment], brief: str | None, target: int) -> Plan:
        usable = [s for s in segments if s.duration >= 0.3]
        if not usable:
            usable = list(segments)
        usable.sort(key=lambda s: s.score, reverse=True)

        max_clips = max(1, int(target // settings.min_clip))
        chosen = usable[: min(len(usable), max_clips)]

        # Energy arc: strongest clip as the hook, then alternate strong/weak for rhythm.
        if len(chosen) > 2:
            hook = chosen[0]
            rest = chosen[1:]
            arranged = [hook]
            lo, hi = 0, len(rest) - 1
            toggle = True
            while lo <= hi:
                arranged.append(rest[lo] if toggle else rest[hi])
                if toggle:
                    lo += 1
                else:
                    hi -= 1
                toggle = not toggle
            chosen = arranged

        per = target / max(1, len(chosen))
        beats: list[Beat] = []
        for i, s in enumerate(chosen):
            role = "hook" if i == 0 else ("cta" if i == len(chosen) - 1 else "body")
            b = Beat(
                role=role,
                intent=f"{role} clip (score {s.score:.2f})",
                target_seconds=round(min(per, s.duration), 2),
                segment_id=s.id,
                in_point=s.in_point,
                out_point=s.out_point,
            )
            b.transition_in = "cut"
            beats.append(b)

        return Plan(
            detected_genre="highlight reel",
            theme=brief or "auto-montage of the strongest moments",
            confidence=0.4,
            beats=beats,
            transitions=["cut"] * len(beats),
            music_mood="upbeat",
            rationale=(
                f"No LLM plan used — selected the top {len(chosen)} clips by quality score, led with "
                f"the strongest as a hook, and arranged the rest for rhythm. Durations balanced toward "
                f"the ~{target}s target."
            ),
            planner_used=self.name,
        )

    # Conforms to Planner protocol via the chain (catalog path unused; chain calls plan_from_segments).
    def plan(self, catalog, brief, target):  # pragma: no cover - not used directly
        raise NotImplementedError
