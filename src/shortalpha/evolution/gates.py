"""Promotion gates for the published book. Failure keeps the current strategy."""

from dataclasses import dataclass
from datetime import date

from shortalpha.calendar import NYSECalendar
from shortalpha.evaluation.book import contiguous_blocks, day_contribution, slot_weight


@dataclass(frozen=True)
class Leg:
    symbol: str
    stock_return: float
    spy_return: float


@dataclass(frozen=True)
class DayPoint:
    signal_date: date
    baseline: tuple[Leg, ...]
    candidate: tuple[Leg, ...]
    baseline_missing_ratio: float
    candidate_missing_ratio: float


def uncertainty(
    points: tuple[DayPoint, ...],
    *,
    top_n: int,
    horizon: int,
    cost: float,
    block_sessions: int,
    calendar: NYSECalendar | None,
) -> dict[str, object]:
    if len(points) < 2:
        return {"block_count": 0, "blocks": "INSUFFICIENT SAMPLE"}
    baseline = _series(points, "baseline", top_n=top_n, horizon=horizon, cost=cost)
    candidate = _series(points, "candidate", top_n=top_n, horizon=horizon, cost=cost)
    blocks = contiguous_blocks(list(baseline), block_sessions, calendar)
    if len(blocks) < 2:
        return {"block_count": len(blocks), "blocks": "INSUFFICIENT SAMPLE"}
    baseline_means = [_mean([baseline[day] for day in block]) for block in blocks]
    candidate_means = [_mean([candidate[day] for day in block]) for block in blocks]
    return {
        "block_count": len(blocks),
        "baseline_block_min_net": min(baseline_means),
        "candidate_block_min_net": min(candidate_means),
        "baseline_block_mean_net": _mean(baseline_means),
        "candidate_block_mean_net": _mean(candidate_means),
    }


def failure_reasons(
    points: tuple[DayPoint, ...],
    *,
    top_n: int,
    horizon: int,
    cost: float,
    block_sessions: int,
    calendar: NYSECalendar | None,
) -> tuple[str, ...]:
    if len(points) < 2:
        return ("fewer than two mature dates",)
    reasons: list[str] = []
    baseline_net = _series(points, "baseline", top_n=top_n, horizon=horizon, cost=cost)
    candidate_net = _series(points, "candidate", top_n=top_n, horizon=horizon, cost=cost)
    baseline_excess = _series(
        points, "baseline", top_n=top_n, horizon=horizon, cost=cost, excess=True
    )
    candidate_excess = _series(
        points, "candidate", top_n=top_n, horizon=horizon, cost=cost, excess=True
    )
    if sum(candidate_net.values()) <= sum(baseline_net.values()):
        reasons.append("net return did not improve")
    if sum(candidate_excess.values()) <= sum(baseline_excess.values()):
        reasons.append("excess return did not improve")
    better_days = sum(candidate_excess[day] > baseline_excess[day] for day in candidate_excess)
    if better_days < 2:
        reasons.append("improvement is not spread across dates")
    if _one_symbol(points, top_n=top_n, horizon=horizon, cost=cost):
        reasons.append("one symbol accounts for the improvement")
    if _coverage(points, "candidate") < _coverage(points, "baseline"):
        reasons.append("coverage worsened")
    if _worst(candidate_net, block_sessions, calendar) < _worst(
        baseline_net, block_sessions, calendar
    ):
        reasons.append("worst performance worsened")
    if _mean([point.candidate_missing_ratio for point in points]) > _mean(
        [point.baseline_missing_ratio for point in points]
    ):
        reasons.append("missing data worsened")
    return tuple(reasons)


def _series(
    points: tuple[DayPoint, ...],
    side: str,
    *,
    top_n: int,
    horizon: int,
    cost: float,
    excess: bool = False,
) -> dict[date, float]:
    values: dict[date, float] = {}
    for point in points:
        legs = getattr(point, side)
        pairs = [(leg.stock_return, leg.spy_return) for leg in legs]
        net, extra = day_contribution(pairs, top_n=top_n, horizon=horizon, cost=cost)
        values[point.signal_date] = extra if excess else net
    return values


def _one_symbol(
    points: tuple[DayPoint, ...],
    *,
    top_n: int,
    horizon: int,
    cost: float,
) -> bool:
    weight = slot_weight(top_n, horizon)
    advantage: dict[str, float] = {}
    for point in points:
        for symbol, extra in _weighted(point.candidate, weight, cost).items():
            advantage[symbol] = advantage.get(symbol, 0.0) + extra
        for symbol, extra in _weighted(point.baseline, weight, cost).items():
            advantage[symbol] = advantage.get(symbol, 0.0) - extra
    total = sum(advantage.values())
    if total <= 0:
        return False
    return max(advantage.values()) > 0.5 * total + 1e-9


def _weighted(legs: tuple[Leg, ...], weight: float, cost: float) -> dict[str, float]:
    values: dict[str, float] = {}
    for leg in legs:
        extra = (leg.stock_return - cost - leg.spy_return) * weight
        values[leg.symbol] = values.get(leg.symbol, 0.0) + extra
    return values


def _coverage(points: tuple[DayPoint, ...], side: str) -> float:
    traded = sum(1 for point in points if getattr(point, side))
    return traded / len(points)


def _worst(
    values: dict[date, float],
    block_sessions: int,
    calendar: NYSECalendar | None,
) -> float:
    blocks = contiguous_blocks(list(values), block_sessions, calendar)
    if len(blocks) >= 2:
        return min(_mean([values[day] for day in block]) for block in blocks)
    return min(values.values())


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)
