from app.models import Segment
from app.pipeline.plan.heuristic import HeuristicPlanner


def _seg(sid, dur, score):
    return Segment(
        id=sid, job_id="j", source_file_id="f", in_point=0.0, out_point=dur, duration=dur,
        score=score, source_path=f"/tmp/{sid}.mp4",
    )


def test_heuristic_plan_orders_hook_first_and_uses_valid_ids():
    segs = [_seg("a", 5, 0.2), _seg("b", 5, 0.9), _seg("c", 5, 0.5)]
    plan = HeuristicPlanner().plan_from_segments(segs, brief=None, target=12)
    assert plan.planner_used == "heuristic"
    assert plan.beats
    ids = {s.id for s in segs}
    assert all(b.segment_id in ids for b in plan.beats)
    assert plan.beats[0].segment_id == "b"   # highest score becomes the hook
    assert plan.beats[0].role == "hook"


def test_heuristic_respects_min_clip_cap():
    segs = [_seg(str(i), 5, 0.5) for i in range(40)]
    plan = HeuristicPlanner().plan_from_segments(segs, brief=None, target=10)
    assert len(plan.beats) <= 10  # target/min_clip
