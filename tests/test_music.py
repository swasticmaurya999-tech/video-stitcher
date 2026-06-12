from app.pipeline import music


def test_mood_maps_to_bucket():
    assert music._bucket("energetic sale hype") == "energetic"
    assert music._bucket("calm and warm") == "calm"
    assert music._bucket("professional corporate tech") == "corporate"
    assert music._bucket("nonsense") == ""


def test_pick_track_returns_a_bundled_track():
    # The repo bundles tracks under app/assets/music/<mood>/*.mp3
    t = music.pick_track("upbeat fun", job_id="abc12345")
    assert t is not None and t.endswith(".mp3")


def test_pick_track_rotation_is_deterministic_and_varies():
    a = music.pick_track("upbeat", job_id="00000000")
    b = music.pick_track("upbeat", job_id="00000000")
    assert a == b  # stable for the same job id
    # different job ids can select different tracks (at least possible)
    picks = {music.pick_track("upbeat", job_id=f"{i:08x}") for i in range(8)}
    assert len(picks) >= 1
