"""Trailing completed-session momentum. Premarket prices are not an input."""

from datetime import date, datetime

from shortalpha.calendar import NYSECalendar
from shortalpha.config import MomentumConfig
from shortalpha.domain import DailyBar, FactorResult, Split
from shortalpha.factors.adjust import adjusted_close
from shortalpha.factors.history import require_completed_bars
from shortalpha.factors.scale import clamp, scale_to_weight


def compute_momentum(
    symbol: str,
    bars: list[DailyBar],
    *,
    session: date,
    as_of: datetime,
    calendar: NYSECalendar,
    momentum: MomentumConfig,
    weight: float,
    splits: tuple[Split, ...] = (),
) -> FactorResult:
    completed = require_completed_bars(
        symbol,
        bars,
        session=session,
        as_of=as_of,
        calendar=calendar,
        count=6,
        operation="momentum",
    )
    closes = [
        adjusted_close(bar.close, bar.session_date, session, splits, symbol) for bar in completed
    ]
    r1 = closes[-1] / closes[-2] - 1
    r3 = closes[-1] / closes[-4] - 1
    r5 = closes[-1] / closes[-6] - 1
    raw = momentum.w1 * r1 + momentum.w3 * r3 + momentum.w5 * r5
    score = scale_to_weight(raw, momentum.raw_low, momentum.raw_high, weight)
    overheated = r5 > momentum.overheat_r5
    if overheated:
        score *= momentum.overheat_multiplier
    score = clamp(score, 0.0, weight)
    risks = ("R5 above 25%; momentum score halved",) if overheated else ()
    return FactorResult(
        name="momentum",
        raw_value=raw,
        normalized_value=score / weight,
        score=score,
        available=True,
        reasons=() if overheated or raw <= 0 else (f"R5 {r5 * 100:+.1f}%",),
        risks=risks,
        details=(
            ("r1", f"{r1:.10f}"),
            ("r3", f"{r3:.10f}"),
            ("r5", f"{r5:.10f}"),
            ("overheated", "true" if overheated else "false"),
        ),
    )
