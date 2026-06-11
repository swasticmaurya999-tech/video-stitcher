"""Stage 4b — PLAN: multi-provider failover chain (DESIGN §4 failover).

Try each configured cloud planner in order; on quota/timeout/invalid-output, fall to the next;
the heuristic planner is the always-succeeds floor. Runs synchronously within the job.
"""
from __future__ import annotations

import logging
import time

from app.config import settings
from app.models import Plan, Segment
from app.pipeline.plan.base import PlannerError
from app.pipeline.plan.heuristic import HeuristicPlanner

log = logging.getLogger("plan")


def _make(name: str):
    if name == "gemini":
        from app.pipeline.plan.gemini import GeminiPlanner

        return GeminiPlanner()
    if name == "groq":
        from app.pipeline.plan.groq import GroqPlanner

        return GroqPlanner()
    if name == "heuristic":
        return HeuristicPlanner()
    raise PlannerError(f"unknown planner '{name}'")


def make_plan(catalog: list[dict], segments: list[Segment], brief: str | None, target: int) -> Plan:
    heuristic = HeuristicPlanner()
    for name in settings.planners:
        if name == "heuristic":
            break  # handled as the floor below
        try:
            planner = _make(name)
        except Exception as e:
            log.info("planner %s unavailable: %s", name, e)
            continue
        # one short retry for transient blips, then move on
        for attempt in range(2):
            try:
                plan = planner.plan(catalog, brief, target)
                log.info("plan via %s (%d beats)", name, len(plan.beats))
                return plan
            except Exception as e:
                log.warning("planner %s attempt %d failed: %s", name, attempt + 1, e)
                if attempt == 0:
                    time.sleep(0.5)
    # Deterministic floor — never fails.
    log.info("falling back to heuristic planner")
    return heuristic.plan_from_segments(segments, brief, target)
