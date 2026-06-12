"""Thin, transparent wrapper over ffmpeg/ffprobe.

We build commands explicitly and run them as subprocesses with a timeout and full stderr capture,
so failures are debuggable and isolated. No third-party ffmpeg wrapper (DESIGN §2).
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from app.config import settings


class FfmpegError(RuntimeError):
    def __init__(self, cmd: list[str], stderr: str):
        self.cmd = cmd
        self.stderr = stderr[-2000:]
        super().__init__(f"ffmpeg failed: {' '.join(cmd[:6])} ... :: {self.stderr[-400:]}")


@dataclass
class MediaInfo:
    duration: float
    has_video: bool
    has_audio: bool
    width: int
    height: int


def run(cmd: list[str], timeout: int = 1800) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise FfmpegError(cmd, proc.stderr or "")
    return proc.stdout


def probe(path: str) -> MediaInfo:
    """Authoritative validation + metadata via ffprobe (DESIGN §3 layer 2)."""
    out = run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path],
        timeout=60,
    )
    data = json.loads(out)
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    dur = 0.0
    try:
        dur = float(data.get("format", {}).get("duration", 0) or 0)
    except (TypeError, ValueError):
        dur = 0.0
    if v is not None and dur <= 0:
        try:
            dur = float(v.get("duration", 0) or 0)
        except (TypeError, ValueError):
            dur = 0.0
    return MediaInfo(
        duration=dur,
        has_video=v is not None,
        has_audio=a is not None,
        width=int(v.get("width", 0)) if v else 0,
        height=int(v.get("height", 0)) if v else 0,
    )


def _video_filter(dims: tuple[int, int], fps: int, stabilize: bool) -> str:
    w, h = dims
    chain = []
    if stabilize:
        chain.append("deshake")
    chain += [
        f"scale={w}:{h}:force_original_aspect_ratio=decrease",
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
        "setsar=1",
        f"fps={fps}",
        "format=yuv420p",
    ]
    return ",".join(chain)


def normalize_clip(
    src: str,
    in_point: float,
    duration: float,
    out_path: str,
    dims: tuple[int, int],
    fps: int,
    has_audio: bool,
    stabilize: bool = False,
    preset: str = "faster",
) -> None:
    """Trim [in, in+duration] of `src` and re-encode to the common profile.

    Guarantees exactly one video + one (possibly synthesized-silent) audio stream so the
    downstream concat is uniform.
    """
    vf = _video_filter(dims, fps, stabilize)
    cmd = ["ffmpeg", "-y", "-ss", f"{in_point:.3f}", "-t", f"{duration:.3f}", "-i", src]
    if not has_audio:
        # Synthesize a silent track matching the clip length.
        cmd += ["-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        amap = ["-map", "1:a:0"]
    else:
        amap = ["-map", "0:a:0?"]
    cmd += [
        "-map", "0:v:0",
        *amap,
        "-vf", vf,
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",  # EBU R128 — consistent loudness across clips
        "-c:v", "libx264", "-preset", preset, "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "44100", "-ac", "2",
        "-shortest", "-movflags", "+faststart",
        out_path,
    ]
    run(cmd)


def concat_copy(list_file: str, out_path: str) -> None:
    """Fast concat of pre-normalized clips (no re-encode)."""
    run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
        "-c", "copy", "-movflags", "+faststart", out_path,
    ])


def burn_captions(video_in: str, ass_path: str, out_path: str) -> None:
    """Burn styled ASS captions onto the video (libass). Re-encodes video; audio copied."""
    # ass filter needs ':' and '\' escaped; container paths are POSIX so this is usually a no-op.
    safe = ass_path.replace("\\", "/").replace(":", "\\:")
    run([
        "ffmpeg", "-y", "-i", video_in, "-vf", f"ass={safe}",
        "-c:v", "libx264", "-preset", "faster", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "copy", "-movflags", "+faststart", out_path,
    ])


def add_text_overlays(
    video_in: str,
    out_path: str,
    duration: float,
    font: str,
    title_file: str | None = None,
    cta_file: str | None = None,
) -> None:
    """Burn a brand title (intro, first 3s, centered) and CTA (end, last 3s, lower) onto the video.

    Text is read from files via ffmpeg `textfile=` so arbitrary company names/punctuation can't
    break the filter graph. Re-encodes video; audio is copied through.
    """
    filters = []
    if title_file:
        filters.append(
            f"drawtext=fontfile={font}:textfile={title_file}:fontcolor=white:fontsize=h/14:"
            f"box=1:boxcolor=black@0.45:boxborderw=24:x=(w-text_w)/2:y=(h-text_h)/2:enable='lt(t,3)'"
        )
    if cta_file:
        end_start = max(0.0, duration - 3.0)
        filters.append(
            f"drawtext=fontfile={font}:textfile={cta_file}:fontcolor=white:fontsize=h/22:"
            f"box=1:boxcolor=black@0.55:boxborderw=18:x=(w-text_w)/2:y=h-h/5:"
            f"enable='gt(t,{end_start:.2f})'"
        )
    if not filters:
        run(["ffmpeg", "-y", "-i", video_in, "-c", "copy", out_path])
        return
    run([
        "ffmpeg", "-y", "-i", video_in, "-vf", ",".join(filters),
        "-c:v", "libx264", "-preset", "faster", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "copy", "-movflags", "+faststart", out_path,
    ])


def apply_audio_ducked(
    video_in: str, out_path: str, duration: float, music: str, music_volume: float = 0.5
) -> None:
    """Keep the clip audio (speech) and mix a music bed UNDERNEATH it that automatically ducks
    when speech is present (sidechaincompress) and swells in the gaps. Preserves the message while
    sounding intentional. Video is stream-copied; audio re-encoded.
    """
    fade_out = max(0.0, duration - 1.5)
    fc = (
        f"[1:a]volume={music_volume},afade=t=in:st=0:d=1,afade=t=out:st={fade_out:.2f}:d=1.5[m];"
        f"[0:a]asplit=2[sc][a0];"
        f"[m][sc]sidechaincompress=threshold=0.02:ratio=8:attack=15:release=350[duck];"
        f"[a0][duck]amix=inputs=2:duration=first:normalize=0[a]"
    )
    run([
        "ffmpeg", "-y", "-i", video_in, "-stream_loop", "-1", "-i", music,
        "-filter_complex", fc, "-map", "0:v:0", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-ar", "44100", "-ac", "2",
        "-shortest", "-movflags", "+faststart", out_path,
    ])


def apply_audio(
    video_in: str, out_path: str, duration: float, mode: str, music: str, volume: float
) -> None:
    """Set the output soundtrack.

    mode="music": replace audio with the looping music bed only (mute clips — clean montage).
    mode="mix":   music bed UNDER the original clip audio.
    Music is looped to cover the video and faded in/out. Video is stream-copied (audio re-encoded).
    """
    fade_out_start = max(0.0, duration - 1.5)
    bed = (
        f"[1:a]volume={volume},afade=t=in:st=0:d=1,afade=t=out:st={fade_out_start:.2f}:d=1.5"
    )
    if mode == "music":
        fc = f"{bed}[a]"
    else:  # mix
        fc = f"{bed}[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2[a]"
    run([
        "ffmpeg", "-y", "-i", video_in, "-stream_loop", "-1", "-i", music,
        "-filter_complex", fc, "-map", "0:v:0", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-ar", "44100", "-ac", "2",
        "-shortest", "-movflags", "+faststart", out_path,
    ])


def fade_video(video_in: str, out_path: str, duration: float, fin: float = 0.4, fout: float = 0.8) -> None:
    """Fade the video in from black at the start and out to black at the end (+ matching audio fade)
    — gives a clean, intentional ending instead of an abrupt cut to nothing. Re-encodes."""
    fout_start = max(0.0, duration - fout)
    vf = f"fade=t=in:st=0:d={fin},fade=t=out:st={fout_start:.2f}:d={fout}"
    af = f"afade=t=in:st=0:d={fin},afade=t=out:st={fout_start:.2f}:d={fout}"
    run([
        "ffmpeg", "-y", "-i", video_in, "-vf", vf, "-af", af,
        "-c:v", "libx264", "-preset", "faster", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "44100", "-ac", "2", "-movflags", "+faststart", out_path,
    ])


def concat_with_transitions(
    clips: list[str], durations: list[float], d_list: list[float], out_path: str,
    transition: str = "dissolve",
) -> None:
    """Concatenate normalized clips with crossfade dissolves (video `xfade` + audio `acrossfade`).

    `d_list[i-1]` is the dissolve length between clip i-1 and i (clamped per-pair by the caller so
    even short clips get a transition — no hard cuts). Re-encodes.
    """
    n = len(clips)
    if n == 1:
        run(["ffmpeg", "-y", "-i", clips[0], "-c", "copy", "-movflags", "+faststart", out_path])
        return
    inputs: list[str] = []
    for c in clips:
        inputs += ["-i", c]
    vparts, aparts = [], []
    vlabel, alabel = "0:v", "0:a"
    cum = durations[0]
    for i in range(1, n):
        d = d_list[i - 1]
        offset = max(0.0, cum - d)
        nv, na = f"v{i}", f"a{i}"
        vparts.append(f"[{vlabel}][{i}:v]xfade=transition={transition}:duration={d:.3f}:offset={offset:.3f}[{nv}]")
        aparts.append(f"[{alabel}][{i}:a]acrossfade=d={d:.3f}[{na}]")
        vlabel, alabel = nv, na
        cum = cum + durations[i] - d
    fc = ";".join(vparts + aparts)
    run([
        "ffmpeg", "-y", *inputs, "-filter_complex", fc,
        "-map", f"[{vlabel}]", "-map", f"[{alabel}]",
        "-c:v", "libx264", "-preset", "faster", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "44100", "-ac", "2", "-movflags", "+faststart", out_path,
    ])


def concat_filter(clips: list[str], out_path: str, dims: tuple[int, int], fps: int) -> None:
    """Concat via filter graph (re-encode) — used when clips might differ; safe fallback."""
    inputs = []
    for c in clips:
        inputs += ["-i", c]
    n = len(clips)
    streams = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(n))
    filtergraph = f"{streams}concat=n={n}:v=1:a=1[v][a]"
    run([
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filtergraph, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "faster", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "44100", "-ac", "2",
        "-movflags", "+faststart", out_path,
    ])
