# Video Stitcher — Walkthrough

**What it does:** Upload up to 50 videos → the service automatically edits them into **one coherent,
ad-grade short (10–120s)** — speech preserved with music ducked under it, captions, clean
transitions — then you download it. Backend-focused; fully async.

**Live:** https://swasticmaurya999-video-stitcher.hf.space  ·  **Stack:** Python/FastAPI · ffmpeg ·
SQLite · faster-whisper · Groq LLM · Cloudflare R2 · Docker on Hugging Face Spaces.

---

## 1. Architecture (async, single deployable)
```
client ──POST /api/jobs──▶ FastAPI ──enqueue──▶ SQLite (jobs + queue)
                              │                       ▲
        poll GET /api/jobs/id │        claim oldest   │
                              ▼                        │
                      background worker ──ffmpeg/AI──▶ R2 (presigned download)
```
- `POST /api/jobs` validates + enqueues and returns **202 + job_id instantly**; a background worker
  processes; the client **polls** for status. Long ffmpeg/AI work never blocks an HTTP request.
- **SQLite is both the job store and the durable queue.** `WORKER_CONCURRENCY=1` = deliberate
  backpressure for a small box (ffmpeg is CPU/RAM-heavy).
- Clean separation: `api/` · `db/` · `storage/` (swappable local↔R2) · `worker` · `pipeline/` ·
  `pipeline/plan/` (swappable LLM/heuristic planners).

## 2. Generation pipeline (the core)
`ingest → segment → analyze → plan → enforce → render`
- **Ingest** — `ffprobe` validates; corrupt / zero-length files are **skipped and reported**, not fatal.
- **Segment** — PySceneDetect splits each video at **shot boundaries** (clean cuts). **File-level
  audio dedup** drops a duplicate ad that shares one voiceover (prevents the same audio twice).
- **Analyze** (local, free) — quality scoring (sharpness/exposure/motion), **Whisper** transcript +
  word timestamps (any language → English), optional CLIP/YOLO tags; near-duplicate dedup.
- **Plan** (LLM) — a text-only catalog → a **storyboard** (genre, ordered beats, casting, title/CTA,
  music mood). **Multi-provider failover** (Groq, multiple keys → heuristic). An **agentic critic
  pass** reviews the plan for repetition/coherence and refines it.
- **Enforce** (deterministic) — validate beats, **snap cuts to silence** (start + end), anti-repetition
  guard, **water-fill durations → guaranteed [10,120]s** or honest failure.
- **Render** (ffmpeg) — normalize clips → crossfade-concat → **keep-speech audio with music ducked
  under it** → burned captions → brand title/CTA → fade-out ending → upload to R2.

## 3. Clip-selection & duration logic
- **Target** = `clamp(N × 3.5, 10, 120)` (auto-scales with #videos) or a user-supplied value.
- **Water-fill allocation** (weighted by the LLM's intended pacing): each clip capped at its real
  footage; shortfall from short clips redistributes to the rest → hits the target. Extend/trim keeps
  the result in **[10,120]s by construction**; only a genuine <10s of total footage fails.

## 4. AI layer — local understanding, text-only reasoning, never trusted blindly
- Heavy *understanding* runs **locally and free** (Whisper/CLIP/scoring). The LLM only sees a few KB
  of **text** → **one cheap call per job** → the free tier survives heavy use.
- **Failover** (Groq keys → heuristic) means no single point of failure — the system always produces
  a video. The **deterministic enforcer** owns the duration guarantee and anti-repetition, so a bad
  LLM response can't break the output.

## 5. Robustness, validation & errors
- **Two-tier validation:** structural (count / extension / size) → fail-fast `4xx` at upload (size
  enforced **mid-stream**, not from a spoofable header); content (corrupt / zero-length) → **skipped
  with a per-file reason**.
- Consistent error envelope `{"error":{code,message}}` with correct status codes; **crash recovery**
  re-queues interrupted jobs on boot; one bad clip is skipped, not fatal.

## 6. Resource awareness
- Streaming chunked uploads; **bounded analysis** (frame sampling, top-K segments, models loaded
  once); **R2 storage** (zero egress); **persisted normalized segments** (make variants/reorders
  cheap); TTL cleanup + a disk-pressure `503` guard. *Every byte has an owner and an expiry.*

## 7. Key decisions & trade-offs
- Async + queue over blocking; **SQLite over Redis/Celery**; **R2 over a managed video API**;
  **keep-speech + duck over muting** (preserves the message) or raw splicing (jarring); local CV+LLM
  over a paid vision API. Each = *match the tool to the scale*, with a documented upgrade path.
- **Honest accuracy:** this is the same architecture family as Opus Clip / Descript / Adobe Sensei;
  their accuracy edge comes from proprietary models trained on millions of edits + feedback loops,
  which I don't have — named, not hidden.

## 8. What I'd improve with more time
- TTS voiceover option; subject-aware **vertical auto-reframe** (9:16); **multiple A/B variants**
  (near-free thanks to persisted segments); beat-synced cuts (built, opt-in); and **Redis+Celery +
  presigned direct-to-R2 uploads** for horizontal scale.

*(Music: CC-BY tracks by Kevin MacLeod, incompetech.com — credited in the README.)*
