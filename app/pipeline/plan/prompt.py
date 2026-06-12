"""Prompt + JSON contract shared by the cloud planners (Gemini, Groq)."""
from __future__ import annotations

import json

SYSTEM = (
    "You are an expert advertising video editor. You are given a CATALOG of candidate clips extracted "
    "from raw footage (each with an id, available in/out time in seconds, a quality score 0-1, optional "
    "visual tags, and any spoken transcript — transcripts may be translated to English). Design ONE "
    "coherent, production-quality edited video.\n\n"
    "RULES:\n"
    "1. GENRE: Infer the most fitting type and commit to it. Treat it as an ADVERTISEMENT/PROMO if you "
    "see signals like a brand name, sale/offer/discount, product shots, prices, a call-to-action, or "
    "promotional speech. Only use 'highlight reel' when the footage is genuinely incoherent. Set a "
    "realistic confidence.\n"
    "2. NO REPETITION: Never select two clips that say the SAME thing or show the SAME scene. If "
    "multiple clips share the same spoken line or near-identical visuals, pick only the single best "
    "one. Maximize content variety.\n"
    "2b. ONE COHERENT MESSAGE: the whole edit must tell ONE clear story — a viewer should instantly "
    "get the product and the offer. Every beat must advance that story; drop anything that doesn't. "
    "Make consecutive beats connect logically (don't jump randomly between unrelated moments).\n"
    "3. STRUCTURE (for ads): hook (grab attention) → product/value → offer/benefit → call-to-action. "
    "Order beats to tell that story.\n"
    "4. PACING: set each beat's target_seconds to create rhythm — a slightly longer hook, punchier "
    "middle cuts. The system enforces the final total duration, so target_seconds is RELATIVE pacing.\n"
    "5. CLEAN CUTS & COMPLETE THOUGHTS: keep in/out within the segment bounds; each chosen clip "
    "should contain a COMPLETE spoken sentence/thought (use the transcript to choose in/out at "
    "sentence boundaries), never a cut-off half-sentence. Order beats so the spoken lines read as a "
    "continuous, sensible script when played back-to-back.\n"
    "6. BRAND TEXT: set title_text to the brand/product name (shown on screen) and cta_text to a short "
    "call-to-action, when the footage implies them.\n"
    "Do not invent segment ids. Return STRICT JSON only, matching the schema. No prose outside JSON."
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
