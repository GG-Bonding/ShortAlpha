"""Promotion gates for the published book. Failure keeps the current strategy."""

import math
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


@dataclass(frozen=True)
class PromotionLimits:
    """Sample floors declared before a candidate is scored."""

    min_mature_dates: int
    min_effective_trades: int
    min_complete_blocks: int

    def __post_init__(self) -> None:
        if self.min_complete_blocks < 2:
            raise ValueError("min_complete_blocks must be at least 2")
        if self.min_mature_dates < 2:
            raise ValueError("min_mature_dates must be at least 2")
        if self.min_effective_trades < 1:
            raise ValueError("min_effective_trades must be positive")


def sample_gaps(
    points: tuple[DayPoint, ...],
    *,
    limits: PromotionLimits,
    block_sessions: int,
    calendar: NYSECalendar | None,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if len(points) < limits.min_mature_dates:
        reasons.append(f"fewer than {limits.min_mature_dates} mature dates")
    trades = sum(len(point.candidate) for point in points)
    if trades < limits.min_effective_trades:
        reasons.append(f"fewer than {limits.min_effective_trades} effective trades")
    if len(_complete_blocks(points, block_sessions, calendar)) < limits.min_complete_blocks:
        reasons.append(f"fewer than {limits.min_complete_blocks} complete blocks")
    return tuple(reasons)


def uncertainty_reason(report: dict[str, object]) -> str | None:
    if report.get("blocks") == "INSUFFICIENT SAMPLE":
        return "improvement uncertainty is not estimable"
    low_net = report.get("improvement_low_net")
    low_excess = report.get("improvement_low_excess")
    if not isinstance(low_net, int | float) or not isinstance(low_excess, int | float):
        return "improvement uncertainty is not estimable"
    if low_net <= 0 or low_excess <= 0:
        return "improvement is inside its uncertainty"
    return None


def uncertainty(
    points: tuple[DayPoint, ...],
    *,
    top_n: int,
    horizon: int,
    cost: float,
    block_sessions: int,
    calendar: NYSECalendar | None,
) -> dict[str, object]:
    counts = {
        "mature_dates": len(points),
        "effective_trades": sum(len(point.candidate) for point in points),
    }
    if len(points) < 2:
        return {
            **counts,
            "block_count": 0,
            "complete_block_count": 0,
            "blocks": "INSUFFICIENT SAMPLE",
        }
    baseline_net = _series(points, "baseline", top_n=top_n, horizon=horizon, cost=cost)
    candidate_net = _series(points, "candidate", top_n=top_n, horizon=horizon, cost=cost)
    baseline_excess = _series(
        points, "baseline", top_n=top_n, horizon=horizon, cost=cost, excess=True
    )
    candidate_excess = _series(
        points, "candidate", top_n=top_n, horizon=horizon, cost=cost, excess=True
    )
    raw_blocks = contiguous_blocks(list(baseline_net), block_sessions, calendar)
    complete = [block for block in raw_blocks if len(block) == block_sessions]
    if len(complete) < 2:
        return {
            **counts,
            "block_count": len(raw_blocks),
            "complete_block_count": len(complete),
            "blocks": "INSUFFICIENT SAMPLE",
        }
    baseline_means = [_mean([baseline_net[day] for day in block]) for block in complete]
    candidate_means = [_mean([candidate_net[day] for day in block]) for block in complete]
    net_mean, net_stderr, net_low = _spread(
        [_block_delta(candidate_net, baseline_net, block) for block in complete]
    )
    excess_mean, excess_stderr, excess_low = _spread(
        [_block_delta(candidate_excess, baseline_excess, block) for block in complete]
    )
    return {
        **counts,
        "block_count": len(complete),
        "complete_block_count": len(complete),
        "baseline_block_min_net": min(baseline_means),
        "candidate_block_min_net": min(candidate_means),
        "baseline_block_mean_net": _mean(baseline_means),
        "candidate_block_mean_net": _mean(candidate_means),
        "improvement_mean_net": net_mean,
        "improvement_stderr_net": net_stderr,
        "improvement_low_net": net_low,
        "improvement_mean_excess": excess_mean,
        "improvement_stderr_excess": excess_stderr,
        "improvement_low_excess": excess_low,
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


def _complete_blocks(
    points: tuple[DayPoint, ...],
    block_sessions: int,
    calendar: NYSECalendar | None,
) -> list[list[date]]:
    days = [point.signal_date for point in points]
    return [
        block
        for block in contiguous_blocks(days, block_sessions, calendar)
        if len(block) == block_sessions
    ]


def _block_delta(
    candidate: dict[date, float], baseline: dict[date, float], block: list[date]
) -> float:
    return _mean([candidate[day] for day in block]) - _mean([baseline[day] for day in block])


def _spread(values: list[float]) -> tuple[float, float, float]:
    """Mean, standard error, and mean minus one standard error.

    The interval is declared before any result is seen.
    """
    mean = _mean(values)
    if len(values) < 2:
        return mean, 0.0, mean
    variance = sum((item - mean) ** 2 for item in values) / (len(values) - 1)
    stderr = math.sqrt(variance / len(values))
    return mean, stderr, mean - stderr


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)
