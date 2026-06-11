# Walkthrough — Video Stitcher

A backend service that takes up to 50 raw videos and produces **one coherent, production-quality
edited video** (10–120s). This document explains the architecture, the generation logic, the key
decisions and trade-offs, and what I'd improve with more time. (`DESIGN.md` is the full decision
log; this is the reader-friendly distillation.)

---

## 1. Problem & approach

The task isn't "stitch clips" — it's **automated video editing**: understand the footage, decide a
logical edit, and render it cleanly. I split that into a **multi-stage pipeline** where *heavy
understanding is local and free*, the *AI does only cheap editorial reasoning over text*, and
*deterministic code guarantees the hard constraints*. The result: an intelligent editor that is
robust, cheap to run, and never hard-depends on a cloud service.

## 2. Architecture

```
Client ──HTTP──▶ FastAPI ──writes──▶ SQLite (jobs · files · segments · queue)
                    │                      ▲
                    │ enqueue              │ claim oldest queued (atomic)
                    ▼                      │
            Background worker ────runs────▶ Pipeline ──ffmpeg/AI──▶ storage (R2/local)
                    │                                                    │
            Janitor (TTL sweep, crash recovery)              presigned download URL
```

- **Async, job-based** (DESIGN §1): `POST /api/jobs` validates + enqueues and returns `202` with a
  `job_id` instantly; a background worker processes one job at a time; the client polls. Long
  ffmpeg/AI work never blocks an HTTP request.
- **Single deployable** runs the API + worker; **SQLite** is both the job store and the durable
  queue. `WORKER_CONCURRENCY=1` is deliberate backpressure for a small box; the queue absorbs load.
- **Separation of concerns**: `api/` (HTTP) · `db/repo` (persistence) · `storage/` (bytes, swappable
  local↔R2) · `worker` (lifecycle) · `pipeline/` (stage-isolated work) · `pipeline/plan/` (swappable
  brains).

## 3. The generation pipeline (the core)

```
INGEST → SEGMENT → ANALYZE → CATALOG → PLAN → ENFORCE → RENDER → UPLOAD
```

1. **Ingest** — download inputs to temp, `ffprobe` each (authoritative validation); corrupt /
   no-video / zero-duration files are **skipped with a reason**, not fatal.
2. **Segment** — PySceneDetect splits each video at natural **shot boundaries** so cuts are clean,
   not mid-action. Single-take footage gracefully becomes one segment.
3. **Analyze** — per-segment **quality scoring** (sharpness, exposure, motion via OpenCV) drives
   *which footage deserves screen time*; **Whisper** gives transcripts + word timestamps for
   speech-aware cuts; optional CLIP tags / object detection enrich understanding. All local, no
   quota.
4. **Catalog** — a compact, text-only description of the footage for the planner (keeps tokens tiny).
5. **Plan** — the editorial brain produces a **storyboard**: detected genre, ordered beats, casting
   (which clip → which beat), and a rationale. The system **infers the video type** when no brief is
   given, and falls back to a clean highlight reel for incoherent footage.
6. **Enforce** — deterministic: validate every beat against real segment bounds, **snap cuts to
   speech/silence boundaries**, and run **clamp + water-fill** to land the duration *exactly* in
   `[10, 120]` — or fail honestly if there isn't 10s of footage.
7. **Render** — normalize each clip to a uniform profile (scale/pad, fps, `loudnorm` for consistent
   audio), persist the normalized clips, then **concat** (copy — fast) and upload.

### Clip selection & duration logic
- **Target** = `clamp(N × 3s, 10, 120)` by default (auto-scales with #videos), or a user-supplied
  value, validated to the window.
- **Allocation** = water-fill: even share per clip, capped by each clip's length, with the shortfall
  from short clips **redistributed** to clips with headroom — so we hit the target whenever the
  footage exists. (Unit-tested in `tests/test_allocate.py`.)
- **Guarantee** = the upper bound falls out of `clamp`; the lower bound from `effective = min(target,
  total footage)` with an honest failure below 10s. Enforced in code, never trusted to the LLM.
  (Unit-tested in `tests/test_enforce.py`.)

## 4. The AI layer (and why it survives a free tier)

- **We never send video/frames to the LLM.** Understanding is local; the LLM sees only a few KB of
  **text** and returns a JSON storyboard. One cheap call per job.
- **Multi-provider failover** (`pipeline/plan/chain.py`): **Gemini → Groq → heuristic**. Cloud
  failures (quota/timeout/invalid JSON) fall through to the next provider; the **heuristic planner
  never fails**, so the system always produces a video. Each planner returns the *same* validated
  `Plan`, so the renderer is provider-agnostic.
- **No single point of failure**; the AI is an enhancement, not a dependency.

## 5. Robustness, validation & errors
- **Two-tier validation** (DESIGN §3): structural errors (count, extension, size) fail fast with a
  `4xx` at upload — size is enforced **mid-stream**, not from a spoofable header; content errors
  (corrupt/zero-length) are **skipped with a per-file reason** during processing.
- **Consistent error envelope** with correct codes (`TOO_MANY_FILES`, `PAYLOAD_TOO_LARGE`,
  `NOT_READY`, `OUTPUT_EXPIRED`, …).
- **Crash recovery**: interrupted jobs are re-queued on boot (idempotent regeneration); a job fails
  only after exhausting retries or on terminal bad input.
- **Edge cases** handled explicitly: single-take videos, no-speech clips, hallucinated LLM segment
  refs (dropped), insufficient footage (honest fail), one bad clip mid-render (skipped, not fatal).

## 6. Resource awareness
- **Streaming uploads** (chunked, never whole files in RAM); **size caps** per-file and per-batch;
  **disk-pressure `503`** guard.
- **Bounded analysis**: frame sampling + downscaling, top-K candidate segments, models loaded once
  and kept warm.
- **Lifecycle**: temp scratch deleted after each job; raw uploads dropped on completion; outputs +
  persisted segments expire by TTL / R2 lifecycle. *Every byte has an owner and an expiry.*
- **Persisted normalized segments** make future reorders/variants cheap re-concatenation.

## 7. Storage & deployment
- **Cloudflare R2** (S3-compatible via boto3) — **zero egress**, so the download-to-temp round trips
  the pipeline needs are free; the local-disk backend is a drop-in for dev/offline (same interface).
- **Hugging Face Spaces (Docker, 16 GB)** — the rare free tier with enough RAM for the ML models
  (the smallest app-PaaS free tiers can't load them). One always-on container runs API + worker.

## 8. Key decisions & trade-offs
- **Async + queue over blocking** — correct for minutes-long jobs; serial processing protects a
  small box.
- **LLM for the thinking, deterministic code for the guarantees** — best of both: creative editing
  *and* a provable duration window.
- **Match the tool to the scale** — SQLite (not Redis/Celery), R2 (not a managed video API),
  heuristic fallback (not a hard cloud dependency). Each has a documented upgrade path.
- **Honest accuracy** — this mirrors how modern tools (Opus Clip, Descript, Adobe Sensei) work;
  their *accuracy* comes from proprietary models trained on millions of edits + templates + feedback
  loops, which this approximates with free off-the-shelf models. Named, not hidden.

## 9. What I'd improve with more time
- **Production polish ladder** (designed, not yet built): beat-synced **crossfades**, burned
  **captions** from Whisper word timings, a **music bed** with ducking, **logo/CTA** cards.
- **Multiple ad variants (A/B)** and **regenerate-with-feedback** — cheap now thanks to persisted
  segments (re-concat only).
- **Subject-aware auto-reframe** for true 9:16 vertical (vs. letterbox/pad).
- **Presigned direct-to-R2 uploads** to offload upload bandwidth from the box.
- **Scale-out**: Redis + Celery workers + object storage already-decoupled → horizontal scaling.
- **More tests**: an ffmpeg-backed integration test on sample clips in CI.
