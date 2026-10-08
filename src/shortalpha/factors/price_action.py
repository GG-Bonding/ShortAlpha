"""Gap and breakout score. A larger gap does not raise the score once it is overheated."""

from datetime import date, datetime

from shortalpha.calendar import NYSECalendar
from shortalpha.config import PriceActionConfig
from shortalpha.domain import DailyBar, FactorResult, PremarketWindow, Split
from shortalpha.errors import DataUnavailableError
from shortalpha.factors.history import require_completed_bars
from shortalpha.factors.scale import clamp


def compute_price_action(
    symbol: str,
    bars: list[DailyBar],
    premarket: PremarketWindow,
    *,
    session: date,
    as_of: datetime,
    calendar: NYSECalendar,
    price_action: PriceActionConfig,
    weight: float,
    splits: tuple[Split, ...] = (),
) -> FactorResult:
    if premarket.last_price is None or premarket.available_at is None:
        reason = premarket.reason or "premarket price unavailable"
        return FactorResult(
            name="price_action",
            raw_value=None,
            normalized_value=0.5,
            score=weight * 0.5,
            available=True,
            degraded=True,
            reasons=(f"{reason}; price-action score is neutral",),
            details=(("degraded", "true"), ("price_consolidated", "false")),
        )
    if any(split.symbol == symbol and split.ex_date == session for split in splits):
        raise DataUnavailableError(
            symbol=symbol,
            provider="corporate_actions",
            operation="price_action",
            timestamp=as_of,
            reason="split is effective on the signal session; premarket is not comparable",
        )
    completed = require_completed_bars(
        symbol,
        bars,
        session=session,
        as_of=as_of,
        calendar=calendar,
        count=20,
        operation="price_action",
    )
    previous = completed[-1]
    gap = premarket.last_price / previous.close - 1
    gap_points = _gap_points(gap, price_action)
    break_points = 0.0
    reasons: list[str] = []
    if premarket.last_price > previous.high:
        break_points += price_action.break_bonus
        reasons.append("Broke previous high")
    high_20 = max(bar.high for bar in completed)
    if premarket.last_price > high_20:
        break_points += price_action.break_bonus
        reasons.append("Broke 20-day high")
    if 0 < gap <= price_action.gap_penalty_start:
        reasons.append("Premarket gap within range")
    structural = gap_points + break_points
    penalty = _gap_penalty(gap, price_action)
    score = structural * penalty
    risks: list[str] = []
    if not premarket.price_consolidated:
        reasons.append("Same-day price is not the consolidated tape")
    if gap > price_action.gap_penalty_start:
        risks.append(f"Premarket gap already {gap * 100:+.1f}%")
    atr_pct = _atr(completed, price_action.atr_lookback) / previous.close
    if atr_pct > price_action.atr_elevated:
        score *= price_action.elevated_atr_scale
        risks.append("ATR elevated")
    score = clamp(score, 0.0, weight)
    return FactorResult(
        name="price_action",
        raw_value=gap,
        normalized_value=score / weight,
        score=score,
        available=True,
        reasons=tuple(reasons),
        risks=tuple(risks),
        details=(
            ("gap", f"{gap:.10f}"),
            ("penalty", f"{penalty:.10f}"),
            ("atr_pct", f"{atr_pct:.10f}"),
            ("degraded", "false"),
            ("price_time", premarket.available_at.isoformat()),
            ("prior_close_time", previous.available_at.isoformat()),
            ("last_price", f"{premarket.last_price:.10f}"),
            ("price_consolidated", "true" if premarket.price_consolidated else "false"),
        ),
    )


def _gap_points(gap: float, cfg: PriceActionConfig) -> float:
    if gap <= 0:
        return 0.0
    if gap <= cfg.gap_penalty_start:
        return cfg.gap_component_max * (gap / cfg.gap_penalty_start)
    return cfg.gap_component_max


def _gap_penalty(gap: float, cfg: PriceActionConfig) -> float:
    if gap <= cfg.gap_penalty_start:
        return 1.0
    if gap >= cfg.gap_hard_penalty:
        return cfg.gap_penalty_floor
    span = cfg.gap_hard_penalty - cfg.gap_penalty_start
    progress = (gap - cfg.gap_penalty_start) / span
    return 1.0 - (1.0 - cfg.gap_penalty_floor) * progress


def _atr(bars: list[DailyBar], lookback: int) -> float:
    true_ranges: list[float] = []
    for previous, bar in zip(bars, bars[1:], strict=False):
        true_ranges.append(
            max(
                bar.high - bar.low,
                abs(bar.high - previous.close),
                abs(bar.low - previous.close),
            )
        )
    window = true_ranges[-lookback:]
    return sum(window) / len(window)
