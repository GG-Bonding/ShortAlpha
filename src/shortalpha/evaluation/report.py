"""Summaries of score versus later excess return. A failed relationship is printed."""

from collections.abc import Sequence
from datetime import datetime

from shortalpha.calendar import NYSECalendar
from shortalpha.evaluation.book import contiguous_blocks, day_contribution

_FACTORS = ("momentum", "volume", "event", "relative_strength", "price_action")
_COHORTS = ("ALL", "PUBLISHED", "VETOED", "NOT_SELECTED")
_EMPTY = "no qualifying events"


def render_evaluation(
    runs: Sequence[tuple[dict[str, object], Sequence[object]]],
    *,
    long_threshold: float,
    horizons: tuple[int, ...],
    buckets: tuple[tuple[float, float], ...],
    min_bucket_count: int,
    as_of: datetime | None = None,
    top_n: int = 3,
    round_trip_cost: float = 0.001,
    primary_horizon: int = 3,
    block_sessions: int = 3,
    calendar: NYSECalendar | None = None,
    strategy_version: str = "",
    run_mode: str = "",
    run_ids: tuple[str, ...] = (),
) -> str:
    if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
        raise ValueError("as_of must be timezone-aware")
    lines = ["ShortAlpha evaluation", f"runs: {len(runs)}"]
    if strategy_version:
        lines.append(f"strategy_version: {strategy_version}")
    if run_mode:
        lines.append(f"run_mode: {run_mode}")
    if run_ids:
        lines.append(f"run_ids: {' '.join(run_ids)}")
    ranked_count = sum(len(_ranked(snapshot)) for snapshot, _rows in runs)
    lines.append(f"symbols: {ranked_count}")
    lines.append("monotonic_use: DIAGNOSTIC")
    lines.append("bucket_minimum_use: DISPLAY")
    if not runs:
        lines.append("INSUFFICIENT SAMPLE")
        return "\n".join(lines)
    for cohort in _COHORTS:
        lines.append("")
        lines.append(f"cohort: {cohort}")
        for horizon in horizons:
            observations = _observations(runs, horizon, as_of, cohort=cohort)
            lines.append("")
            lines.append(f"{horizon}D")
            lines.append(f"completed: {len(observations)}")
            if not observations:
                lines.append("INSUFFICIENT SAMPLE")
                continue
            lines.extend(_return_lines(observations))
            if cohort == "ALL":
                lines.extend(_high_low(observations, long_threshold))
                lines.extend(_buckets(observations, buckets, min_bucket_count, horizon))
                lines.extend(_within_day(observations, horizon))
        if cohort == "PUBLISHED":
            lines.extend(
                _published_book(
                    runs,
                    as_of,
                    horizon=primary_horizon,
                    top_n=top_n,
                    cost=round_trip_cost,
                    block_sessions=block_sessions,
                    calendar=calendar,
                )
            )
    lines.extend(_event_groups(runs, as_of, primary_horizon))
    return "\n".join(lines)


def _return_lines(observations: list[dict[str, object]]) -> list[str]:
    returns = [float(item["stock_return"]) for item in observations]
    excess = [float(item["excess_return"]) for item in observations]
    maes = [float(item["mae"]) for item in observations]
    mfes = [float(item["mfe"]) for item in observations]
    hits = sum(value > 0 for value in returns)
    return [
        f"hit_rate: {_fmt(hits / len(returns))}",
        f"avg_return: {_fmt(_mean(returns))}",
        f"median_return: {_fmt(_median(returns))}",
        f"avg_excess: {_fmt(_mean(excess))}",
        f"median_excess: {_fmt(_median(excess))}",
        f"win_loss: {_win_loss(returns)}",
        f"avg_mae: {_fmt(_mean(maes))}",
        f"worst_mae: {_fmt(min(maes))}",
        f"avg_mfe: {_fmt(_mean(mfes))}",
        f"best_mfe: {_fmt(max(mfes))}",
    ]


def _observations(
    runs: Sequence[tuple[dict[str, object], Sequence[object]]],
    horizon: int,
    as_of: datetime | None,
    *,
    cohort: str,
) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for snapshot, rows in runs:
        by_symbol = {
            row["symbol"]: row
            for row in rows
            if int(row["horizon"]) == horizon and _label_visible(row, as_of)
        }
        tradable = _tradable_symbols(snapshot)
        signal_date = str(snapshot.get("signal_date") or "undated")
        for ranked in _ranked(snapshot):
            if not _in_cohort(ranked, tradable, cohort):
                continue
            row = by_symbol.get(ranked["symbol"])
            if row is None:
                continue
            factors = ranked["factors"]
            if not isinstance(factors, dict):
                continue
            found.append(
                {
                    "symbol": ranked["symbol"],
                    "signal_date": signal_date,
                    "total_score": float(ranked["total_score"]),
                    "stock_return": float(row["stock_return"]),
                    "excess_return": float(row["excess_return"]),
                    "spy_return": float(row["spy_return"]) if _has(row, "spy_return") else 0.0,
                    "mae": float(row["mae"]),
                    "mfe": float(row["mfe"]),
                    "labels": _event_labels(factors.get("event")),
                    "factors": {
                        name: float(factors[name]["score"])
                        for name in _FACTORS
                        if isinstance(factors.get(name), dict)
                        and factors[name].get("score") is not None
                    },
                }
            )
    return found


def _in_cohort(ranked: dict[str, object], tradable: set[str], cohort: str) -> bool:
    symbol = str(ranked["symbol"])
    vetoed = bool(ranked.get("thesis_veto"))
    if cohort == "ALL":
        return True
    if cohort == "PUBLISHED":
        return symbol in tradable
    if cohort == "VETOED":
        return vetoed
    if cohort == "NOT_SELECTED":
        return symbol not in tradable
    raise ValueError(f"unknown cohort: {cohort}")


def _tradable_symbols(snapshot: dict[str, object]) -> set[str]:
    if snapshot.get("no_trade") is True:
        return set()
    rows = snapshot.get("symbols")
    if not isinstance(rows, list):
        return set()
    return {str(row["symbol"]) for row in rows if isinstance(row, dict) and row.get("symbol")}


def _label_visible(row: object, as_of: datetime | None) -> bool:
    if as_of is None:
        return True
    if not _has(row, "label_available_at"):
        return False
    stamp = row["label_available_at"]
    if stamp is None:
        return False
    if isinstance(stamp, str):
        stamp = datetime.fromisoformat(stamp)
    if not isinstance(stamp, datetime):
        return False
    return stamp <= as_of


def _has(row: object, key: str) -> bool:
    if isinstance(row, dict):
        return key in row
    keys = getattr(row, "keys", None)
    return keys is not None and key in keys()


def _event_labels(factor: object) -> tuple[str, ...]:
    if not isinstance(factor, dict):
        return ()
    classified = factor.get("classified_events")
    labels: list[str] = []
    if isinstance(classified, list):
        for item in classified:
            if isinstance(item, dict) and item.get("label") and float(item.get("signed") or 0) > 0:
                label = str(item["label"])
                if label not in labels:
                    labels.append(label)
        return tuple(labels)
    reasons = factor.get("reasons")
    if not isinstance(reasons, list):
        return ()
    for reason in reasons:
        text = str(reason)
        if text and text != _EMPTY and text not in labels:
            labels.append(text)
    return tuple(labels)


def _ranked(snapshot: dict[str, object]) -> list[dict[str, object]]:
    rows = snapshot.get("ranked")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _high_low(observations: list[dict[str, object]], threshold: float) -> list[str]:
    high = [
        item["excess_return"] for item in observations if float(item["total_score"]) >= threshold
    ]
    low = [item["excess_return"] for item in observations if float(item["total_score"]) < threshold]
    lines = ["", f"score >= {threshold:g} vs < {threshold:g}"]
    if not high or not low:
        lines.append("INSUFFICIENT SAMPLE")
        return lines
    high_mean = _mean([float(item) for item in high])
    low_mean = _mean([float(item) for item in low])
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
            if low <= float(item["total_score"]) < high
            or (last and float(item["total_score"]) == high)
        ]
        label = f"{low:g}-{high:g}"
        if len(chosen) < minimum:
            lines.append(f"{label}: INSUFFICIENT SAMPLE")
            continue
        excess = _mean([float(item["excess_return"]) for item in chosen])
        returns = [float(item["stock_return"]) for item in chosen]
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


def _within_day(observations: list[dict[str, object]], horizon: int) -> list[str]:
    lines = ["", f"factor attribution {horizon}D within_day"]
    by_day: dict[str, list[dict[str, object]]] = {}
    for item in observations:
        by_day.setdefault(str(item["signal_date"]), []).append(item)
    if len(observations) < 2:
        lines.append("INSUFFICIENT SAMPLE")
        return lines
    for name in _FACTORS:
        daily: list[float] = []
        undefined = False
        for rows in by_day.values():
            paired = [
                (float(item["factors"][name]), float(item["excess_return"]))
                for item in rows
                if name in item["factors"]
            ]
            if len(paired) < 2:
                continue
            correlation = _spearman([item[0] for item in paired], [item[1] for item in paired])
            if correlation is None:
                undefined = True
                continue
            daily.append(correlation)
        if not daily:
            text = "UNDEFINED" if undefined else "INSUFFICIENT SAMPLE"
            lines.append(f"{name}: days=0 mean_spearman={text}")
            continue
        lines.append(f"{name}: days={len(daily)} mean_spearman={_fmt(_mean(daily))}")
    return lines


def _published_book(
    runs: Sequence[tuple[dict[str, object], Sequence[object]]],
    as_of: datetime | None,
    *,
    horizon: int,
    top_n: int,
    cost: float,
    block_sessions: int,
    calendar: NYSECalendar | None,
) -> list[str]:
    observations = _observations(runs, horizon, as_of, cohort="PUBLISHED")
    lines = ["", f"published book {horizon}D"]
    by_day: dict[str, list[dict[str, object]]] = {}
    for item in observations:
        by_day.setdefault(str(item["signal_date"]), []).append(item)
    if not by_day:
        lines.append("days: 0")
        lines.append("INSUFFICIENT SAMPLE")
        return lines
    nets: list[float] = []
    excesses: list[float] = []
    known: list[tuple[object, float]] = []
    for signal_date, rows in sorted(by_day.items()):
        legs = [(float(row["stock_return"]), float(row["spy_return"])) for row in rows[:top_n]]
        net, excess = day_contribution(legs, top_n=top_n, horizon=horizon, cost=cost)
        nets.append(net)
        excesses.append(excess)
        day = _date_or_none(signal_date)
        if day is not None:
            known.append((day, net))
    blocks = contiguous_blocks([day for day, _net in known], block_sessions, calendar)
    lines.append(f"days: {len(nets)}")
    lines.append(f"avg_net: {_fmt(_mean(nets))}")
    lines.append(f"avg_excess: {_fmt(_mean(excesses))}")
    lines.append(f"worst_day_net: {_fmt(min(nets))}")
    lines.append(f"round_trip_cost: {_fmt(cost)}")
    if len(blocks) < 2:
        lines.append("blocks: INSUFFICIENT SAMPLE")
        return lines
    net_by_day = {day: net for day, net in known}
    block_means = [_mean([net_by_day[day] for day in block]) for block in blocks]
    lines.append(f"block_count: {len(blocks)}")
    lines.append(f"block_mean_net: {_fmt(_mean(block_means))}")
    lines.append(f"block_min_net: {_fmt(min(block_means))}")
    return lines


def _date_or_none(value: str):
    from datetime import date

    if value == "undated":
        return None
    return date.fromisoformat(value)


def _event_groups(
    runs: Sequence[tuple[dict[str, object], Sequence[object]]],
    as_of: datetime | None,
    horizon: int,
) -> list[str]:
    lines = ["", f"event groups {horizon}D"]
    observations = _observations(runs, horizon, as_of, cohort="ALL")
    grouped: dict[str, list[dict[str, object]]] = {}
    for item in observations:
        labels = item["labels"]
        if not isinstance(labels, tuple) or not labels:
            continue
        for label in labels:
            grouped.setdefault(str(label), []).append(item)
    if not grouped:
        lines.append("none")
        return lines
    for label in sorted(grouped):
        rows = grouped[label]
        excess = [float(item["excess_return"]) for item in rows]
        lines.append(f"{label}: count={len(rows)} avg_excess={_fmt(_mean(excess))}")
    published = _observations(runs, horizon, as_of, cohort="PUBLISHED")
    lines.append(f"event groups published {horizon}D")
    published_groups: dict[str, list[float]] = {}
    for item in published:
        labels = item["labels"]
        if not isinstance(labels, tuple):
            continue
        for label in labels:
            published_groups.setdefault(str(label), []).append(float(item["excess_return"]))
    if not published_groups:
        lines.append("none")
        return lines
    for label in sorted(published_groups):
        excess = published_groups[label]
        lines.append(f"{label}: count={len(excess)} avg_excess={_fmt(_mean(excess))}")
    return lines


def _win_loss(returns: list[float]) -> str:
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    if not losses:
        return "UNDEFINED"
    if not wins:
        return _fmt(0)
    return _fmt(_mean(wins) / abs(_mean(losses)))


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
