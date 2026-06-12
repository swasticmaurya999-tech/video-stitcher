from app.models import EDLItem
from app.pipeline import captions


def _item(words):
    return EDLItem(segment_id="s", source_path="/x.mp4", in_point=0, out_point=5, duration=5, words=words)


def test_build_ass_remaps_to_output_timeline(tmp_path):
    # clip 0 starts at 0s, clip 1 starts at 5s; a word at clip-relative 1s in clip 1 → global 6s.
    edl = [
        _item([{"word": "hello", "start": 0.5, "end": 1.0}]),
        _item([{"word": "world", "start": 1.0, "end": 1.5}]),
    ]
    out = tmp_path / "c.ass"
    assert captions.build_ass(edl, [0.0, 5.0], (1920, 1080), str(out)) is True
    text = out.read_text(encoding="utf-8")
    assert "Dialogue:" in text and "hello" in text and "world" in text
    assert "0:00:06.00" in text  # world: 5.0 + 1.0


def test_build_ass_empty_when_no_words(tmp_path):
    out = tmp_path / "c.ass"
    assert captions.build_ass([_item([])], [0.0], (1920, 1080), str(out)) is False


def test_ts_formatting():
    assert captions._ts(6.0) == "0:00:06.00"
    assert captions._ts(75.25) == "0:01:15.25"
