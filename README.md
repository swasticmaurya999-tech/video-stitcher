---
title: Video Stitcher
emoji: 🎬
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# 🎬 Video Stitcher — AI Video Editor

Upload up to **50 videos** → an AI editor analyzes the footage, designs a logical storyboard, and
assembles **one coherent, production-quality video (10–120s)** → download it. Backend-focused;
async job processing with progress; runs fully offline (heuristic planner) or with free-tier LLMs.

> The live feature is on Hugging Face Spaces (see submission). First request after idle may take
> ~30s to warm up (cold start + model load).

---

## Quick start (one command, fully local)

```bash
docker compose up --build
# open http://localhost:8000
```

That's it — no keys required. With no LLM keys it uses the **heuristic planner** and local
analysis; everything works. To enable the cloud "brain", set keys first:

```bash
export GEMINI_API_KEY=...      # Google AI Studio (free tier)
export GROQ_API_KEY=...        # Groq (free tier)
docker compose up --build
```

### Run without Docker
```bash
python -m venv .venv && source .venv/bin/activate   # (Windows: .venv\Scripts\activate)
pip install -r requirements.txt
# optional, for CLIP/object-detection: pip install -r requirements-ml.txt
uvicorn app.main:app --port 8000
```
Requires `ffmpeg` + `ffprobe` on PATH.

---

## API

| Method & path | Purpose |
|---|---|
| `POST /api/jobs` | multipart upload of videos + optional `target_duration`, `aspect`, `brief` → `202` with a job |
| `GET /api/jobs/{id}` | poll status (stage, progress, skip report, AI rationale) |
| `GET /api/jobs/{id}/download` | `302` → presigned/served URL of the finished video |
| `GET /api/jobs` | recent jobs |
| `GET /health` | liveness |

### curl
```bash
# Upload + generate (returns a job_id)
curl -F "files=@a.mp4" -F "files=@b.mp4" -F "target_duration=20" \
     -F "brief=energetic 20s promo" http://localhost:8000/api/jobs

# Poll until status == completed
curl http://localhost:8000/api/jobs/<job_id>

# Download the result
curl -L -OJ http://localhost:8000/api/jobs/<job_id>/download
```

Errors use a consistent envelope: `{"error": {"code": "...", "message": "..."}}`.

---

## How it works (pipeline)

```
upload → S3/R2 → INGEST (ffprobe validate) → SEGMENT (shot detection) → ANALYZE (quality + CLIP
+ Whisper) → PLAN (Gemini → Groq → heuristic failover) → ENFORCE (snap cuts + clamp/water-fill →
guaranteed 10–120s) → RENDER (normalize + loudnorm + concat) → upload → presigned download
```

- **Local & free understanding** (OpenCV scoring, Whisper, optional CLIP) — no quota.
- **One cheap text LLM call/job** designs the storyboard; **deterministic code guarantees** the
  duration window and never trusts the LLM blindly.
- **Failover chain** (Gemini → Groq → heuristic) means the system always produces a video.

See **[WALKTHROUGH.md](WALKTHROUGH.md)** for the full architecture, decisions, and trade-offs, and
**[DESIGN.md](DESIGN.md)** for the decision log.

---

## Configuration (all env-driven — see `.env.example`)

Key knobs: `STORAGE_BACKEND` (local|r2), `R2_*`, `GEMINI_API_KEY`/`GROQ_API_KEY`,
`MAX_FILES`, `MAX_FILE_SIZE_MB`, `MAX_TOTAL_SIZE_MB`, `MIN_OUTPUT_SEC`/`MAX_OUTPUT_SEC`,
`ASPECT` (16:9|9:16|1:1), `WORKER_CONCURRENCY`, `AUDIO_MODE` (voiceover|music|mix|clips), and feature
toggles `ENABLE_WHISPER/CAPTIONS/TRANSITIONS/CRITIC/CLIP/DETECT/STABILIZE/BEATSYNC`.

Heavy passes are toggles so you can trade quality ↔ compute on a small box.

### What's implemented vs. roadmap
**Implemented:** async upload→generate→download, validation + skip reporting, near-duplicate dedup,
shot-boundary cuts, quality/CLIP/object-aware selection, speech-aware cuts (Whisper, any language →
English), water-fill duration guarantee, LLM storyboard with Gemini→Groq→heuristic failover, an
**agentic critic loop** (review + refine), **keep-speech audio with music ducked under it**,
**mood-matched music variety**, **burned word-synced captions**, **crossfade transitions**,
**beat-synced cuts** (opt-in), **brand title + CTA text**, loudness normalization, optional
stabilization, R2/local storage, persisted segments, TTL cleanup, crash recovery.
**Roadmap (not built):** TTS voiceover, logo-image overlay, multiple A/B variants, subject-aware
vertical reframe. (See WALKTHROUGH §9.)

> **Music credit:** bundled tracks in `app/assets/music/` are by **Kevin MacLeod** (incompetech.com),
> licensed [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/). The system mood-matches and
> rotates them per output; override with `MUSIC_PATH`, or change behavior with `AUDIO_MODE`
> (`voiceover` | `music` | `mix` | `clips`).

---

## Tools used

| Tool | Why |
|---|---|
| **Python + FastAPI + uvicorn** | async API, first-class uploads, fast to build/read |
| **ffmpeg / ffprobe** | the media engine — trim/normalize/concat + validation |
| **SQLite** | durable jobs + queue + metadata, zero external infra |
| **PySceneDetect, OpenCV** | shot detection + quality/motion scoring + perceptual-hash dedup |
| **faster-whisper** | speech transcription + word timestamps (speech-aware cuts + captions) |
| **librosa** | beat detection for beat-synced cuts |
| **open-clip, ultralytics** *(optional, `requirements-ml.txt`)* | visual tags + object/person detection |
| **Gemini / Groq (free tier)** | the editorial "brain" (storyboard), with heuristic fallback |
| **Cloudflare R2 (boto3)** | S3-compatible object storage, zero egress |
| **Docker / Hugging Face Spaces** | packaging + a free 16 GB host for the ML workload |
| **pytest** | unit tests for the duration math + API |

---

## Tests
```bash
pip install pytest && python -m pytest tests/ -q
```
