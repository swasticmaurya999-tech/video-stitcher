"""Prompt + JSON contract shared by the cloud planners (Gemini, Groq)."""
from __future__ import annotations

import json

SYSTEM = (
    "You are an expert video editor. You are given a CATALOG of candidate clips extracted from raw "
    "footage (each with an id, its available in/out time in seconds, a quality score 0-1, optional "
    "visual tags, and any spoken transcript). Your job is to design ONE coherent, production-quality "
    "edited video.\n"
    "First infer the most fitting video TYPE from the footage (e.g. advertisement, promo, travel "
    "montage, event recap, product demo, explainer, or — if the footage is incoherent — a 'highlight "
    "reel'). Then design a logical storyboard and select/arrange clips into it. Favor high-score, "
    "visually clear clips; avoid cutting mid-sentence when a transcript is present; do not invent "
    "segment ids; keep each beat's in/out within that segment's available in/out.\n"
    "Return STRICT JSON only, matching the schema. No prose outside JSON."
)

SCHEMA_HINT = {
    "detected_genre": "string",
    "theme": "string",
    "confidence": "0..1",
    "beats": [
        {
            "role": "hook|body|benefit|cta|...",
            "intent": "why this clip is here",
            "segment_id": "id from catalog",
            "in": "seconds (>= segment in)",
            "out": "seconds (<= segment out)",
            "target_seconds": "desired length",
            "transition": "cut|crossfade",
        }
    ],
    "transitions": ["cut", "..."],
    "music_mood": "string",
    "title_text": "string",
    "cta_text": "string",
    "rationale": "2-3 sentences explaining the edit",
}


def build_user_prompt(catalog: list[dict], brief: str | None, target: int) -> str:
    parts = [
        f"TARGET DURATION: about {target} seconds (hard bounds: 10-120s; the system will enforce exact length).",
    ]
    if brief:
        parts.append(f"CREATIVE BRIEF (steer the edit): {brief}")
    else:
        parts.append("No brief given — infer the most fitting video type from the footage itself.")
    parts.append("SCHEMA:\n" + json.dumps(SCHEMA_HINT, indent=2))
    parts.append("CATALOG:\n" + json.dumps(catalog, indent=2))
    parts.append("Return only the JSON object.")
    return "\n\n".join(parts)
