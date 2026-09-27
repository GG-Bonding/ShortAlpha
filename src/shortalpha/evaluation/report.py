"""Summaries of score versus later excess return. A failed relationship is printed."""

from collections.abc import Sequence

_FACTORS = ("momentum", "volume", "event", "relative_strength", "price_action")


def render_evaluation(
    runs: Sequence[tuple[dict[str, object], Sequence[object]]],
    *,
    long_threshold: float,
    horizons: tuple[int, ...],
    buckets: tuple[tuple[float, float], ...],
    min_bucket_count: int,
) -> str:
    lines = ["ShortAlpha evaluation", f"runs: {len(runs)}"]
    ranked_count = sum(len(_ranked(snapshot)) for snapshot, _rows in runs)
    lines.append(f"symbols: {ranked_count}")
    if not runs:
        lines.append("INSUFFICIENT SAMPLE")
        return "\n".join(lines)
    for horizon in horizons:
        observations = _observations(runs, horizon)
        lines.append("")
        lines.append(f"{horizon}D")
        lines.append(f"completed: {len(observations)}")
        if not observations:
            lines.append("INSUFFICIENT SAMPLE")
            continue
        returns = [item["stock_return"] for item in observations]
        excess = [item["excess_return"] for item in observations]
        maes = [item["mae"] for item in observations]
        mfes = [item["mfe"] for item in observations]
        hits = sum(value > 0 for value in returns)
        lines.append(f"hit_rate: {_fmt(hits / len(returns))}")
        lines.append(f"avg_return: {_fmt(_mean(returns))}")
        lines.append(f"median_return: {_fmt(_median(returns))}")
        lines.append(f"avg_excess: {_fmt(_mean(excess))}")
        lines.append(f"median_excess: {_fmt(_median(excess))}")
        lines.append(f"win_loss: {_win_loss(returns)}")
        lines.append(f"avg_mae: {_fmt(_mean(maes))}")
        lines.append(f"worst_mae: {_fmt(min(maes))}")
        lines.append(f"avg_mfe: {_fmt(_mean(mfes))}")
        lines.append(f"best_mfe: {_fmt(max(mfes))}")
        lines.extend(_high_low(observations, long_threshold))
        lines.extend(_buckets(observations, buckets, min_bucket_count, horizon))
        lines.extend(_attribution(observations, horizon))
    return "\n".join(lines)


def _observations(
    runs: Sequence[tuple[dict[str, object], Sequence[object]]], horizon: int
) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for snapshot, rows in runs:
        by_symbol = {row["symbol"]: row for row in rows if int(row["horizon"]) == horizon}
        for ranked in _ranked(snapshot):
            row = by_symbol.get(ranked["symbol"])
            if row is None:
                continue
            factors = ranked["factors"]
            if not isinstance(factors, dict):
                continue
            found.append(
                {
                    "symbol": ranked["symbol"],
                    "total_score": float(ranked["total_score"]),
                    "stock_return": float(row["stock_return"]),
                    "excess_return": float(row["excess_return"]),
                    "mae": float(row["mae"]),
                    "mfe": float(row["mfe"]),
                    "factors": {
                        name: float(factors[name]["score"])
                        for name in _FACTORS
                        if isinstance(factors.get(name), dict)
                        and factors[name].get("score") is not None
                    },
                }
            )
    return found


def _ranked(snapshot: dict[str, object]) -> list[dict[str, object]]:
    rows = snapshot.get("ranked")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _high_low(observations: list[dict[str, object]], threshold: float) -> list[str]:
    high = [item["excess_return"] for item in observations if item["total_score"] >= threshold]
    low = [item["excess_return"] for item in observations if item["total_score"] < threshold]
    lines = ["", f"score >= {threshold:g} vs < {threshold:g}"]
    if not high or not low:
        lines.append("INSUFFICIENT SAMPLE")
        return lines
    high_mean = _mean(high)
    low_mean = _mean(low)
    lines.append(f"avg_excess_high: {_fmt(high_mean)}")
    lines.append(f"avg_excess_low: {_fmt(low_mean)}")
    if high_mean <= low_mean:
        lines.append("HIGH SCORE DOES NOT BEAT LOW SCORE")
    return lines


def _buckets(
    observations: list[dict[str, object]],
    buckets: tuple[tuple[float, float], ...],
    minimum: int,
    horizon: int,
) -> list[str]:
    lines = ["", "buckets"]
    usable: list[float] = []
    for index, (low, high) in enumerate(buckets):
        last = index == len(buckets) - 1
        chosen = [
            item
            for item in observations
            if low <= item["total_score"] < high or (last and item["total_score"] == high)
        ]
        label = f"{low:g}-{high:g}"
        if len(chosen) < minimum:
            lines.append(f"{label}: INSUFFICIENT SAMPLE")
            continue
        excess = _mean([item["excess_return"] for item in chosen])
        returns = [item["stock_return"] for item in chosen]
        hits = sum(value > 0 for value in returns)
        usable.append(excess)
        lines.append(
            f"{label}: count={len(chosen)} hit_rate={_fmt(hits / len(chosen))} "
            f"avg_return={_fmt(_mean(returns))} avg_excess={_fmt(excess)}"
        )
    if len(usable) < 2:
        lines.append(f"{horizon}D monotonic: INSUFFICIENT SAMPLE")
    elif all(left < right for left, right in zip(usable, usable[1:], strict=False)):
        lines.append(f"{horizon}D monotonic: MONOTONIC")
    else:
        lines.append(f"{horizon}D monotonic: NO MONOTONIC RELATIONSHIP")
    return lines


def _attribution(observations: list[dict[str, object]], horizon: int) -> list[str]:
    lines = ["", f"factor attribution {horizon}D"]
    if len(observations) < 2:
        lines.append("INSUFFICIENT SAMPLE")
        return lines
    for name in _FACTORS:
        paired = [
            (float(item["factors"][name]), float(item["excess_return"]))
            for item in observations
            if name in item["factors"]
        ]
        if len(paired) < 2:
            lines.append(f"{name}: INSUFFICIENT SAMPLE")
            continue
        scores = [item[0] for item in paired]
        excess = [item[1] for item in paired]
        correlation = _spearman(scores, excess)
        gap = _half_gap(scores, excess)
        corr_text = "UNDEFINED" if correlation is None else _fmt(correlation)
        gap_text = "UNDEFINED" if gap is None else _fmt(gap)
        lines.append(f"{name}: spearman={corr_text} top_half_minus_bottom_half={gap_text}")
    return lines


def _win_loss(returns: list[float]) -> str:
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    if not losses:
        return "UNDEFINED"
    if not wins:
        return _fmt(0)
    return _fmt(_mean(wins) / abs(_mean(losses)))


def _half_gap(scores: list[float], excess: list[float]) -> float | None:
    order = sorted(range(len(scores)), key=lambda index: scores[index])
    if len(order) < 2:
        return None
    half = len(order) // 2
    if half == 0:
        return None
    bottom = order[:half]
    top = order[-half:]
    return _mean([excess[index] for index in top]) - _mean([excess[index] for index in bottom])


def _spearman(left: list[float], right: list[float]) -> float | None:
    return _pearson(_ranks(left), _ranks(right))


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        stop = start
        while stop + 1 < len(order) and values[order[stop + 1]] == values[order[start]]:
            stop += 1
        rank = (start + stop) / 2 + 1
        for index in range(start, stop + 1):
            ranks[order[index]] = rank
        start = stop + 1
    return ranks


def _pearson(left: list[float], right: list[float]) -> float | None:
    mean_left = _mean(left)
    mean_right = _mean(right)
    numerator = sum((x - mean_left) * (y - mean_right) for x, y in zip(left, right, strict=True))
    left_scale = sum((x - mean_left) ** 2 for x in left) ** 0.5
    right_scale = sum((y - mean_right) ** 2 for y in right) ** 0.5
    if left_scale == 0 or right_scale == 0:
        return None
    return numerator / (left_scale * right_scale)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _fmt(value: float) -> str:
    return f"{value:.4f}"
