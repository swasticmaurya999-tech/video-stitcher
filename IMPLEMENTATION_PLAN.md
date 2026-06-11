# Video Stitcher — Implementation Plan

> Deep implementation plan derived from `DESIGN.md` (all 9 sections locked). Optimized, edge-case-
> driven, build-in-verifiable-phases. **No code is written until this is approved.**

Principles throughout: **(1)** working end-to-end first, intelligence + polish layered on top;
**(2)** every stage has a clear input→output contract; **(3)** every external/heavy op is bounded,
cached, and degrades gracefully; **(4)** the duration guarantee and failover are enforced by
deterministic code, never trusted to the LLM.

---

## 1. Repository / module layout

```
video-stitcher/
├── app/
│   ├── main.py                # FastAPI app, lifespan(start worker+janitor), mount routes + static
│   ├── config.py              # pydantic-settings — ALL env knobs (limits, models, keys, TTLs)
│   ├── errors.py              # AppError(code,message,http_status) + exception handlers → envelope
│   ├── models.py              # enums + dataclasses: JobStatus, Stage, Job, FileRec, Segment, Plan, Beat, EDLItem
│   ├── api/
│   │   ├── routes.py          # POST /api/jobs · GET /api/jobs/{id} · GET .../download · GET /api/jobs · GET /health
│   │   └── schemas.py         # pydantic request/response (JobOut, ErrorEnvelope, ...)
│   ├── db/
│   │   ├── database.py        # sqlite conn (WAL), schema init, connection-per-thread
│   │   └── repo.py            # data access: jobs/files/segments CRUD + queue claim (atomic)
│   ├── storage/
│   │   ├── base.py            # StorageBackend protocol: save/download/presign_get/delete/exists
│   │   ├── r2.py              # Cloudflare R2 via boto3 (S3-compatible)
│   │   └── local.py           # local-disk backend (dev + tests + fallback)
│   ├── worker.py              # worker loop (claim→run→update) + janitor + crash-recovery on boot
│   ├── pipeline/
│   │   ├── orchestrator.py    # runs stages in order, per-stage progress + error handling
│   │   ├── ingest.py          # download inputs → temp, ffprobe, validate (content layer)
│   │   ├── segment.py         # PySceneDetect → candidate segments
│   │   ├── analyze.py         # per-segment scoring + CLIP tags + faces/objects + Whisper
│   │   ├── catalog.py         # assemble the text CATALOG for the planner
│   │   ├── enforce.py         # validate EDL · snap cuts · clamp+water-fill · drop empties
│   │   ├── render.py          # normalize segments → concat → effects pass → encode
│   │   ├── ffmpeg.py          # command builders + safe subprocess runner (timeout, stderr capture)
│   │   └── plan/
│   │       ├── base.py        # Planner protocol: plan(catalog,brief,target)→Plan
│   │       ├── chain.py       # failover: gemini→groq→heuristic (config-ordered)
│   │       ├── prompt.py      # prompt builder + JSON schema + parse/validate
│   │       ├── gemini.py      # Gemini adapter (JSON mode)
│   │       ├── groq.py        # Groq adapter (JSON mode)
│   │       └── heuristic.py   # deterministic planner (never fails)
│   ├── models_ml.py           # lazy singletons: Whisper, CLIP, detector (load once, keep warm)
│   └── web/index.html         # demo page (vanilla JS)
├── tests/                     # pytest: duration, enforce, validation, heuristic planner, api smoke
├── samples/                   # sample inputs + a generated output
├── Dockerfile · docker-compose.yml · .env.example · requirements.txt
├── README.md · WALKTHROUGH.md · DESIGN.md · IMPLEMENTATION_PLAN.md
```

Separation of concerns: **API** (HTTP only) · **db/repo** (persistence) · **storage** (bytes) ·
**worker** (lifecycle) · **pipeline** (the work, stage-isolated) · **plan** (swappable brains).

---

## 2. Config (`config.py`) — every knob, env-driven

`MAX_FILES=50 · MIN_FILES=1 · MAX_FILE_SIZE=100MB · MAX_TOTAL_SIZE=1GB · ALLOWED_EXT={mp4,mov,webm,mkv}`
`MIN_OUTPUT=10 · MAX_OUTPUT=120 · CLIP_SECONDS=3 · MIN_CLIP=1.0`
`TARGET_W=1920 TARGET_H=1080 FPS=30 ASPECT=16:9` (+ 9:16, 1:1 options)
`WORKER_CONCURRENCY=1 · MAX_JOB_ATTEMPTS=2 · STAGE_TIMEOUTS{...}`
`WHISPER_MODEL=base · ENABLE_CLIP · ENABLE_DETECT · ENABLE_STABILIZE · ENABLE_TRANSITIONS · ENABLE_CAPTIONS · ENABLE_MUSIC`
`ANALYZE_FPS=1.0 ANALYZE_MAXDIM=480 MAX_SEGMENTS_CONSIDERED=120` (bounding)
`PLANNER_CHAIN=[gemini,groq,heuristic] · GEMINI_KEY · GROQ_KEY · LLM_TIMEOUT=30`
`STORAGE_BACKEND=r2|local · R2_ENDPOINT/KEY/SECRET/BUCKET · PRESIGN_TTL=3600`
`UPLOAD_TTL_H=24 · OUTPUT_TTL_H=24 · SEGMENT_TTL_H=24 · DISK_MIN_FREE_MB=500 · TEMP_DIR`

All heavy features are **toggles** so we can trade quality↔compute on the free box.

---

## 3. Data model + state machine

### Tables (SQLite, WAL mode; indices on `status`, `job_id`)
```
jobs(id PK, status, stage, progress_cur, progress_total, attempts,
     target_duration, aspect, brief NULL, detected_genre NULL, rationale NULL,
     planner_used NULL, total_uploaded, used_count, skipped_count,
     output_key NULL, output_duration NULL, error NULL, created_at, updated_at)
files(id PK, job_id FK, original_name, stored_key, size_bytes, duration NULL,
      status[pending|used|skipped], skip_reason NULL)
segments(id PK, job_id FK, source_file_id FK, in_point, out_point, duration,
         normalized_key NULL, score, tags NULL, transcript NULL, status, created_at)
```

### Job state machine
```
queued ──worker claims──▶ processing(stage: ingest→segment→analyze→plan→enforce→render→upload)
   ▲                          │
   │ crash/restart            ├──success──▶ completed (output_key set)
   └──(attempts<MAX)──────────┘
                              └──fatal / attempts≥MAX──▶ failed (error set)
```
- **Atomic claim:** `UPDATE jobs SET status='processing' WHERE id=(SELECT id FROM jobs WHERE
  status='queued' ORDER BY created_at LIMIT 1) RETURNING id` (single-writer safe; WAL + busy_timeout).
- **Crash recovery on boot:** any `processing` job → if `attempts<MAX` reset to `queued`, else `failed`.
- **Progress:** every stage updates `stage`, `progress_cur/total` → surfaced via API.

---

## 4. Pipeline stage contracts (input → output, + bounding/edge cases)

| Stage | Input | Output | Bounding / key edge cases |
|-------|-------|--------|---------------------------|
| **ingest** | file rows + R2 keys | local temp paths + per-file `duration` | ffprobe each; **skip** corrupt/no-video-stream/zero-duration → `files.status=skipped(reason)`; fail job if 0 usable |
| **segment** | usable files | `segments[]` (in/out) | PySceneDetect; **single-take → whole file = 1 segment**; merge < MIN_CLIP; cap to `MAX_SEGMENTS_CONSIDERED` by score |
| **analyze** | segments | scores+tags+transcript per segment | sample @ `ANALYZE_FPS`, downscaled; models lazy-loaded once; a frame decode error → skip frame; no speech → empty transcript; CLIP/detect are toggles |
| **catalog** | analyzed segments | compact text catalog (JSON) | trim to top-K by score to keep prompt small + render bounded |
| **plan** | catalog + brief + target | `Plan{genre,beats[],transitions,music,title,cta,rationale}` | failover chain; invalid JSON / bad segment ref → next provider; heuristic never fails |
| **enforce** | Plan + segments | validated `EDLItem[]` + final duration | snap cuts to sentence/silence; **clamp+water-fill** → sum∈[10,120]; drop beats w/ missing/skipped segment; **fail if total<10** |
| **render** | EDLItem[] | final mp4 (+ persisted segment clips) | reuse persisted segment if same in/out+aspect; one bad segment → skip+continue; out-of-disk guard; encode preset bounds time |
| **upload** | final mp4 | `output_key` + presigned URL | retry on transient R2 error; on success delete inputs proactively |

### Duration enforcer (the guarantee — pure, unit-tested)
1. `target = brief.target or clamp(N*CLIP_SECONDS, 10, 120)`.
2. `max_clips = floor(target/MIN_CLIP)`; if `M_selected > max_clips` → even-sample subset.
3. **water-fill** `base=target/M`, cap each at segment length, redistribute shortfall (DESIGN §4B).
4. `effective = min(target, Σ chosen lengths)`; **if `effective<10` → fail** ("insufficient footage").
5. round to 0.1s, last clip absorbs drift → sum exact.

### Planner output schema (validated with pydantic; same for all providers)
```json
{ "detected_genre","theme","confidence",
  "beats":[{"role","intent","target_seconds","segment_id","in","out"}],
  "transitions":["cut|crossfade"...], "music_mood","title_text","cta_text","rationale" }
```
Heuristic planner builds the *same* object from scores+tags (energy-arc order, group-by-tag).

---

## 5. Render sub-plan (ffmpeg, bounded & robust)

- **Pass A — normalize each segment** (skip if persisted clip exists for same in/out+aspect):
  `trim → [stabilize?] → scale+pad/auto-reframe → color/WB → loudnorm → H.264/AAC` → temp clip →
  **upload to R2 `segments/`** + record `normalized_key`. Silent-audio synth if source has none.
  Bounded memory (one segment at a time); a failed segment is **skipped**, not fatal.
- **Pass B — assemble:** default **concat demuxer `-c copy`** (fast, no re-encode). If
  `ENABLE_TRANSITIONS` → `xfade` filtergraph instead (re-encode at boundaries).
- **Pass C — effects** (single encode pass on the stitched file, only if any enabled):
  burn captions (ASS from Whisper word timings) · music bed `amix` + sidechain **duck** under speech ·
  logo/title/CTA overlays · final encode **1080p H.264 +faststart**.
- `ffmpeg.py`: command builder + runner with **timeout, full stderr capture, non-zero → typed error**.
  Every command logged for debuggability.

---

## 6. Edge-case matrix (explicit handling)

| Area | Edge case | Handling |
|------|-----------|----------|
| Upload | 0 / >50 files | `400 NO_FILES` / `TOO_MANY_FILES` |
| Upload | oversize file/batch (header lies) | enforce **mid-stream**, abort+cleanup → `413` |
| Upload | bad extension / 0-byte | `415` / skip empty |
| Upload | duplicate or path-traversal filename | UUID storage keys; original kept as metadata only |
| Upload | disk below threshold | `503 STORAGE_UNAVAILABLE` |
| Validate | corrupt / no video stream / zero-dur / audio-only | ffprobe → **skip + reason**; fail job only if 0 usable |
| Validate | rotated / VFR / odd dimensions / HDR | normalize handles (apply rotation, cfr, scale, tonemap toggle) |
| Segment | no scene cuts (one take) | whole file = single segment |
| Segment | thousands of micro-scenes | merge < MIN_CLIP, cap by `MAX_SEGMENTS_CONSIDERED` |
| Analyze | no speech / model load fail / frame decode fail | empty transcript / disable feature + log / skip frame |
| Plan | invalid JSON / bad segment ref / 0 beats / overlap | validate → next provider → heuristic floor |
| Plan | all cloud providers rate-limited | heuristic planner (always succeeds) |
| Enforce | total footage < 10s | **fail** with clear message |
| Enforce | snap pushes clip out of source bounds | clamp to bounds, re-balance |
| Render | one segment ffmpeg fails | skip + continue; fail only if <1 usable remains |
| Render | out of disk mid-render | disk guard → fail gracefully + cleanup |
| Storage | R2 up/download/presign error | bounded retry; surface typed error |
| Job | crash mid-job | re-queue (attempts<MAX) else fail |
| Job | SQLite write contention | WAL + busy_timeout + single writer (worker) |
| API | download before ready / failed / expired | `409 NOT_READY` / `409 JOB_FAILED` / `410 OUTPUT_EXPIRED` |
| Cleanup | temp leak on crash | startup janitor sweep + per-job temp delete |

---

## 7. Optimization checklist

- **Streaming uploads** (1 MB chunks) — never whole files in RAM.
- **Frame sampling** for analysis (`ANALYZE_FPS≈1`, downscale to `ANALYZE_MAXDIM`) — not every frame.
- **Models loaded once** (lazy singletons in `models_ml.py`), kept warm across jobs; `faster-whisper`
  (CTranslate2) + small model default.
- **Persisted normalized segments** → variants/regenerate are concat-only (no re-normalize).
- **Catalog + plan caching** keyed on input hash → regenerate/reorder skip re-analysis.
- **Concat-copy** default (no re-encode) when no transitions; effects collapsed into **one** final pass.
- **Bounded prompt** (top-K segments) → tiny token use, free-tier safe; **one LLM call/job**, cached.
- **SQLite WAL + indices**; single-writer worker avoids lock contention.
- **ffmpeg**: sane `-preset` (faster), `-threads` tuned, `+faststart`; per-stage timeouts.
- **Within-job parallelism**: optional bounded thread-pool for independent ffprobe/normalize, gated by
  RAM/CPU (default sequential on the free box; configurable).

---

## 8. Frontend (`web/index.html`) — minimal but complete

Vanilla JS, served at `/`. Handles the **full flow + all edge states**:
- Form: `multiple` file input · optional **brief** textarea · optional **target_duration** · **aspect** select.
- Submit via **XHR with upload-progress bar**; disable button while uploading; show client-side
  pre-checks (count/size) before POST.
- On `202` → poll `GET /api/jobs/{id}` every 2s with **backoff on errors**; render **stage progress**,
  **skip report** (used/skipped + reasons), **detected genre + rationale + planner_used**.
- On `completed` → presigned **download link** + inline `<video>` preview + storyboard list.
- On `failed` → clear error message (from envelope). On `410` → "output expired".
- **Recent jobs** list via `GET /api/jobs`. Graceful handling of network drop / server error envelope.

---

## 9. Build order (each phase independently verifiable)

| Phase | Deliverable | Verify by |
|-------|-------------|-----------|
| **0** | Scaffold: config, FastAPI, `/health`, Docker, compose | `docker-compose up` → health 200 |
| **1** | DB + models + repo + **local** storage backend | unit tests on repo |
| **2** | Upload endpoint + validation + streaming + job create | curl upload → job row, 400/413/415 paths |
| **3** | Worker + state machine + **naive** stitch (concat raw) → download | end-to-end MVP works |
| **4** | `ffmpeg.py` + ingest/ffprobe + **normalize→concat** robust + duration enforcer | mixed-format inputs stitch cleanly, 10–120s |
| **5** | Segmentation + analysis (scoring) + catalog | segments + scores produced |
| **6** | **Heuristic planner** + enforce(snapping) | intelligent edit **fully offline, no keys** |
| **7** | Gemini + Groq planners + **failover chain** + prompt/schema | LLM storyboard; kill keys → heuristic |
| **8** | Whisper + CLIP + face/object understanding | richer catalog; speech-aware cuts |
| **9** | Polish ladder: loudnorm·color·stabilize·transitions·captions·music·logo/CTA | produced-looking output |
| **10** | **R2** backend + presigned + persisted segments + lifecycle | cloud-native; crash-recovery re-runs |
| **11** | Frontend page | click-through demo |
| **12** | Tests · Docker finalize · **HF Spaces deploy** · samples · README · WALKTHROUGH | live URL + docs |
| **Stretch** | variants (A/B) · TTS voiceover · auto-reframe vertical · multi-platform export | as time permits |

**Phase 6 is the key milestone:** a fully working *intelligent* editor with **zero external keys**
(heuristic brain + local everything). Phases 7–10 layer cloud AI + polish on a working base — so we
always have a shippable artifact and never block on keys.

---

## 10. Testing (pytest)

- `test_duration.py` — clamp + water-fill (incl. insufficient-footage fail, rounding-exactness).
- `test_enforce.py` — EDL validation, snapping bounds, beat-drop, [10,120] guarantee.
- `test_validation.py` — file count/size/extension/empty + mid-stream abort.
- `test_planner_heuristic.py` — deterministic plan from a fixture catalog.
- `test_api_smoke.py` — upload→poll→download happy path (local backend, naive render).

---

## 11. Open items to confirm before/at build time (not blocking the plan)

- Two **free-tier keys** (Gemini + Groq) + an **R2 bucket + token** — needed only for Phases 7 & 10;
  we build/test Phases 0–6, 8–9, 11 locally without them.
- A small set of **sample videos** for `samples/` and demo (you provide, or I fetch CC-licensed).
- Royalty-free **music track(s)** + a placeholder **logo** for the polish phase.
```
