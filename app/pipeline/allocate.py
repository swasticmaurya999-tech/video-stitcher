"""Pure duration math — the heart of the 10-120s guarantee (DESIGN §4A/§4B/§4F).

Kept dependency-free and side-effect-free so it is exhaustively unit-testable.
"""
from __future__ import annotations


def compute_target(
    n_usable: int,
    clip_seconds: float,
    min_out: int,
    max_out: int,
    user_target: int | None = None,
) -> int:
    """Decide the target output length. User value wins (validated upstream); else computed."""
    if user_target is not None:
        return max(min_out, min(max_out, int(user_target)))
    ideal = n_usable * clip_seconds
    return int(max(min_out, min(max_out, round(ideal))))


def select_subset(count: int, target: float, min_clip: float) -> list[int]:
    """Indices of the items to feature. If too many to fit at MIN_CLIP, even-sample across the batch.

    Returns indices into the original 0..count-1 ordering (preserves upload order).
    """
    if count <= 0:
        return []
    max_clips = max(1, int(target // min_clip))
    if count <= max_clips:
        return list(range(count))
    # Even-sample `max_clips` indices spread across the whole batch.
    step = count / max_clips
    picked = sorted({min(count - 1, int(i * step)) for i in range(max_clips)})
    return picked


def water_fill(capacities: list[float], target: float) -> list[float]:
    """Distribute `target` seconds across items, each capped at its capacity (DESIGN §4B).

    Short items cap out and release their leftover, which is redistributed to items with headroom.
    Returns per-item allocations. Sum == target when total capacity >= target, else == total capacity.
    """
    n = len(capacities)
    alloc = [0.0] * n
    if n == 0 or target <= 0:
        return alloc
    open_idx = list(range(n))
    budget = float(target)
    # Iterate; each pass either finishes or removes >=1 capped item.
    while open_idx:
        share = budget / len(open_idx)
        constrained = [i for i in open_idx if capacities[i] < share - 1e-9]
        if constrained:
            for i in constrained:
                alloc[i] = capacities[i]
                budget -= capacities[i]
                open_idx.remove(i)
            if budget <= 1e-9:
                break
        else:
            for i in open_idx:
                alloc[i] = share
            break
    return alloc


def weighted_water_fill(capacities: list[float], weights: list[float], target: float) -> list[float]:
    """Like `water_fill`, but each clip's fair share is proportional to its `weight` (the LLM's
    intended length / pacing) instead of equal. Short clips still cap out and release their leftover.

    Equal weights reproduce `water_fill`. Sum == target when total capacity >= target.
    """
    n = len(capacities)
    alloc = [0.0] * n
    if n == 0 or target <= 0:
        return alloc
    w = [max(0.0, x) for x in weights]
    open_idx = list(range(n))
    budget = float(target)
    while open_idx:
        total_w = sum(w[i] for i in open_idx)
        if total_w <= 1e-9:  # no usable weights → fall back to an even split
            share = budget / len(open_idx)
            constrained = [i for i in open_idx if capacities[i] < share - 1e-9]
            if constrained:
                for i in constrained:
                    alloc[i] = capacities[i]
                    budget -= capacities[i]
                    open_idx.remove(i)
                if budget <= 1e-9:
                    break
                continue
            for i in open_idx:
                alloc[i] = share
            break
        constrained = [i for i in open_idx if capacities[i] < budget * w[i] / total_w - 1e-9]
        if constrained:
            for i in constrained:
                alloc[i] = capacities[i]
                budget -= capacities[i]
                open_idx.remove(i)
            if budget <= 1e-9:
                break
            continue
        for i in open_idx:
            alloc[i] = budget * w[i] / total_w
        break
    return alloc


def round_allocations(alloc: list[float], precision: float = 0.1) -> list[float]:
    """Round to `precision`, then make the largest allocation absorb the rounding drift so the
    total is preserved exactly (no cumulative drift away from the target)."""
    if not alloc:
        return alloc
    target_sum = sum(alloc)
    rounded = [round(a / precision) * precision for a in rounded_guard(alloc)]
    drift = target_sum - sum(rounded)
    # Apply the exact drift to the largest clip so the total is preserved precisely. (This clip is
    # capped to its available span later in build_edl, so a sub-0.1 value here is harmless.)
    j = max(range(len(rounded)), key=lambda i: rounded[i])
    rounded[j] = round(max(0.0, rounded[j] + drift), 4)
    return rounded


def rounded_guard(alloc: list[float]) -> list[float]:
    return [max(0.0, a) for a in alloc]
