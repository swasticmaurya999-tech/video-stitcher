"""Lazy, cached loaders for the heavy ML models.

Each model is loaded once on first use and kept warm for the process lifetime. Every loader is
defensive: if a dependency is missing or load fails, it returns None and the caller degrades
gracefully (the system always works, just with less understanding).
"""
from __future__ import annotations

import logging
from functools import lru_cache

from app.config import settings

log = logging.getLogger("ml")


@lru_cache(maxsize=1)
def get_whisper():
    if not settings.enable_whisper:
        return None
    try:
        from faster_whisper import WhisperModel

        return WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")
    except Exception as e:  # pragma: no cover
        log.warning("Whisper unavailable: %s", e)
        return None


@lru_cache(maxsize=1)
def get_clip():
    if not settings.enable_clip:
        return None
    try:
        import open_clip
        import torch

        model, _, preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="laion2b_s34b_b79k"
        )
        model.eval()
        tokenizer = open_clip.get_tokenizer("ViT-B-32")
        return {"model": model, "preprocess": preprocess, "tokenizer": tokenizer, "torch": torch}
    except Exception as e:  # pragma: no cover
        log.warning("CLIP unavailable: %s", e)
        return None


@lru_cache(maxsize=1)
def get_detector():
    if not settings.enable_detect:
        return None
    try:
        from ultralytics import YOLO

        return YOLO("yolov8n.pt")
    except Exception as e:  # pragma: no cover
        log.warning("Detector unavailable: %s", e)
        return None


# A compact concept vocabulary for CLIP zero-shot tagging (extend as needed).
CLIP_LABELS = [
    "a person", "a group of people", "a smiling face", "a product close-up", "a logo",
    "food or drink", "a landscape", "a city street", "an interior space", "text on screen",
    "a vehicle", "an animal", "hands using a product", "an action or sport", "nature",
]
