# Video Stitcher — Design Document

> Running design log for the Backend Engineer skill assessment (Digital Creators).
> Each section records the decisions made, the options considered, and the *why*.
> This doubles as the source material for the final **walkthrough document**.

**Assessment:** Upload up to 50 videos → service auto-generates one stitched video (10s–2min) → user downloads it.
**Hard deadline:** Fri 12 June 2026, 6:00 PM IST. **Walkthrough call:** Thu 11 June 2026.

---

## Section agenda & status

| # | Section | Status |
|---|---------|--------|
| 1 | Processing model & architecture | ✅ locked · 🔶 augmented post-call |
| 2 | Tech stack & tools | ✅ locked · 🔶 expanded post-call |
| 3 | Upload & validation | ✅ locked |
| 4 | Generation logic | ✅ **REVISED post-call → intelligent pipeline (see ⚡ block)** |
| 5 | Storage & resource lifecycle | ✅ **RE-LOCKED → R2 + persisted segments (see ⚡ block)** |
| 6 | API design | ✅ locked · 🔶 augmented post-call |
| 7 | Minimal frontend / test harness | ✅ locked |
| 8 | Deployment | ✅ **LOCKED → HF Spaces (Docker, 16 GB)** |
| 9 | Deliverables & docs | ✅ locked |

> **⚡ A post-walkthrough-call scope change significantly expanded this project.** The original
> §1–§7 below remain valid context (and the deterministic §4 survives as the *fallback + duration
> enforcer*), but the **authoritative current direction is the "⚡ POST-CALL SCOPE CHANGE" block at
> the end of this document.** Read that for §4 (intelligent pipeline) and the §1/§2/§5/§6 deltas.

---

<!-- Section decisions get appended below as we lock them. -->

## Section 1 — Processing model & architecture ✅

### Decisions

- **1A — Async, job-based generation.** `POST /generate` validates, creates a job, returns a
  `job_id` immediately; processing happens in the background; client polls for status, then
  downloads. Chosen over blocking because long ffmpeg jobs (30s–minutes for 50 videos) exceed
  free-tier/proxy request timeouts (~30–60s), give no progress visibility, and tie up a server
  worker. Async is also exactly the behaviour the brief probes for.
- **1B — In-process background worker over a durable (SQLite-backed) queue.** A single deployable
  runs both the web API and a background worker loop that pulls the oldest `queued` job and runs
  ffmpeg as a subprocess. No external infra (no Redis). ffmpeg being a subprocess means it does
  not block the app's event loop — the OS schedules it.
- **1C — SQLite for job state + as the queue.** Durable across restarts, zero external service,
  transactional, queryable. Holds job *metadata* only (status, progress, error, result path);
  the actual video bytes live on disk (Section 5).

### Job lifecycle

`queued` → `processing` → `completed` | `failed` (error message captured and surfaced via API).

### Concurrency model

- **API layer: unbounded.** Many users can upload / poll / download concurrently.
- **Generation layer: bounded, default 1.** A single worker drains the queue one job at a time;
  others wait in `queued` (no user is rejected — they're queued). Configurable via
  `WORKER_CONCURRENCY` (default `1`).
- **Why:** ffmpeg is CPU/RAM-heavy; a free-tier box is ~1 vCPU / 256–512 MB RAM. Running many
  ffmpeg jobs at once would thrash/OOM and fail *everyone's* job. A queue with bounded concurrency
  is the correct backpressure: accept all work, drain at a sustainable rate. ("Resource awareness".)

### Architecture sketch

```
Single deployed app:
  Web API (upload, /generate, /jobs/:id) ──writes/reads──▶ SQLite (jobs + queue)
                                                                 ▲
  Worker loop ──polls oldest queued──────────────────────────────┘
       └── spawns ffmpeg subprocess ──▶ disk: /uploads, /outputs
```

### When to upgrade to Redis + Celery (and the scaling story)

Triggers: need >1 machine, throughput exceeds one box, independent scaling of web vs workers,
automatic retries/timeouts, priorities/scheduling/rate-limits, or queue observability.
- **Redis** = shared broker/state so any worker on any machine grabs the next job (SQLite is a
  local file, can't be shared across machines).
- **Celery** = worker framework providing retries, timeouts, acks, concurrency, result tracking.

**Scaling ladder (answer to "how would you scale this?"):**
0. As built — one box, web+worker together, SQLite queue, concurrency 1.
1. **Vertical first (free):** bigger box, raise `WORKER_CONCURRENCY`.
2. **Decouple storage:** videos → object storage (S3/GCS). The real unlock — files no longer tied
   to one machine's disk, so any worker can process any job.
3. **Externalize queue & split workers:** SQLite-queue → Redis + Celery; web and workers become
   separate deployables; add workers horizontally; autoscale on queue depth. *Video logic
   unchanged — only the queue and runner change.*
4. **Remaining bottlenecks:** upload bandwidth → presigned direct-to-S3 uploads; ffmpeg CPU → GPU
   transcoding or split-and-merge a job across workers; job metadata → Postgres; global users →
   CDN + regional workers.

Principle: find the real bottleneck, fix that one, re-measure — don't build the distributed
version up front. Adding Redis/Celery before you're bottlenecked on one machine adds operational
surface area for zero throughput gain (one box has one CPU ceiling either way).

---

## Section 2 — Tech stack & tools ✅

### Final stack

| Capability | Tool | Why |
|-----------|------|-----|
| Language + web framework | **Python + FastAPI** | Async-native, first-class `UploadFile` streaming, trivial subprocess control, `sqlite3` in stdlib. Optimized for dev speed + readability under deadline. |
| ASGI server | **uvicorn** | Standard FastAPI server; handles concurrent requests. |
| Video processing | **ffmpeg** (CLI via `subprocess`) | Industry-standard media engine; called directly (not via wrapper) for transparency, lowest overhead, debuggable commands. |
| Video inspection | **ffprobe** | Reads duration/codec before cutting; powers duration math + zero-length/corrupt-file rejection. |
| Job store + queue | **SQLite** (stdlib `sqlite3`) | Durable, zero external infra; thin data-access module, no ORM. |
| Background worker | **Daemon thread** at startup | Isolates blocking poll/subprocess loop from the async event loop. |
| Upload parsing | FastAPI **`UploadFile`** | Streams uploads to disk in chunks; avoids loading 50 large files into RAM. |
| Config | **pydantic-settings** / env vars | All limits/knobs from env; nothing hard-coded. |
| Packaging | **Docker** | Guarantees ffmpeg present in deploy; satisfies a stretch goal. |
| Tests (stretch) | **pytest** | Unit-test pure clip/duration math + API smoke test if time allows. |

**Deliberately omitted (restraint = judgment):** ORM, Celery/Redis, frontend framework, ffmpeg
wrapper lib. Each omission traces to "matched the tool to the scale."

### Why Python over other languages — the trade-off

The performance-critical path is **ffmpeg**, a native subprocess: every language calls the same
binary and waits the same time, so host-language raw speed is **off the critical path**. So we
optimize for dev velocity, subprocess/upload ergonomics, async, and readability under a 2-day clock.
- **Node/TS** — viable (matches React background) but ffmpeg tooling (`fluent-ffmpeg`) is
  semi-abandoned, `child_process` clunkier than Python `subprocess`, multipart needs `multer`.
  Would pick it if team standard were TS; architecture is identical.
- **Go** — single binary + great concurrency, but speed edge is moot (ffmpeg-bound), more
  boilerplate slows the build, and its headline strength (cheap massive concurrency) is the one
  thing we deliberately *bound* (§1). Wins when you need thousands of connections / tiny memory.
- **Rust** — fastest/safest but slow to iterate; over-engineering for a 2-day task.
- **Java/Spring, Rails, PHP** — capable but heavyweight / weaker fit for subprocess-driven media
  + async background work.

### Why ffmpeg (vs alternatives)

Nearly every alternative wraps ffmpeg anyway. Chosen directly because: right primitive
(trim+concat+normalize native), zero cost / no vendor / no keys, lowest resource overhead (matters
on a 256–512 MB box with 50 inputs), transparent & debuggable, universal format support, and it
keeps the core engineering in our codebase.
- **MoviePy / PyAV / ffmpeg-python** — wrap ffmpeg, add overhead or obscure errors.
- **OpenCV** — frame-analysis tool, not efficient concat; wrong shape.
- **GStreamer** — powerful but complex pipeline model; overkill for batch stitching.
- **Shotstack / Cloudinary / Mux / AWS MediaConvert** — would work but **outsource the exact part
  the assessment asks us to build**; plus cost/keys/lock-in. Answering a different question.
- **HandBrake / MEncoder / avconv** — wrong shape or stale.

---

## Section 3 — Upload & validation ✅

### Bounds
- **Max 50 files** (brief); >50 → `400`. **Min 1** (single source still works; lenient over rejecting valid edge).

### Format verification — two layers (never trust extension/MIME)
1. **Cheap gate at upload:** extension in allowlist `mp4 / mov / webm / mkv`. Rejects obvious
   non-videos before wasting disk.
2. **Authoritative at generation:** **ffprobe** must report a decodable **video stream** with
   **duration > 0**. Catches corrupt/fake/zero-length/truncated files. Also yields per-file
   duration for the §4 clip math (validation + generation-prep are the same step). No
   `python-magic` dependency — ffprobe is the better authority.

### Size limits (resource awareness)
- **Per-file: 100 MB** (`MAX_FILE_SIZE`). **Total batch: 1 GB** (`MAX_TOTAL_SIZE`). Total is the
  real guard (50× per-file would exceed free-tier disk). Numbers chosen to fit small ephemeral
  disk; both env-configurable.
- **Enforced during streaming**, not from `Content-Length` (header can lie): count bytes per chunk,
  abort + clean up the instant per-file or running-total cap is exceeded.

### Streaming & safety
- **Chunked streaming to disk** (~1 MB chunks) via `UploadFile` — never load 50 files into RAM.
- **UUID filenames** on disk; original name kept only as metadata (no path-traversal via filenames).

### Two-tier failure policy
- **Structural errors → fail-fast synchronous `400` at upload** (too many files, bad extension,
  oversize, empty). User-fixable, all-or-nothing, error names which files failed.
- **Content errors → lenient skip at generation.** ffprobe-invalid files are skipped; the rest
  proceed. **Fail whole job only if 0 usable videos remain.**

### Skip reporting (transparency)
Job status surfaces `total_uploaded`, `used`, `skipped`, and `skipped_files[]` with a
human-readable `reason` per file — visible during *and* after processing. Hard failure only on
zero usable inputs: `{"status":"failed","error":"No usable videos: ..."}`.

### Data model — two tables (batch = job)
One submission (≤50 videos) = one **job** producing one output. One job has many files.

```
jobs(id PK, status, target_duration, total_uploaded, used_count, skipped_count,
     output_path NULL, error NULL, created_at, updated_at)
files(id PK, job_id FK→jobs.id, original_name, stored_path, size_bytes,
      duration REAL NULL, status[pending|used|skipped], skip_reason NULL)
```
- `files.job_id` records which batch a video came in; `files.status`/`skip_reason` records its
  outcome. Skip report = `SELECT original_name, skip_reason FROM files WHERE job_id=? AND
  status='skipped'`.
- Chosen over a single-table JSON manifest for clean normalization, queryability, and separation
  of concerns. Bytes live on disk; DB holds metadata only.
- **Not stored** (no purpose in scope): users/auth, cross-batch history, analytics.

### Disk layout (detail + cleanup TTL → §5)
```
storage/uploads/{job_id}/{file_uuid}.ext   ← inputs
storage/outputs/{job_id}.mp4                ← result
```

---

## Section 4 — Generation logic (clips + duration) ✅

### 4A — Target duration: computed by default, user-overridable
- **Default:** `target = clamp(N × CLIP_SECONDS, 10, 120)`; `N` = usable videos, `CLIP_SECONDS`
  default 3 (config). Auto-scales with upload size. `clamp` is where the brief's 10–120s hard
  constraint mechanically lives (below 10 → bump to 10; above 120 → cap to 120).
- **Override:** user-supplied `target_duration` validated to `[10,120]` else `400`.
- Satisfies the "configurable duration" stretch goal via the override.

### 4B — Clip length: even share + water-filling redistribution
`base = T / M`; each clip capped at its source duration (`min(base, d_i)`). Short videos cap out and
release their leftover budget, which is **redistributed** to videos with headroom:
```
open = all M videos; budget = T
loop:
    share = budget / len(open)
    constrained = {v in open : d_v < share}
    if constrained: for v in constrained: L_v = d_v; budget -= d_v; remove v from open   # repeat
    else:           for v in open: L_v = share; done
```
Water-filling: terminates in ≤M passes; hits T exactly when `Σd_i ≥ T`, else everyone caps at d_i
and `ΣL_i = Σd_i`. Round to 0.1s, last clip absorbs rounding so the sum is exact.
*Example:* T=15, d=[1,2,3,50,50] → [1,2,3,4.5,4.5] = 15.

### 4C — Subset selection: **Option A (watchable, even-sample)** ✅
`MIN_CLIP = 1s`. `max_clips = floor(T / MIN_CLIP)`. If `N > max_clips`, **evenly sample**
`max_clips` videos spread across upload order (represents the whole batch, not just the front);
featured/dropped videos reported via the usage report. Chosen over "include everything" because
sub-second clips flicker and look broken; we already have reporting to surface which were featured.

### 4D — Clip position: from the start (offset 0)
Deterministic, simple, explainable. Possible enhancement (documented, not default): sample from a
small offset / middle to skip intros & black frames.

### 4E — Ordering: upload order
Stable, predictable, user-controllable by upload sequence. Random shuffle / transitions = stretch.

### 4F — Duration guarantee ∈ [10,120]
- Upper: `T ≤ 120` (clamped) and `ΣL_i ≤ T` → output ≤ 120. ✓
- Lower: `effective = min(T, total_usable_footage)`. ≥T footage → ~T; 10–T footage → all footage;
  **<10s footage → fail** ("Insufficient footage: Xs total, minimum output is 10s"). No
  loop/freeze-frame padding — honest failure over deceptive output.

### 4G — Technical stitching: normalize-then-concat (robust)
Arbitrary uploads differ in codec/resolution/fps/pixfmt, so fast `concat -c copy` of raw inputs
breaks. Two-pass:
1. **Normalize each clip** (one ffmpeg pass): trim `-ss 0 -t L_i` + re-encode to common profile —
   scale+pad **1280×720**, **30fps**, `yuv420p`, **H.264**, **AAC 44.1k stereo**. **Synthesize
   silent audio if source has none** (uniform streams for concat).
2. **Concat demuxer `-c copy`** the normalized clips → final **MP4 `+faststart`** (web-streamable).

Two-pass chosen over a single 50-input `filter_complex`: bounded memory, per-clip **progress
reporting** (clip 12/47), and failure isolated to one clip instead of the whole graph. Resource-aware.

### Worked example
5 videos `[2,1,30,8,4]`, no target → N=5, target=clamp(15,10,120)=15s; all 5 fit (≤15 clips);
base=3, cap+redistribute → ~15s; clips from start, upload order; normalize→concat → 15s MP4.

---

## Section 5 — Storage & resource lifecycle ✅

### 5A — Disk layout (job-scoped)
```
storage/uploads/{job_id}/{file_uuid}.ext   ← raw uploads (≤50)
storage/work/{job_id}/clip_000.mp4 …       ← normalized intermediates (transient)
storage/outputs/{job_id}.mp4                ← final deliverable
```
Grouped by `job_id` so cleanup = one recursive delete of a folder.

### Bytes vs metadata
**Videos = real files on local disk. SQLite stores metadata + a `stored_path` pointer only** (no
BLOBs — DBs are bad at large binaries; filesystem is purpose-built). Upload journey: `UploadFile`
stream → chunked write to `uploads/{job}/{uuid}.ext` (size enforced mid-stream) → insert `files` row
with `stored_path`.

### 5B — Intermediate files
Normalization creates a second copy of video data on disk. Mitigation: **delete `work/{job_id}/` the
instant concat consumes it** (don't wait for job-level cleanup). Considered stream/pipe to lower peak
disk but rejected: concat demuxer needs seekable files; real intermediates give failure isolation,
progress, debuggability. Disk cost already bounded by immediate deletion → robustness without the
disk penalty piping was meant to avoid.

### 5C — Upload cleanup
On terminal state (`completed`/`failed`), **delete `uploads/{job_id}/` immediately** — only the
output matters. Steady state stores outputs only, not 50 raw inputs per job.

### 5D — Output retention (TTL + janitor)
Outputs expire **24h** after creation (`OUTPUT_TTL_HOURS`). A periodic **janitor** (hourly, in the
worker process) deletes expired outputs + orphaned `uploads/`/`work/` dirs. Download after expiry →
**`410 Gone`** ("output expired"), not a confusing 404.

### 5E — Disk-pressure guard
If free disk < safety threshold, **reject new uploads with `503`** ("server busy") rather than
accept work we can't store. Disk-level backpressure mirroring the queue's CPU-level backpressure.

### 5F — Crash recovery
On startup, jobs stuck in `processing` are **re-queued** (inputs still on disk; regeneration is
idempotent into the same output path); startup janitor sweeps orphaned `work/` dirs.

### Storage interface (scaling hook)
File ops go behind a thin module (`save`/`open`/`delete`/`url_for`) so swapping **local disk → S3/GCS**
(scaling Stage 2) is a one-file change.

### Why not S3 now
Single box → no shared-storage problem to solve. S3 adds account/IAM/keys/SDK/network failure mode
for zero benefit at this scale; ffmpeg needs local bytes anyway; cost is a footnote (free tier exists).
Same "match tool to scale" discipline as deferring Redis/Celery. First thing introduced when going
multi-machine. **Theme: every byte has an owner and an expiry** — uploads die when the job ends,
intermediates when consumed, outputs on TTL, orphans get swept; nothing accumulates unbounded.

---

## Section 6 — API design ✅

Principles: predictable shapes, correct status codes, async-first (never blocks).

### 6A — One-step flow ✅
`POST /api/jobs` uploads videos **and** triggers generation in one call. (Two-step
upload→generate noted as alternative; buys re-generate-without-reupload, not needed here.)

### 6B — Endpoints
| Method & path | Purpose | Success |
|---------------|---------|---------|
| `POST /api/jobs` | Multipart upload + optional `target_duration`; validate, store, enqueue | **202 Accepted** |
| `GET /api/jobs/{id}` | Poll job status | 200 |
| `GET /api/jobs/{id}/download` | Stream finished video | 200 |
| `GET /api/jobs` | List recent jobs (newest first, cap 50, no pagination) — for demo page | 200 |
| `GET /health` | Liveness check (`{"status":"ok"}`) | 200 |

`DELETE /api/jobs/{id}` skipped unless time permits. `202` on create = semantically correct
"accepted for async processing".

### 6C — Job object (same shape from POST and GET)
```json
{ "job_id","status":"queued|processing|completed|failed","target_duration",
  "progress":{"stage","current","total"},"total_uploaded","used","skipped",
  "skipped_files":[{"filename","reason"}],"output_duration","download_url","error",
  "created_at","updated_at" }
```
Client polls `GET /jobs/{id}`, watches `status`; `download_url` fills on `completed`, `error` on
`failed`. `progress` (clip X/Y from per-clip normalization) covers the progress-reporting stretch goal.

### 6D — Status codes + consistent error envelope
Envelope: `{"error":{"code","message"}}` everywhere.
| Situation | Code | code |
|-----------|------|------|
| >50 files | 400 | TOO_MANY_FILES |
| no/empty files | 400 | NO_FILES |
| bad extension | 415 | UNSUPPORTED_MEDIA_TYPE |
| over size cap | 413 | PAYLOAD_TOO_LARGE |
| target_duration outside 10–120 | 400 | INVALID_DURATION |
| disk pressure (§5E) | 503 | STORAGE_UNAVAILABLE |
| unknown job | 404 | JOB_NOT_FOUND |
| download while processing | 409 | NOT_READY |
| download but job failed | 409 | JOB_FAILED |
| download after TTL | 410 | OUTPUT_EXPIRED |
(`409` for not-ready chosen over niche `425 Too Early`.)

### 6E — Download
`FileResponse`: `Content-Type: video/mp4`, `Content-Disposition: attachment; filename="stitched-{id}.mp4"`,
`Accept-Ranges: bytes` + `Content-Length` (seekable/resumable). Streams from disk, not into memory.

### 6F — Polling over websockets
Client polls `GET /jobs/{id}` (~2s, documented not enforced). Stateless, curl-friendly, right scale.
SSE/websockets noted as upgrade for live progress.

### Micro-defaults
`target_duration` = optional multipart form field (absent → computed default). Same-origin HTML page
→ no CORS (one-line add if split out). No auth/rate-limiting (per brief; `503` disk guard is the only
backpressure) — stated as a decision, not omission.

---

## Section 7 — Minimal frontend / test harness ✅

Out of scope for grading (brief: bare HTML/curl is fine). Goal: least effort that fully exercises
the feature + makes the live demo effortless. No framework, no build step (that would be the exact
over-engineering the brief warns against).

### Deliverables
- **Single static HTML page** (vanilla JS) served by FastAPI at `GET /` — **same-origin** (no CORS),
  **one deployable** (live URL serves API + demo). Flow: `multiple` file input + optional target
  field → `POST /api/jobs` → poll `GET /api/jobs/{id}` every ~2s showing `status` + `progress`
  (normalizing 12/47) + skip report → on `completed` show download link + inline `<video>` preview;
  small recent-jobs list via `GET /api/jobs`. ~60–80 lines.
- **curl examples in README** (terminal reviewers + living API docs):
  ```bash
  curl -F "files=@a.mp4" -F "files=@b.mp4" -F "target_duration=20" <url>/api/jobs
  curl <url>/api/jobs/<job_id>          # poll until completed
  curl -OJ <url>/api/jobs/<job_id>/download
  ```
- **Skip** Postman (curl covers it) and any frontend framework.

---
---

# ⚡ POST-CALL SCOPE CHANGE (revised direction) — AUTHORITATIVE

**After the walkthrough call the scope expanded significantly. This block supersedes parts of
§1–§6 above.** The company is an **AI advertisement generation** company.

### What changed
1. **Cloud storage required** — use **S3 free tier** (confirmed; free-tier key available).
2. **Production-quality, coherent output** — the result must be a properly edited video with
   **clean cuts and logical structure**, NOT random cuts.
3. **Intelligent, content-aware editing** — smart trimming, logical ordering, and **score-weighted
   time allocation** (footage with more useful content gets more screen time).
4. **Genre-agnostic** — likely an ad but "could be something else"; we do **NOT hard-code ad
   structure** — the system *infers* the most fitting video type from the footage.
5. **Deadline shifted** → runway for the intelligent pipeline + production polish.
6. A **free-tier LLM key will be provided** (e.g. Google AI Studio / Gemini, or Groq).

---

## Revised Section 4 — Adaptive, content-grounded intelligent pipeline ✅

**Approach = content-grounded hybrid** (NOT pure top-down): the LLM always sees the real footage
catalog *before* proposing structure, so it never designs beats the footage can't support. Leans
top-down for narrative logic, grounded in bottom-up content reality.

### Internal processing flow
```
[0] UPLOAD    client → S3 (raw inputs)
[1] INGEST    (local) download to temp · ffprobe (dur/res/fps/audio/codec) · validate decodable & dur>0
[2] SEGMENT   (local) PySceneDetect per video → candidate segments [start,end] on clean shot boundaries
[3] ANALYZE   (local, no quota) per segment build CATALOG entry:
                quality (blur/exposure/motion/stability) · audio (loudness + voice-activity) ·
                CLIP visual tags · face/object detection · Whisper transcript w/ WORD timestamps
        CATALOG = [{video,start,end,dur,score,tags,transcript,faces,objects}, ...]
[4] PLAN      (CLOUD AI — ONE text call) input = catalog + optional brief + target_duration →
                detected_genre + theme (handles "is it an ad?" automatically) ·
                storyboard (ordered beats w/ intent + target durations) ·
                casting (segment → beat, in/out) · transitions · music_mood · title/CTA · RATIONALE
              FALLBACK if AI unavailable/rate-limited → HEURISTIC planner from scores+tags
[5] ENFORCE   (local, deterministic — AI never trusted blindly)
                segments exist? in/out valid? no overlaps? · snap cuts to sentence/silence
                boundaries (Whisper words) · **clamp + water-fill → guarantee final ∈ [10s,120s]**
                · drop beats with no good footage
[6] RENDER    (local ffmpeg, multi-pass) per segment: trim(snapped) → stabilize-if-shaky →
                color/WB match → scale/pad OR auto-reframe(vertical) → loudnorm;
                assemble: concat w/ transitions (beat-synced) → burn captions → music bed + ducking
                → logo/CTA/title cards → encode 1080p H.264 +faststart
[7] DELIVER   (local) upload output → S3 · presigned URL · job carries storyboard + RATIONALE + skip report
```
**Division of labor:** heavy *understanding* is local & free; AI does ONE cheap *text* call for the
*thinking*; deterministic code does precise *cutting* and *guarantees constraints*. The original
deterministic §4 (clamp + water-fill) **survives verbatim as the Stage-5 duration enforcer**.

### Adaptive intent model (handles "we don't know what it's about")
Priority: **(1) explicit brief** (user prompt, steers) → **(2) inferred intent** (LLM classifies
genre+theme from the catalog — the local CLIP/Whisper/detection layer is the AI's "eyes," so it's
never blind) → **(3) generic fallback** = clean "best-of highlight reel" when footage is too
incoherent to infer a story. **Never fabricates a narrative that isn't there** (honest-over-deceptive,
same principle as the duration floor). Default *production* bias = promo/ad-style; *narrative
structure* stays adaptive. AI's read is surfaced in output/UI for visible intelligence.

### AI architecture + fallback ladder
- **Understanding = fully local & free:** Whisper (speech) + CLIP (visual tags) + OpenCV (quality) +
  face/object detection. No quota ever.
- **Brain = free-tier cloud LLM, TEXT-ONLY, ONE call/job, cached**, behind a strategy interface.
  We NEVER send raw video/frames → token use is tiny → free tier survives heavy testing.
- **Quota safety:** text-only input · result caching keyed on input set · heuristic/mock mode is the
  dev default (only hit real LLM for demo) · Whisper/CLIP local so testable freely.

### Multi-provider failover chain (Stage-4 PLAN) ✅
The PLAN call is **atomic**: a free-tier limit fails the *whole* call up front (`429`/quota) — never a
half-built plan — and we don't act on the plan until it's validated (Stage 5). So failover is clean
and runs **synchronously WITHIN the same job** (user still gets their video in one go; never "error
now, retry later").

**Ordered chain (config-driven, generalizes to N keys/providers):**
```
GEMINI (primary) ──fail──▶ GROQ (secondary) ──fail──▶ HEURISTIC engine (never fails)
```
- Each backend is a `Planner` with the same signature `plan(catalog, brief, target) → validated Plan`
  (Gemini / Groq / Heuristic are interchangeable adapters; downstream code is provider-agnostic).
- **Heuristic floor never fails** (pure local: energy-arc order + score-ranked + group-by-tag) →
  job always completes. Two free tiers + deterministic floor = effectively never blocked.
- **Failure triggers:** `429` (quota/rate), `5xx`, timeout, **and invalid/unparseable output**
  (validate schema before trusting; garbage JSON = failure → next provider).
- **1 short backoff retry** on the same provider (transient per-minute blip), then switch.
- **Optional local Ollama** can sit in the chain for fully-offline self-host (heavy on a tiny box).
- Record **`planner_used`** (`gemini|groq|heuristic`) on the job (transparency + shows resilience).
- Keys in **env/secrets**, never in code. Only this one call uses cloud (local understanding has no
  quota), so the failover surface is tiny.
- Walkthrough value: resilient LLM layer, **no single point of failure**, graceful degradation.

### Production ladder (all free/local unless noted)
**Core (baked in):** content-grounded storyboard · clean shot-boundary cuts · quality + face/object-
aware selection · speech-aware cutting + silence trim (Whisper word timestamps) · score-weighted
allocation · color grade + WB match · conditional stabilization (vidstab) · loudness norm (loudnorm)
· pacing/beat-sync (librosa) · transitions (xfade) · music bed + ducking · captions · logo/CTA.
**Stretch ladder (by ROI):** multiple ad variants (A/B — on-brand for ad co) → LLM script → free TTS
voiceover (piper/coqui) → subject-aware auto-reframe for vertical → multi-platform export (16:9/9:16/
1:1) → brand kit (logo/colors/font) → editable storyboard + regenerate-with-feedback.

### Format
1080p **16:9 default**, **9:16 vertical** as config option (naive center-crop is bad → vertical uses
subject-aware auto-reframe as a stretch).

### Honest-accuracy framing (walkthrough material)
Architecture is aligned with modern AI-editing tools (Opus Clip/Vizard/Klap = Whisper→LLM-select→
reframe+caption; Descript = transcript editing + silence removal; Adobe Sensei = scene detect +
auto-reframe + ducking; Pictory/InVideo = script→footage+templates; Magisto/CapCut = ML highlight +
templates + beat-sync). Their **accuracy** comes from proprietary models trained on millions of
professional edits + hand-authored templates + A/B feedback loops + human-in-the-loop — which we
**don't** have. We build a **credible approximation** with free off-the-shelf models, naming the gap
rather than hiding it (stronger signal than overclaiming).

---

## Cascade deltas to earlier sections (fold formally as we revisit each)

**§1 (augment):** async/worker/SQLite all hold. Processing becomes a **multi-stage pipeline**
(ingest→segment→analyze→plan→enforce→render→deliver) with **per-stage progress**. New **external LLM
dependency** → needs **timeout + retry + heuristic fallback**. Jobs now much longer → reinforces async.
`WORKER_CONCURRENCY` likely stays 1 (jobs are heavier).

**§2 (expand stack):** add **PySceneDetect**, **OpenCV/numpy** (segmentation + quality scoring),
**open_clip/CLIP** (visual tags), **faster-whisper** (transcription + word timestamps), **librosa**
(beat/audio), **mediapipe or ultralytics/YOLO** (face/object), **LLM SDK** for the free-tier provider
(Gemini/Groq), **boto3** (S3), **piper/coqui TTS** (stretch). Re-justify: we now intentionally add ML
deps because the *product is intelligence* — but keep each behind a toggle for compute.

**§5 (REVISED → S3/R2 — LOCKED):**
- **Provider: Cloudflare R2** (S3-compatible via `boto3`; only endpoint+creds differ). Chosen over
  AWS S3 because **R2 has zero egress fees** — our off-AWS box must download every input/segment from
  cloud to process locally, which would be billed egress on S3. R2 also gives more free storage
  (~10 GB). Code stays S3-compatible → not locked in; literal S3 fine if we ever deploy on AWS
  (same-region free egress). Coupling to §8 host noted.
- **Upload path: server-proxied** (client → API → R2, stream + validate on the way). Keeps one-step
  `POST /api/jobs` + stream-validation. Presigned-direct-PUT noted as the resource-scaling upgrade.
- **Inputs + outputs both in R2** (source of truth); **local temp = ephemeral scratch**. Inputs in
  R2 enable real **crash recovery** (re-queued job re-downloads inputs after a box restart); zero
  egress makes the re-fetch free.
- **Download: presigned GET URL** (short-lived, ~1h) → `GET /download` returns a **302 redirect**
  (or the URL in JSON). Offloads the box; native range/resume. (Supersedes old FileResponse-from-disk.)
- **Lifecycle/TTL: R2 lifecycle rules** (uploads ~1d, segments + outputs ~24h–7d) **+ proactive
  delete** of a job's `uploads/` on completion. Download after expiry → `410 Gone`.
- **Local temp retained** + **disk-pressure `503` guard** still apply (we download inputs + write
  intermediates + output locally during a render). Temp cleared after each render.
- **Security:** private bucket · least-privilege scoped token · creds in env · presigned URLs
  short-lived. SQLite stores **object keys** (`stored_key`, `output_key`), not local paths.
- Bucket layout:
  ```
  uploads/{job_id}/{file_uuid}.ext     ← inputs (source of truth)
  segments/{job_id}/seg_000.mp4 …      ← persisted normalized building blocks (see below)
  outputs/{job_id}.mp4                   ← deliverable(s) / variants
  ```

**Persisted segments (interviewer suggestion — ADOPTED, revises old §5B "delete intermediates"):**
Natural extension of §4G (we already normalize each segment to a real file). Instead of deleting
them, **persist the normalized segments to R2 (TTL'd) as reusable building blocks** →
*expensive work (analyze+cut+normalize) once → cheap work (concat in some order) many times.*
- **Payoff:** multiple variants (A/B) = re-concat cached segments in new orders (**near-free**);
  reorder / regenerate / "make it shorter" = new EDL over same segments → re-concat; re-planning =
  one LLM call over the cached catalog, **no re-analysis**; resumable renders; decoupled assembly.
- **New `segments` table:**
  ```
  segments(id PK, job_id FK, source_file_id FK→files.id, in_point, out_point, duration,
           normalized_key, quality_score, tags, transcript_snippet)
  ```
  EDL references `segment.id`s; reorder = new ordering over rows → re-concat their `normalized_key`s.
- **Nuances:** persisted segments are **aspect-profile-specific** (a 16:9 segment can't be reused in
  a 9:16 render → re-derive for different aspect). Storage cost mitigated by TTL + keeping local temp
  ephemeral (push segments to R2, clear local). Only truly transient scratch (analysis frames) is
  deleted immediately.
- **Clarification:** within one output the order is fixed by the EDL up front; persistence pays off
  **across** renders (variants/regenerate/tweaks), not inside a single render.

**§6 (augment):** job status gains **storyboard + rationale + detected_genre** fields (show the AI's
thinking); **optional `brief`/prompt** form field on `POST /api/jobs`; **multi-stage progress**
(stage = ingest/segment/analyze/plan/render); download → **redirect to presigned S3 URL** (or proxy).
`target_duration` still optional; add optional `aspect_ratio` (16:9 / 9:16 / 1:1).

**§3 / §7 / §9:** minor. §3 may raise size caps for real footage. §7 may show the brief input +
rationale. §9 walkthrough expands.

---

## Section 8 — Deployment ✅ (LOCKED)

**Driver:** scope change made this an **ML workload**. Whisper + CLIP want ~1.5–2 GB+ RAM; CPU-heavy
→ jobs take minutes; needs a **long-lived worker** (not serverless freeze) + ffmpeg & model weights
**baked into the image**. This **rules out the smallest free tiers** (Render 512 MB, Fly 256 MB) — the
models won't even load. Honest call framing: *"the ML models set a RAM floor; I picked a free tier
that actually has enough memory."*

- **8A Host: Hugging Face Spaces (Docker SDK)** — **2 vCPU / 16 GB RAM free**, ML-native, git-push
  deploy, public URL. Rare free tier with enough RAM for the models. Defensible/clever for an AI
  workload. Wart: **sleeps on inactivity → cold start** (~30s warm-up, noted in README). Alternative
  considered: **Oracle Cloud Always Free Ampere VM** (4 OCPU/24 GB, no sleep, Docker Compose, but more
  setup + card required). Also considered: Cloud Run (scale-to-zero freezes worker → needs job
  pattern), Railway/Fly (not truly free at this RAM).
- **8B Containerization:** Docker single image, multi-stage, **ffmpeg + Python deps + model weights
  baked at build time** (no runtime download). **`docker-compose up`** = one-command local run /
  fallback. One always-on container runs **API + background worker** (§1).
- **8C Resource bounding (so the free box copes):** smaller default models (Whisper `base`, light
  CLIP, YOLO-nano) · frame sampling (~1 fps, low-res) · `WORKER_CONCURRENCY=1` · heavy passes
  (stabilization/CLIP) are **toggles** · honest timing (50 videos = several min; async + progress);
  may demo with fewer/shorter clips while supporting up to 50.
- **8D Config & secrets:** env/HF secrets — R2 creds, Gemini + Groq keys, size/duration limits, model
  choices, TTLs, `WORKER_CONCURRENCY`. Nothing hard-coded. `GET /health` for liveness.
- **8E Cold start:** HF sleep → first request slow (model load); noted in README. (Oracle = no sleep
  if preferred later.)

---

## Section 9 — Deliverables & docs ✅ (LOCKED)

Graded under "Documentation & communication"; 3 of 4 deliverables are docs. `DESIGN.md` is the
walkthrough's source material.

- **9A Repo (public GitHub), clean layout:** `app/` (API) · `pipeline/` (stages) · `storage/` (R2
  interface) · `planners/` (LLM + heuristic failover chain) · `web/` (static page) · `tests/` ·
  `Dockerfile` · `docker-compose.yml` · `.env.example` · `README.md` · `WALKTHROUGH.md`. Structure
  itself signals separation of concerns.
- **9B README:** overview · architecture diagram · **one-command run** (`docker-compose up`) ·
  `.env.example` (R2 + Gemini/Groq keys + limits) · API reference + curl · live URL · config knobs ·
  cold-start note · troubleshooting.
- **9C WALKTHROUGH.md** (narrative from DESIGN.md): problem/approach · architecture + pipeline diagram
  · **the generation pipeline (the star)** · clip-selection & duration logic + **10–120s guarantee** ·
  AI architecture (local understanding, text-only LLM, **multi-provider failover**, heuristic
  fallback) · validation/errors/edge cases · resource awareness (temp lifecycle, persisted segments,
  R2, cleanup) · deployment (HF Spaces + why) · **key decisions & trade-offs** ("match tool to scale"
  + "honest-accuracy") · **what I'd improve with more time** (stretch ladder + AI roadmap).
- **9D Demo assets:** small sample input clips + a generated sample output (in-repo/linked) + a
  demo-page GIF in README → reviewer sees it works without sourcing footage.
- **9E Tests (stretch, high-ROI):** pytest on pure logic — clamp + water-fill, EDL
  validation/enforcement, intent-fallback — plus one API smoke test.
- **9F Submission:** email `ankit@thedigitalcreators.io` — live URL + test steps · GitHub link ·
  WALKTHROUGH.md · tools list. One email hitting all four deliverables.

---

# ✅ DESIGN COMPLETE — all 9 sections locked. Next: implementation (build in one pass).
