"""Central configuration — every knob is environment-driven (pydantic-settings).

Nothing operational is hard-coded; defaults are safe for a local run with no cloud keys.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Storage ---
    storage_backend: str = "local"  # "local" | "r2"
    r2_endpoint: str = ""
    r2_access_key: str = ""
    r2_secret_key: str = ""
    r2_bucket: str = "video-stitcher"
    presign_ttl: int = 3600

    # --- LLM planners ---
    planner_chain: str = "gemini,groq,heuristic"
    gemini_api_key: str = ""
    groq_api_key: str = ""
    llm_timeout: int = 30
    enable_critic: bool = True      # 2nd LLM pass reviews the plan + refines if flagged
    critic_max_iters: int = 2

    # --- Limits ---
    max_files: int = 50
    min_files: int = 1
    max_file_size_mb: int = 100
    max_total_size_mb: int = 1024
    min_output_sec: int = 10
    max_output_sec: int = 120
    clip_seconds: float = 3.5
    min_clip: float = 1.5      # longer min clip → fewer, cleaner cuts (all crossfade-able)

    # --- Output profile ---
    target_width: int = 1920
    target_height: int = 1080
    fps: int = 30
    aspect: str = "16:9"

    # --- Feature toggles (each one is actually wired) ---
    enable_clip: bool = False       # CLIP visual tags (needs requirements-ml)
    enable_detect: bool = False     # YOLO object/person detection (needs requirements-ml)
    enable_whisper: bool = True     # speech transcription + speech-aware cuts
    enable_stabilize: bool = False  # ffmpeg deshake on shaky clips
    enable_transitions: bool = True   # crossfade dissolves between clips (vs hard cuts)
    crossfade_duration: float = 0.5   # seconds of dissolve between clips
    enable_beatsync: bool = False     # nudge cuts onto the music beats (opt-in, experimental)
    enable_endfade: bool = True       # fade in at the start + fade to black at the end
    # audio_mode: "voiceover" = keep clip speech, duck music UNDER it (preserves message — default);
    #             "music" = mute clips + music bed only;  "mix" = music under clip audio (no duck);
    #             "clips" = clip audio only, no music.
    audio_mode: str = "voiceover"
    music_path: str = ""            # force a specific track; empty → mood-picked from the library
    music_library_dir: str = "app/assets/music"
    music_volume: float = 0.35      # 0..1 — base music level (mix mode; voiceover/music auto-set)
    duck_level: float = 0.25        # reserved hint for music level while speech is present
    enable_text: bool = True        # render LLM/brand title + CTA as on-screen text (branded ad)
    enable_captions: bool = True    # burn word-synced captions of the speech (readable on mute)
    caption_fontsize_div: int = 18  # caption font size = output_height / this
    brand_font: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    # --- Analysis bounding ---
    whisper_model: str = "base"
    whisper_task: str = "translate"   # "translate" → English (robust for any language) | "transcribe"
    dedup_threshold: float = 0.82     # transcript similarity above which clips are near-duplicates
    file_dedup_threshold: float = 0.82  # full-audio similarity above which two FILES are the same
    analyze_fps: float = 1.0
    analyze_maxdim: int = 480
    max_segments_considered: int = 120

    # --- Worker / lifecycle ---
    worker_concurrency: int = 1
    max_job_attempts: int = 2
    upload_ttl_h: int = 24
    output_ttl_h: int = 24
    segment_ttl_h: int = 24
    disk_min_free_mb: int = 500
    janitor_interval_sec: int = 3600

    # --- Paths ---
    data_dir: str = "./data"
    db_path: str = "./data/app.db"
    temp_dir: str = "./data/tmp"

    # --- Derived helpers ---
    @property
    def max_file_size(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def max_total_size(self) -> int:
        return self.max_total_size_mb * 1024 * 1024

    @property
    def allowed_ext(self) -> set[str]:
        return {".mp4", ".mov", ".webm", ".mkv"}

    @property
    def planners(self) -> list[str]:
        return [p.strip().lower() for p in self.planner_chain.split(",") if p.strip()]

    @property
    def groq_keys(self) -> list[str]:
        """GROQ_API_KEY may be a comma-separated list (multiple accounts → main + fallback)."""
        return [k.strip() for k in self.groq_api_key.split(",") if k.strip()]

    @property
    def dims(self) -> tuple[int, int]:
        """Target (width, height) for the configured aspect ratio."""
        if self.aspect == "9:16":
            return (1080, 1920)
        if self.aspect == "1:1":
            return (1080, 1080)
        return (self.target_width, self.target_height)

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.temp_dir):
            Path(p).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s


settings = get_settings()
