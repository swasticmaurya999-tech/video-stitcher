"""Gemini planner (Google AI Studio free tier). Text-only, one call, JSON output."""
from __future__ import annotations

import json

from app.config import settings
from app.pipeline.plan.base import PlannerError, parse_plan
from app.pipeline.plan.prompt import SYSTEM, build_user_prompt

MODEL = "gemini-2.0-flash"


class GeminiPlanner:
    name = "gemini"

    def __init__(self) -> None:
        if not settings.gemini_api_key:
            raise PlannerError("no GEMINI_API_KEY")
        import google.generativeai as genai

        genai.configure(api_key=settings.gemini_api_key)
        self._model = genai.GenerativeModel(
            MODEL,
            system_instruction=SYSTEM,
            generation_config={"response_mime_type": "application/json", "temperature": 0.6},
        )

    def plan(self, catalog, brief, target):
        valid_ids = {c["segment_id"] for c in catalog}
        resp = self._model.generate_content(
            build_user_prompt(catalog, brief, target),
            request_options={"timeout": settings.llm_timeout},
        )
        try:
            data = json.loads(resp.text)
        except (json.JSONDecodeError, AttributeError) as e:
            raise PlannerError(f"gemini returned non-JSON: {e}")
        return parse_plan(data, valid_ids, self.name)
