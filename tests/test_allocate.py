from app.pipeline.allocate import compute_target, round_allocations, select_subset, water_fill


def test_compute_target_computed_and_clamped():
    assert compute_target(1, 3, 10, 120) == 10      # 1*3=3 -> clamp up to 10
    assert compute_target(20, 3, 10, 120) == 60     # in range
    assert compute_target(50, 3, 10, 120) == 120    # 150 -> clamp down
    assert compute_target(4, 3, 10, 120) == 12


def test_compute_target_user_override():
    assert compute_target(50, 3, 10, 120, user_target=30) == 30
    assert compute_target(5, 3, 10, 120, user_target=200) == 120  # clamped
    assert compute_target(5, 3, 10, 120, user_target=2) == 10


def test_water_fill_redistribution():
    # DESIGN worked example: short clips cap out, leftovers flow to clips with headroom.
    alloc = water_fill([1, 2, 3, 50, 50], 15)
    assert abs(sum(alloc) - 15) < 1e-6
    assert alloc[0] == 1 and alloc[1] == 2 and alloc[2] == 3
    assert abs(alloc[3] - 4.5) < 1e-6 and abs(alloc[4] - 4.5) < 1e-6


def test_water_fill_insufficient_capacity():
    # Total capacity below target -> everyone caps at their own length.
    alloc = water_fill([2, 1, 3], 30)
    assert alloc == [2, 1, 3]


def test_round_allocations_preserves_total():
    alloc = water_fill([2, 1, 4.33, 4.33, 4.33], 16)
    r = round_allocations(alloc)
    assert abs(sum(r) - sum(alloc)) < 1e-6


def test_select_subset_even_sampling():
    # 50 clips, target 10s, min_clip 1 -> at most 10 featured, spread across the batch.
    picked = select_subset(50, 10, 1.0)
    assert len(picked) == 10
    assert picked[0] == 0 and picked[-1] < 50
    assert picked == sorted(picked)


def test_select_subset_all_fit():
    assert select_subset(5, 30, 1.0) == [0, 1, 2, 3, 4]
