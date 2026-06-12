from app.pipeline.beats import snap_durations


def test_snap_preserves_total_and_min_clip():
    durs = [3.0, 3.0, 3.0]
    beats = (1.4, 2.8, 4.3, 5.9, 7.4, 8.9)  # ~every 1.45s
    out = snap_durations(durs, beats, min_clip=1.0)
    assert abs(sum(out) - sum(durs)) < 1e-6     # total preserved (duration guarantee intact)
    assert all(d >= 1.0 - 1e-6 for d in out)    # each clip >= min_clip
    assert len(out) == len(durs)


def test_snap_moves_boundaries_only_slightly():
    durs = [4.0, 4.0]
    beats = (3.7, 7.5)   # boundary at 4.0 -> nearest beat 3.7 (0.3s, within MAX_SNAP)
    out = snap_durations(durs, beats, min_clip=1.0)
    assert abs(out[0] - 3.7) < 1e-6
    assert abs(sum(out) - 8.0) < 1e-6


def test_snap_noop_without_enough_beats_or_clips():
    assert snap_durations([5.0, 5.0], (), 1.0) == [5.0, 5.0]
    assert snap_durations([10.0], (1.0, 2.0), 1.0) == [10.0]


def test_snap_ignores_far_beats():
    durs = [4.0, 4.0]
    beats = (1.0, 7.0)   # nearest beat to 4.0 is 3.0s away (> MAX_SNAP) -> no snap
    out = snap_durations(durs, beats, min_clip=1.0)
    assert abs(out[0] - 4.0) < 1e-6
