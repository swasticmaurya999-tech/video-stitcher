# Video Stitcher — Full Project Context

> **Purpose of this file:** a complete, self-contained briefing so any assistant can understand
> the project accurately and answer questions about it — including questions that are *scoped out*
> of the build, and adjacent/“what-if” questions. Read top to bottom; everything needed is here.

> **⚡ IMPORTANT — READ FIRST:** After a walkthrough call the scope expanded a lot. The project is
> no longer a simple "stitch clips with a duration guarantee"; it is now an **AI-powered intelligent
> video editor that produces a coherent, production-quality video (ad-grade) from raw footage**, for
> an **AI advertisement company**. The authoritative current direction is **Section 8: REVISED
> DIRECTION** near the end of this file. Sections 1–3 below are the original brief/method/decisions
> and remain valid context, but where they conflict with Section 8, **Section 8 wins**.

---

## 0. What this project is (one paragraph)

This is a take-home **Backend Engineer skill assessment** for a company called **Digital Creators**.
The task is to build and **deploy** a small but real backend service: a user **uploads several
videos (up to 50)**, the service **automatically generates one new video** stitched together from
**clips** of those uploads, and the user can **download** the result. The final video must be
**between 10 seconds and 2 minutes**. It is **backend-focused** — UI is explicitly *not* evaluated;
a bare HTML page or curl commands are fine. The work is graded on **engineering judgment, design
reasoning, robustness, resource-awareness, API design, and communication** — not on polish. There
is a **walkthrough call** mid-build and a **hard submission deadline**.

**Current state:** design is fully locked for sections 1–7 (architecture, stack, upload/validation,
generation logic, storage/lifecycle, API, minimal frontend). Sections 8 (deployment) and 9
(deliverables/docs) are not yet decided. **No code written yet** — we designed first, will implement
in one pass afterward.

---

## 1. The original assessment brief (reproduced in full)

**Role:** Backend Engineer
**Issued:** Wednesday, 10 June 2026
**Q&A / Walkthrough Meeting:** Thursday, 11 June 2026
**Submission Deadline:** Friday, 12 June 2026, 6:00 PM IST (hard deadline)
**Submit to:** ankit@thedigitalcreators.io

### 1.1 Overview
Build and deploy a small but real backend feature. The goal is not a polished product — it’s to see
how you design, build, and ship a backend service that does meaningful work under real constraints.
The feature: a user uploads several videos, and the service produces one new video stitched together
from clips of those uploads. The user can then download the result. This is backend-focused; the
frontend should be as minimal as possible. UI is not evaluated.

### 1.2 The Task
Build a service that lets a user:
1. Upload multiple videos — up to a maximum of 50 videos at a time.
2. Generate one new video automatically assembled from clips taken from the uploaded videos.
3. Download the generated video once it’s ready.

**Core requirements**
- **Upload:** Accept multiple video files in a single submission (up to 50). Validate file type and
  reasonable size limits.
- **Generation:** Produce a single output video composed of clips drawn from the uploaded videos.
  The clip-selection and stitching logic is your design decision — but you must explain it clearly
  (how clips are chosen, how long each is, ordering, how you guarantee final duration is in range).
- **Duration constraint:** The final video must be between **10 seconds and 2 minutes**. How the
  target duration is decided (fixed, user-supplied, or computed) is up to you — document your choice.
- **Download:** Provide a way for the user to retrieve the finished video.

**Constraints & things to decide for yourself (justify each):**
- Per-file and total upload size limits.
- Accepted video formats (e.g. mp4, mov, webm).
- How clips are selected and arranged into the final video.
- How the final duration is determined and enforced.
- **Note on long-running work:** video processing is slow. Think about how your API behaves while a
  video is being generated — blocking the request vs. async processing with a status/progress
  mechanism. They are interested in the reasoning.

### 1.3 “Live Feature” — deployment
The feature must be **deployed and accessible** (not just runnable locally). A free tier on any host
is fine (Render, Railway, Fly.io, AWS, GCP, etc.). Provide the live URL. If live deploy is genuinely
not feasible, give one-command local instructions — but a live URL is strongly preferred.

### 1.4 Out of Scope
- Frontend / UI polish. A bare HTML page, Postman collection, or curl commands is acceptable.
- Authentication, user accounts, billing (unless done as an optional stretch).

### 1.5 Deliverables
1. The live feature — a working, accessible URL + brief test instructions.
2. Tools used — languages, frameworks, libraries, services, with a short *why* for each.
3. Full source code — repo (GitHub/GitLab) or zip, including a README.
4. Walkthrough document — architecture, clip-selection/generation logic, key decisions & trade-offs,
   how you handled processing/validation/errors, and what you’d improve with more time.

### 1.6 How it’s evaluated
- Architecture & code quality (structure, readability, separation of concerns)
- API design (clear, correct, predictable endpoints)
- Handling of long-running processing
- Robustness (validation, error handling, edge cases — unsupported files, too many uploads,
  zero-length clips)
- Resource awareness (large files, 50 uploads, temp storage, cleanup)
- Tool choices & justification
- Documentation & communication
- It actually works

### 1.7 Optional stretch goals (not required, never at expense of core)
Progress/status polling · a real background job/queue with workers · configurable clip
duration/ordering/transitions · containerization (Docker) and/or automated tests · storage lifecycle
/ cleanup of temporary and old files.

---

## 2. Working method

We are going **section by section**: for each section we (1) lay out the decisions it forces and the
realistic options, (2) discuss the *why* and pick, (3) record the locked decision + reasoning in a
running `DESIGN.md`. After all sections are locked, we implement the whole thing in one pass. This
also means the design doc becomes the source material for the required walkthrough document.

**Section status:** 1✅ 2✅ 3✅ 4✅ 5✅ 6✅ 7✅ — 8⏳ (deployment) — 9⏳ (deliverables/docs).

---

## 3. Locked design decisions (sections 1–7)

### Section 1 — Processing model & architecture
- **Async, job-based generation.** `POST /generate` validates, creates a job, returns a `job_id`
  immediately; processing happens in the background; client polls for status, then downloads.
  Chosen over blocking because long ffmpeg jobs (30s–minutes for 50 videos) exceed
  free-tier/proxy request timeouts (~30–60s), give no progress visibility, and tie up a worker.
- **In-process background worker over a durable SQLite-backed queue.** One deployable runs both the
  web API and a background worker loop that pulls the oldest `queued` job and runs ffmpeg as a
  subprocess. No external infra (no Redis). ffmpeg as a subprocess doesn’t block the event loop.
- **SQLite for job state + as the queue.** Durable across restarts, zero external service. Holds
  metadata only; video bytes live on disk.
- **Job lifecycle:** `queued → processing → completed | failed` (errors captured & surfaced).
- **Concurrency model:** API layer is **unbounded** (many concurrent users). Generation is
  **bounded, default 1** (`WORKER_CONCURRENCY`) — a single worker drains the queue serially; others
  wait in `queued` (no rejection). Reason: ffmpeg is CPU/RAM-heavy; a free-tier box is ~1 vCPU /
  256–512 MB; running many ffmpeg jobs at once would thrash/OOM and fail everyone’s job. A queue
  with bounded concurrency is correct backpressure: accept all work, drain at a sustainable rate.

**When to upgrade to Redis + Celery (scaling story):** triggers are needing >1 machine, throughput
exceeding one box, independent scaling of web vs workers, automatic retries/timeouts,
priorities/scheduling, or queue observability. Redis = shared broker so any worker on any machine
grabs the next job (SQLite is a local file, not shareable). Celery = worker framework giving
retries/timeouts/acks/concurrency. **Scaling ladder:** (0) as built; (1) vertical first — bigger box,
raise `WORKER_CONCURRENCY`; (2) decouple storage → object storage (S3/GCS) so files aren’t tied to
one machine’s disk; (3) externalize queue → Redis + Celery, split web/workers, autoscale workers on
queue depth (video logic unchanged); (4) remaining bottlenecks — upload bandwidth via presigned
direct-to-S3, ffmpeg CPU via GPU/split-and-merge, metadata → Postgres, global users → CDN. Principle:
find the real bottleneck, fix that one, re-measure; don’t build the distributed version up front.

### Section 2 — Tech stack & tools
| Capability | Tool | Why |
|---|---|---|
| Language + framework | **Python + FastAPI** | Async-native, first-class `UploadFile` streaming, trivial subprocess control, `sqlite3` in stdlib; optimized for dev speed + readability under deadline |
| ASGI server | **uvicorn** | Standard FastAPI server; concurrent requests |
| Video processing | **ffmpeg** (CLI via `subprocess`) | Industry-standard engine; called directly (not via wrapper) for transparency, low overhead, debuggable commands |
| Video inspection | **ffprobe** | Reads duration/codec before cutting; powers duration math + zero-length/corrupt rejection |
| Job store + queue | **SQLite** (stdlib) | Durable, no external infra; thin data-access module, no ORM |
| Background worker | **Daemon thread** | Isolates blocking poll/subprocess loop from async event loop |
| Upload parsing | FastAPI **`UploadFile`** | Streams to disk in chunks; no loading 50 files into RAM |
| Config | **pydantic-settings / env vars** | All limits/knobs from env; nothing hard-coded |
| Packaging | **Docker** | Guarantees ffmpeg present in deploy; stretch goal |
| Tests (stretch) | **pytest** | Unit-test pure clip/duration math + API smoke test |

**Deliberately omitted (restraint = judgment):** ORM, Celery/Redis, frontend framework, ffmpeg
wrapper lib. Each omission = “matched tool to scale.”

**Why Python over alternatives:** the perf-critical path is ffmpeg (native subprocess) — every
language calls the same binary and waits the same time, so host-language raw speed is off the
critical path. So optimize for dev velocity, subprocess/upload ergonomics, async, readability.
Node/TS viable but ffmpeg tooling (`fluent-ffmpeg`) semi-abandoned and `child_process` clunkier;
Go’s speed edge is moot (ffmpeg-bound), more boilerplate, and its big-concurrency strength is the
one thing we deliberately bound; Rust over-engineering for a 2-day task; Java/Rails/PHP heavyweight
/ weaker fit for subprocess media + async background work.

**Why ffmpeg over alternatives:** nearly every alternative wraps ffmpeg anyway. Direct use gives the
right primitive (trim+concat+normalize native), zero cost/no vendor/no keys, lowest overhead
(matters on a small box with 50 inputs), transparency, universal format support, and keeps the core
engineering in our codebase. MoviePy/PyAV/ffmpeg-python wrap ffmpeg and add overhead/obscure errors;
OpenCV is frame-analysis not concat; GStreamer is complex; managed APIs (Shotstack/Cloudinary/Mux/
AWS MediaConvert) would **outsource the exact part the assessment asks us to build** (plus
cost/keys/lock-in); HandBrake/MEncoder/avconv wrong shape or stale.

### Section 3 — Upload & validation
- **Bounds:** max 50 files (>50 → `400`); min 1 (single source still works).
- **Format verification — two layers, never trust extension/MIME:** (1) cheap gate at upload —
  extension in allowlist `mp4 / mov / webm / mkv`; (2) authoritative at generation — **ffprobe** must
  report a decodable video stream with duration > 0. ffprobe also yields per-file duration for the
  clip math (validation + generation-prep are the same step). No `python-magic` — ffprobe is the
  better authority.
- **Size limits (resource awareness):** per-file 100 MB (`MAX_FILE_SIZE`), total batch 1 GB
  (`MAX_TOTAL_SIZE`). Total is the real guard (fits small ephemeral disk). **Enforced during
  streaming**, not from `Content-Length` (header can lie): count bytes per chunk, abort + clean up
  the instant a cap is exceeded.
- **Streaming & safety:** chunked streaming to disk (~1 MB) via `UploadFile` (never load 50 files
  into RAM); **UUID filenames** on disk, original name kept as metadata only (no path traversal).
- **Two-tier failure policy:** structural errors → fail-fast synchronous `400` at upload (too many
  files, bad extension, oversize, empty); content errors → **lenient skip** at generation
  (ffprobe-invalid files skipped, rest proceed). Fail whole job only if 0 usable videos remain.
- **Skip reporting (transparency):** job status surfaces `total_uploaded`, `used`, `skipped`, and
  `skipped_files[]` with a human-readable `reason` per file (count + which + why), visible during and
  after processing. Hard failure only on zero usable inputs.
- **Data model — two tables (batch = job):** one submission (≤50 videos) = one **job** producing one
  output; one job has many files.
  ```
  jobs(id PK, status, target_duration, total_uploaded, used_count, skipped_count,
       output_path NULL, error NULL, created_at, updated_at)
  files(id PK, job_id FK→jobs.id, original_name, stored_path, size_bytes,
        duration REAL NULL, status[pending|used|skipped], skip_reason NULL)
  ```
  `files.job_id` records which batch a video came in; `files.status`/`skip_reason` its outcome.
  Chosen over a single-table JSON manifest for normalization, queryability, separation of concerns.
  Bytes on disk; DB holds metadata only. Not stored (no purpose in scope): users/auth, cross-batch
  history, analytics.

### Section 4 — Generation logic (clips + duration) — the core IP
- **4A Target duration — computed by default, user-overridable.** Default
  `target = clamp(N × CLIP_SECONDS, 10, 120)`; N = usable videos, `CLIP_SECONDS` default 3 (config).
  `clamp` is where the 10–120s hard constraint mechanically lives (below 10 → bump to 10; above 120 →
  cap to 120). Override: user-supplied `target_duration` validated to `[10,120]` else `400`. Satisfies
  the configurable-duration stretch goal.
- **4B Clip length — even share + water-filling redistribution.** `base = T / M`; each clip capped at
  its source duration (`min(base, d_i)`). Short videos cap out and release leftover budget,
  redistributed to videos with headroom:
  ```
  open = all M videos; budget = T
  loop:
      share = budget / len(open)
      constrained = {v in open : d_v < share}
      if constrained: assign L_v=d_v, budget-=d_v, remove v; repeat
      else: assign L_v=share to all open; done
  ```
  Terminates in ≤M passes; hits T exactly when total footage ≥ T, else everyone caps at d_i.
  Round to 0.1s; last clip absorbs rounding so the sum is exact. Example: T=15, d=[1,2,3,50,50] →
  [1,2,3,4.5,4.5]=15.
- **4C Subset selection — Option A (watchable, even-sample).** `MIN_CLIP=1s`,
  `max_clips=floor(T/MIN_CLIP)`. If `N > max_clips`, evenly sample `max_clips` videos spread across
  upload order (represents whole batch); featured/dropped reported. Chosen over “include everything”
  because sub-second clips flicker/look broken; we already have reporting to surface which were used.
- **4D Clip position — from the start (offset 0).** Deterministic, explainable. Documented (not
  default) enhancement: sample from a small offset/middle to skip intros & black frames.
- **4E Ordering — upload order.** Stable, predictable, user-controllable. Random shuffle/transitions
  = stretch.
- **4F Duration guarantee ∈ [10,120].** Upper: `T ≤ 120` (clamped) and `ΣL_i ≤ T` → ≤120.
  Lower: `effective = min(T, total_usable_footage)`; ≥T footage → ~T; 10–T footage → all footage;
  **<10s footage → fail** (“Insufficient footage: Xs total, minimum output is 10s”). No
  loop/freeze-frame padding — honest failure over deceptive output.
- **4G Technical stitching — normalize-then-concat (robust).** Arbitrary uploads differ in
  codec/resolution/fps/pixfmt, so fast `concat -c copy` of raw inputs breaks. Two-pass:
  (1) **Normalize each clip** in one ffmpeg pass: trim `-ss 0 -t L_i` + re-encode to common profile —
  scale+pad **1280×720**, **30fps**, `yuv420p`, **H.264**, **AAC 44.1k stereo**; **synthesize silent
  audio if source has none** (uniform streams for concat). (2) **Concat demuxer `-c copy`** the
  normalized clips → final **MP4 `+faststart`** (web-streamable). Two-pass chosen over a single
  50-input `filter_complex`: bounded memory, per-clip **progress reporting** (clip 12/47), failure
  isolated to one clip. Resource-aware.

### Section 5 — Storage & resource lifecycle
- **Disk layout (job-scoped):**
  ```
  storage/uploads/{job_id}/{file_uuid}.ext   ← raw uploads (≤50)
  storage/work/{job_id}/clip_000.mp4 …       ← normalized intermediates (transient)
  storage/outputs/{job_id}.mp4                ← final deliverable
  ```
  Grouped by job_id so cleanup = one recursive delete.
- **Bytes vs metadata:** videos = real files on local disk; SQLite stores metadata + a `stored_path`
  pointer only (no BLOBs — DBs are bad at large binaries; filesystem is purpose-built). Upload
  journey: `UploadFile` stream → chunked write (size enforced mid-stream) → insert `files` row.
- **Intermediates:** delete `work/{job_id}/` the instant concat consumes it. Considered stream/pipe
  to lower peak disk but rejected: concat demuxer needs seekable files; real intermediates give
  failure isolation, progress, debuggability; disk cost already bounded by immediate deletion.
- **Upload cleanup:** on terminal state, delete `uploads/{job_id}/` immediately — steady state stores
  outputs only.
- **Output retention (TTL + janitor):** outputs expire **24h** after creation (`OUTPUT_TTL_HOURS`); a
  periodic janitor (hourly, in worker) deletes expired outputs + orphaned dirs; download after expiry
  → **`410 Gone`**.
- **Disk-pressure guard:** if free disk < threshold, reject new uploads with **`503`** (disk-level
  backpressure mirroring the queue’s CPU-level backpressure).
- **Crash recovery:** on startup, jobs stuck in `processing` are **re-queued** (inputs still on disk;
  regeneration is idempotent into the same output path); startup janitor sweeps orphaned `work/` dirs.
- **Storage interface (scaling hook):** file ops behind a thin module (`save/open/delete/url_for`) so
  swapping local disk → S3/GCS is a one-file change.
- **Why not S3 now:** single box → no shared-storage problem to solve; S3 adds account/IAM/keys/SDK/
  network failure mode for zero benefit at this scale; ffmpeg needs local bytes anyway; cost is a
  footnote (free tier exists). Same “match tool to scale” discipline as deferring Redis/Celery.
  Theme: **every byte has an owner and an expiry** — nothing accumulates unbounded.

### Section 6 — API design
Principles: predictable shapes, correct status codes, async-first (never blocks).
- **One-step flow:** `POST /api/jobs` uploads videos **and** triggers generation in one call.
- **Endpoints:**
  | Method & path | Purpose | Success |
  |---|---|---|
  | `POST /api/jobs` | Multipart upload + optional `target_duration`; validate, store, enqueue | **202** |
  | `GET /api/jobs/{id}` | Poll job status | 200 |
  | `GET /api/jobs/{id}/download` | Stream finished video | 200 |
  | `GET /api/jobs` | List recent jobs (newest first, cap 50, no pagination) — demo page | 200 |
  | `GET /health` | Liveness (`{"status":"ok"}`) | 200 |
  `DELETE /api/jobs/{id}` skipped unless time permits. `202` = correct “accepted for async”.
- **Job object (same shape from POST & GET):**
  ```json
  { "job_id","status":"queued|processing|completed|failed","target_duration",
    "progress":{"stage","current","total"},"total_uploaded","used","skipped",
    "skipped_files":[{"filename","reason"}],"output_duration","download_url","error",
    "created_at","updated_at" }
  ```
  Client polls, watches `status`; `download_url` fills on `completed`, `error` on `failed`.
  `progress` (clip X/Y) covers the progress-reporting stretch goal.
- **Consistent error envelope:** `{"error":{"code","message"}}` everywhere. Code map: >50→400
  TOO_MANY_FILES; empty→400 NO_FILES; bad ext→415 UNSUPPORTED_MEDIA_TYPE; oversize→413
  PAYLOAD_TOO_LARGE; bad duration→400 INVALID_DURATION; disk→503 STORAGE_UNAVAILABLE; unknown→404
  JOB_NOT_FOUND; download-while-processing→409 NOT_READY; failed→409 JOB_FAILED; expired→410
  OUTPUT_EXPIRED. (409 for not-ready chosen over niche 425 Too Early.)
- **Download:** `FileResponse` with `Content-Type: video/mp4`, `Content-Disposition: attachment;
  filename="stitched-{id}.mp4"`, `Accept-Ranges: bytes` + `Content-Length` (seekable/resumable);
  streams from disk.
- **Polling over websockets:** client polls `GET /jobs/{id}` (~2s, documented not enforced);
  stateless, curl-friendly. SSE/websockets noted as upgrade for live progress.
- **Micro-defaults:** `target_duration` = optional multipart form field; same-origin HTML page → no
  CORS; no auth/rate-limiting (per brief; 503 disk guard is the only backpressure) — a decision, not
  an omission.

### Section 7 — Minimal frontend / test harness
Out of scope for grading. No framework, no build step. Deliverables: a **single static HTML page**
(vanilla JS) served by FastAPI at `GET /` (same-origin, one deployable) driving upload → poll
(status + progress + skip report) → download link + inline `<video>` preview + recent-jobs list; plus
**curl examples in the README**. Skip Postman and any frontend framework.

---

## 4. Still open (not yet decided)

### Section 8 — Deployment (NOT yet decided)
Needs a choice of host (Render / Railway / Fly.io / AWS / GCP — free tier). Key constraints that will
drive it: must run **ffmpeg** (bundle via Docker), needs **CPU + disk + long timeouts** (short-timeout
serverless is a poor fit), needs the background worker to keep running. Persistent vs ephemeral disk
matters (we designed for ephemeral + TTL). Depends on what accounts the candidate already has.

### Section 9 — Deliverables & docs (NOT yet decided)
README, the walkthrough document (this design doc is its source material), the tools-with-rationale
list, repository setup, one-command run instructions (likely `docker compose up`), and packaging for
submission.

---

## 5. Cross-cutting themes & talking points (for answering scoped-out / adjacent questions)

- **“Match the tool to the scale.”** The recurring discipline: we know the production-grade tool
  (Redis+Celery, S3, websockets, Postgres) AND the exact threshold where it earns its place, and we
  deliberately don’t reach for it before then. Over-engineering is a negative signal; restraint with
  a documented upgrade path is a positive one.
- **Async-first.** The API never blocks on video work; everything heavy goes through the job queue.
- **Backpressure at every constrained resource:** CPU → bounded worker concurrency + queue; disk →
  503 upload guard + TTL janitor.
- **Every byte has an owner and an expiry:** uploads die when the job ends, intermediates when
  consumed, outputs on TTL (24h), orphans get swept on restart. Nothing grows unbounded.
- **Honest failure over deceptive success:** insufficient footage fails with a clear message rather
  than padding/looping to fake the minimum duration.
- **Transparency:** partial failures (bad files) are skipped but fully reported (count + which + why);
  the rest of the batch still succeeds.
- **Robustness via real intermediate files:** normalize-then-concat with seekable files gives failure
  isolation, progress, and debuggability — accepting a bounded, managed disk cost.
- **Scoped out per the brief (so intentionally absent):** authentication, user accounts, billing,
  UI polish. The brief allows these only as stretch.
- **Stretch goals we effectively cover for free:** status/progress polling (the `progress` field +
  poll endpoint), background job processing (the worker + SQLite queue), configurable duration
  (the `target_duration` override), Docker (packaging), storage lifecycle/cleanup (TTL + janitor).

## 6. Key parameters / config knobs (defaults)
- `MAX_FILES = 50`, min 1
- `MAX_FILE_SIZE = 100 MB`, `MAX_TOTAL_SIZE = 1 GB`
- Allowed formats: `mp4, mov, webm, mkv`
- Output duration window: **10s – 120s** (hard)
- `CLIP_SECONDS = 3` (per-video target used in the computed duration), `MIN_CLIP = 1s`
- Normalize profile: 1280×720, 30fps, yuv420p, H.264 + AAC 44.1k stereo, MP4 +faststart
- `WORKER_CONCURRENCY = 1`
- `OUTPUT_TTL_HOURS = 24`, janitor hourly

## 7. Timeline
- Issued Wed 10 Jun 2026 · Walkthrough call Thu 11 Jun 2026 · original **hard deadline Fri 12 Jun
  2026, 6 PM IST** → email ankit@thedigitalcreators.io. **Deadline shifted after the call** (more
  runway for the expanded scope). A mid-build call where reasoning matters as much as the artifact.

---

## 8. ⚡ REVISED DIRECTION (post-call) — AUTHORITATIVE

After the walkthrough call, the project changed from "stitch clips + guarantee 10–120s" to an
**AI-powered intelligent video editor**: take up to 50 raw videos and produce ONE **coherent,
production-quality** edited video (ad-grade — clean cuts, logical structure, smart time allocation),
for an **AI advertisement generation company**. Output is **genre-agnostic** (likely an ad but could
be other types — we do NOT hard-code ad structure; we infer it).

### What changed
1. **Cloud storage** = **S3 free tier** (confirmed). 2. **Production-quality coherent output** (no
random cuts). 3. **Intelligent content-aware editing** (smart trim, logical order, score-weighted
time). 4. **Genre-agnostic** (infer the video type). 5. **Deadline shifted** (more runway).
6. **Free-tier LLM key provided** (e.g. Google AI Studio / Gemini, or Groq).

### The generation pipeline (content-grounded hybrid, NOT pure top-down)
The LLM always sees the real footage catalog *before* proposing structure, so it never designs beats
the footage can't support. Internal flow:
```
[0] UPLOAD  → S3 (raw inputs)
[1] INGEST  (local) download to temp · ffprobe · validate decodable & dur>0
[2] SEGMENT (local) PySceneDetect → candidate segments on clean shot boundaries
[3] ANALYZE (local, no quota) per segment: quality (blur/exposure/motion/stability) · audio
            (loudness + voice-activity) · CLIP visual tags · face/object detection · Whisper
            transcript w/ WORD timestamps  → builds a text CATALOG
[4] PLAN    (CLOUD AI, ONE text call) catalog + optional brief + target → detected_genre + theme +
            storyboard (ordered beats) + casting (segment→beat in/out) + transitions + music_mood +
            title/CTA + RATIONALE.  Fallback if AI down → heuristic planner from scores+tags
[5] ENFORCE (local, deterministic) validate plan · snap cuts to sentence/silence (Whisper words) ·
            **clamp + water-fill → guarantee final ∈ [10s,120s]** · drop empty beats
[6] RENDER  (local ffmpeg) per segment: trim → stabilize-if-shaky → color/WB match → scale/pad OR
            auto-reframe(vertical) → loudnorm; assemble: transitions(beat-synced) → captions →
            music+ducking → logo/CTA → encode 1080p H.264 +faststart
[7] DELIVER (local) output → S3 · presigned URL · job carries storyboard + RATIONALE + skip report
```
**Division of labor:** heavy *understanding* is local & free; AI does ONE cheap *text* call for the
*thinking*; deterministic code does precise *cutting* and *guarantees constraints*. The original
deterministic clip logic (clamp + water-fill from Section 3/§4) **survives as the Stage-5 duration
enforcer**.

### Adaptive intent (handles unknown/unspecified footage purpose)
Priority: (1) explicit user **brief** (steers) → (2) **inferred intent** (LLM classifies genre+theme
from the catalog; the local CLIP/Whisper/detection layer is the AI's "eyes" — never blind) → (3)
**generic fallback** = clean "best-of highlight reel" when footage is too incoherent for a story.
**Never fabricates a narrative that isn't there.** Production defaults bias to promo/ad-style; the
narrative structure stays adaptive. AI's read is surfaced in the UI for visible intelligence.

### AI architecture + why free-tier survives testing
- **Understanding = fully local & free:** Whisper + CLIP + OpenCV + face/object detection (no quota).
- **Brain = free-tier cloud LLM (Gemini/Groq), TEXT-ONLY, ONE call/job, cached.** We NEVER send raw
  video/frames to the API → token use is tiny → free tier survives heavy testing. (Key misconception
  to correct: free tiers die only if you stream media to them; we don't.)
- **Fallback ladder:** free-tier cloud LLM → optional local **Ollama** (free/unlimited but needs
  hardware, weaker, heavy for a tiny server) → **heuristic engine** (always present). System never
  hard-depends on the cloud. Quota safety: text-only · caching · heuristic/mock dev default.

### Production ladder (all free/local unless noted)
**Core:** content-grounded storyboard · clean shot-boundary cuts · quality + face/object-aware
selection · speech-aware cutting + silence trim · score-weighted allocation · color grade + WB match
· conditional stabilization · loudnorm · pacing/beat-sync · transitions · music bed + ducking ·
captions · logo/CTA. **Stretch (by ROI):** multiple ad variants (A/B) → LLM script → free TTS
voiceover → subject-aware auto-reframe (vertical) → multi-platform export (16:9/9:16/1:1) → brand kit
→ editable storyboard + regenerate. **Format:** 1080p 16:9 default, 9:16 vertical as config option.

### Honest-accuracy framing
Aligned with modern AI-editing tools (Opus Clip/Vizard/Klap, Descript, Adobe Sensei, Pictory/InVideo,
Magisto/CapCut). Their accuracy comes from proprietary models trained on millions of edits +
hand-authored templates + A/B feedback + human-in-the-loop — which we don't have. We build a credible
approximation with free off-the-shelf models, and name the gap rather than hide it.

### Deltas to the original sections
- **§1 architecture:** async/worker/SQLite hold; now a multi-stage pipeline with per-stage progress;
  external LLM dependency needs timeout+retry+fallback; jobs longer; `WORKER_CONCURRENCY` likely 1.
- **§2 stack (expanded):** + PySceneDetect, OpenCV/numpy, open_clip, faster-whisper, librosa,
  mediapipe/YOLO, an LLM SDK (Gemini/Groq), boto3, piper/coqui TTS (stretch). Each behind a toggle.
- **§5 storage (REVERSED → S3):** inputs→S3; download to local temp to process; output→S3 via
  presigned URL; lifecycle via S3 lifecycle rules; storage-interface makes the swap clean; local temp
  still needs disk-pressure guard. (The earlier "why not S3" is reversed by the explicit requirement.)
- **§6 API (augmented):** job status gains storyboard + rationale + detected_genre; optional
  `brief`/prompt + `aspect_ratio` form fields; multi-stage progress; download → presigned S3 redirect.
- **§8 deployment (still pending, now heavier):** compute is the concern (CLIP/Whisper/scene-detect/
  stabilize are CPU-heavy on 50 videos) → likely a stronger free tier or accept long async jobs;
  mitigate via sampling + toggles; S3 region/egress; LLM external. Needs a dedicated decision + host
  choice.
- **§3/§7/§9:** minor (size caps, brief input + rationale display, bigger walkthrough).
