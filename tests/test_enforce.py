import pytest

from app.models import Beat, Plan, Segment
from app.pipeline.enforce import InsufficientFootage, build_edl


def _seg(sid, dur):
    return Segment(
        id=sid, job_id="j", source_file_id="f", in_point=0.0, out_point=dur, duration=dur,
        source_path=f"/tmp/{sid}.mp4",
    )


def _beat(sid, in_p, out_p):
    b = Beat(role="body", intent="", target_seconds=out_p - in_p, segment_id=sid, in_point=in_p, out_point=out_p)
    b.transition_in = "cut"
    return b


def test_build_edl_hits_target_in_range():
    segs = [_seg("a", 4), _seg("b", 4), _seg("c", 4), _seg("d", 4)]
    plan = Plan(beats=[_beat("a", 0, 4), _beat("b", 0, 4), _beat("c", 0, 4), _beat("d", 0, 4)])
    edl, dur = build_edl(plan, segs, target=12)
    assert 10 <= dur <= 120
    assert abs(dur - 12) < 0.5
    assert len(edl) >= 1


def test_build_edl_upper_bound_enforced():
    segs = [_seg("a", 300)]
    plan = Plan(beats=[_beat("a", 0, 300)])
    edl, dur = build_edl(plan, segs, target=999)  # caller passes oversized; enforcer clamps to 120
    assert dur <= 120


def test_build_edl_extends_short_beats_to_meet_target():
    # LLM proposed tiny 2s beats, but each clip has 8s of footage → must extend/top-up to >=10s.
    segs = [_seg("a", 8), _seg("b", 8), _seg("c", 8)]
    plan = Plan(beats=[_beat("a", 0, 2), _beat("b", 0, 2)])  # only 4s proposed, only 2 clips
    edl, dur = build_edl(plan, segs, target=12)
    assert 10 <= dur <= 120
    assert abs(dur - 12) < 0.6   # reached the target by extending + adding clip c


def test_floor_guaranteed_when_total_footage_sufficient():
    # 3 clips of 3.5s = 10.5s total. Target lands on the 10s floor; rounding must NOT drop us under.
    segs = [_seg("a", 3.5), _seg("b", 3.5), _seg("c", 3.5)]
    plan = Plan(beats=[_beat("a", 0, 3.5), _beat("b", 0, 3.5), _beat("c", 0, 3.5)])
    edl, dur = build_edl(plan, segs, target=10)
    assert dur >= 10.0          # never reports/produces under the floor when footage allows
    assert dur <= 120.0


def test_upper_bound_clamped_with_many_long_clips():
    segs = [_seg(str(i), 60) for i in range(5)]  # 300s of footage
    plan = Plan(beats=[_beat(str(i), 0, 60) for i in range(5)])
    edl, dur = build_edl(plan, segs, target=999)  # caller over-asks
    assert dur <= 120.0


def test_build_edl_insufficient_footage():
    segs = [_seg("a", 3), _seg("b", 2)]  # 5s total < 10s floor
    plan = Plan(beats=[_beat("a", 0, 3), _beat("b", 0, 2)])
    with pytest.raises(InsufficientFootage):
        build_edl(plan, segs, target=10)


def test_build_edl_drops_hallucinated_segments_then_falls_back():
    segs = [_seg("a", 6), _seg("b", 6)]
    # plan references a non-existent segment id -> dropped; falls back to all segments.
    plan = Plan(beats=[_beat("ghost", 0, 5)])
    edl, dur = build_edl(plan, segs, target=10)
    assert 10 <= dur <= 120
