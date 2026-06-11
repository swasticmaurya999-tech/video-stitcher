"""Groq planner (free tier, fast open-model inference). Text-only, one call, JSON output."""
from __future__ import annotations

import json

from app.config import settings
from app.pipeline.plan.base import PlannerError, parse_plan
from app.pipeline.plan.prompt import SYSTEM, build_user_prompt

MODEL = "llama-3.3-70b-versatile"


class GroqPlanner:
    name = "groq"

    def __init__(self) -> None:
        if not settings.groq_api_key:
            raise PlannerError("no GROQ_API_KEY")
        from groq import Groq

        self._client = Groq(api_key=settings.groq_api_key, timeout=settings.llm_timeout)

    def plan(self, catalog, brief, target):
        valid_ids = {c["segment_id"] for c in catalog}
        resp = self._client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": build_user_prompt(catalog, brief, target)},
            ],
            response_format={"type": "json_object"},
            temperature=0.6,
        )
        try:
            data = json.loads(resp.choices[0].message.content)
        except (json.JSONDecodeError, AttributeError, IndexError) as e:
            raise PlannerError(f"groq returned non-JSON: {e}")
        return parse_plan(data, valid_ids, self.name)
