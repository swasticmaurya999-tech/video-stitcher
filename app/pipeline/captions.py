"""Build styled ASS captions from the EDL's clip-relative word timestamps.

Each clip's words are shifted onto the OUTPUT timeline using `clip_starts` (the output start time
of each clip, which accounts for crossfade overlaps when transitions are enabled). Words are grouped
into short lower-third lines (bold white, thick outline) — readable on any background, sound-off.
"""
from __future__ import annotations

from app.models import EDLItem

WORDS_PER_LINE = 6
MIN_LINE_SEC = 0.6


def _ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _header(width: int, height: int, fontsize: int) -> str:
    margin_v = max(40, height // 10)
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\n"
        "WrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, "
        "Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
        f"Style: Default,DejaVu Sans,{fontsize},&H00FFFFFF,&H00000000,&H64000000,1,0,1,3,1,2,"
        f"60,60,{margin_v}\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def _escape(text: str) -> str:
    return text.replace("\n", " ").replace("{", "(").replace("}", ")").strip()


def build_ass(
    edl: list[EDLItem], clip_starts: list[float], dims: tuple[int, int], out_path: str,
    fontsize_div: int = 18,
) -> bool:
    """Write an ASS file. Returns True if any caption events were produced."""
    width, height = dims
    fontsize = max(16, height // max(8, fontsize_div))
    lines: list[str] = []

    for i, item in enumerate(edl):
        base = clip_starts[i] if i < len(clip_starts) else 0.0
        words = [w for w in item.words if w.get("word")]
        for j in range(0, len(words), WORDS_PER_LINE):
            group = words[j: j + WORDS_PER_LINE]
            start = base + min(w["start"] for w in group)
            end = base + max(w["end"] for w in group)
            if end - start < MIN_LINE_SEC:
                end = start + MIN_LINE_SEC
            text = _escape(" ".join(w["word"] for w in group))
            if text:
                lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Default,,0,0,0,,{text}")

    if not lines:
        return False
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(_header(width, height, fontsize))
        f.write("\n".join(lines) + "\n")
    return True
