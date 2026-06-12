"""Groq planner (free tier, fast open-model inference). Text-only, one call, JSON output.

Supports MULTIPLE Groq keys (from different accounts) for extra free quota: it tries each key in
turn and only fails once all keys are exhausted (then the chain falls to the heuristic planner).
"""
from __future__ import annotations

import json
import logging

from app.config import settings
from app.pipeline.plan.base import PlannerError, parse_plan
from app.pipeline.plan.prompt import SYSTEM, build_user_prompt

log = logging.getLogger("plan")
MODEL = "llama-3.3-70b-versatile"


class GroqPlanner:
    name = "groq"

    def __init__(self) -> None:
        if not settings.groq_keys:
            raise PlannerError("no GROQ_API_KEY")

    def plan(self, catalog, brief, target):
        from groq import Groq

        valid_ids = {c["segment_id"] for c in catalog}
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": build_user_prompt(catalog, brief, target)},
        ]
        last: Exception | None = None
        for i, key in enumerate(settings.groq_keys):
            try:
                client = Groq(api_key=key, timeout=settings.llm_timeout)
                resp = client.chat.completions.create(
                    model=MODEL, messages=messages,
                    response_format={"type": "json_object"}, temperature=0.6,
                )
                data = json.loads(resp.choices[0].message.content)
                return parse_plan(data, valid_ids, self.name)
            except Exception as e:  # quota/timeout/parse → try the next key
                last = e
                log.warning("groq key #%d failed: %s", i + 1, str(e)[:160])
        raise PlannerError(f"all {len(settings.groq_keys)} groq key(s) failed: {last}")
