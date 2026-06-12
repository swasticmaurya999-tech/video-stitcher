"""Agentic critic — a 2nd LLM pass that reviews the storyboard before rendering.

Flags repetition, incoherent ordering, weak hook/CTA, or off-genre choices and returns feedback the
planner can act on. Text-only, reuses the free-tier providers. Never blocks: any failure → approved.
"""
from __future__ import annotations

import json
import logging

from app.config import settings
from app.models import Plan

log = logging.getLogger("critic")

CRITIC_SYSTEM = (
    "You are a senior creative director reviewing an automated short-video AD edit plan. A viewer "
    "should watch it and immediately understand a single, coherent message. Be a STRICT reviewer.\n"
    "FLAG (approved=false) if ANY of these are true:\n"
    "- two or more beats convey the SAME spoken line or message (repetition) — name which beats;\n"
    "- the order doesn't tell a logical story (should flow hook → product/value → offer → CTA);\n"
    "- the opening beat is not a strong hook, or there is no clear call-to-action at the end;\n"
    "- a beat's content is irrelevant to the inferred product/message.\n"
    "In feedback, give SPECIFIC, actionable instructions: which beats to drop (by segment_id), how to "
    "reorder, and what the hook/CTA should be. Return STRICT JSON: "
    '{"approved": true|false, "score": 0-10, "feedback": "specific fixes"}. No prose outside JSON.'
)


def _call_json(system: str, user: str) -> dict | None:
    for name in settings.planners:
        try:
            if name == "groq" and settings.groq_keys:
                from groq import Groq
                from app.pipeline.plan.groq import MODEL as GMODEL

                for key in settings.groq_keys:  # try each Groq key (main → fallback)
                    try:
                        client = Groq(api_key=key, timeout=settings.llm_timeout)
                        r = client.chat.completions.create(
                            model=GMODEL,
                            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                            response_format={"type": "json_object"}, temperature=0.3,
                        )
                        return json.loads(r.choices[0].message.content)
                    except Exception as e:
                        log.info("critic groq key failed: %s", str(e)[:120])
                continue
            if name == "gemini" and settings.gemini_api_key:
                import google.generativeai as genai
                from app.pipeline.plan.gemini import MODEL as GEMMODEL

                genai.configure(api_key=settings.gemini_api_key)
                m = genai.GenerativeModel(
                    GEMMODEL, system_instruction=system,
                    generation_config={"response_mime_type": "application/json", "temperature": 0.3},
                )
                resp = m.generate_content(user, request_options={"timeout": settings.llm_timeout})
                return json.loads(resp.text)
        except Exception as e:  # quota/timeout/parse — try next, else give up
            log.info("critic via %s failed: %s", name, e)
    return None


def critique(plan: Plan, catalog: list[dict], brief: str | None) -> dict:
    """Return {"approved": bool, "feedback": str}. Defaults to approved on any failure."""
    payload = {
        "brief": brief or "(none)",
        "detected_genre": plan.detected_genre,
        "title_text": plan.title_text,
        "cta_text": plan.cta_text,
        "beats": [
            {"order": i, "role": b.role, "intent": b.intent, "segment_id": b.segment_id}
            for i, b in enumerate(plan.beats)
        ],
        "transcripts": {c["segment_id"]: c.get("transcript", "")[:300] for c in catalog},
    }
    data = _call_json(CRITIC_SYSTEM, json.dumps(payload, indent=2))
    if not isinstance(data, dict):
        return {"approved": True, "feedback": ""}
    return {
        "approved": bool(data.get("approved", True)),
        "feedback": str(data.get("feedback", ""))[:500],
    }
