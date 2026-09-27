"""Volume ratio. Premarket is used only when the feed actually observed it."""

from datetime import date, datetime

from shortalpha.calendar import NYSECalendar
from shortalpha.config import VolumeConfig
from shortalpha.domain import DailyBar, FactorResult
from shortalpha.errors import DataUnavailableError
from shortalpha.factors.history import require_completed_bars
from shortalpha.factors.scale import scale_to_weight
from shortalpha.providers.base import PreMarketDataProvider


def compute_volume(
    symbol: str,
    bars: list[DailyBar],
    premarket: PreMarketDataProvider,
    *,
    session: date,
    as_of: datetime,
    calendar: NYSECalendar,
    volume: VolumeConfig,
    weight: float,
) -> FactorResult:
    ratio, premarket_available, fallback_reason = _premarket_ratio(
        symbol,
        premarket,
        session=session,
        as_of=as_of,
        calendar=calendar,
        lookback=volume.lookback,
    )
    if ratio is None:
        ratio = _daily_ratio(
            symbol,
            bars,
            session=session,
            as_of=as_of,
            calendar=calendar,
            lookback=volume.lookback,
        )
    score = scale_to_weight(ratio, volume.ratio_low, volume.ratio_high, weight)
    if premarket_available:
        reason = f"Premarket volume {ratio:.1f}x"
    else:
        reason = f"Volume {ratio:.1f}x"
    return FactorResult(
        name="volume",
        raw_value=ratio,
        normalized_value=score / weight,
        score=score,
        available=True,
        degraded=not premarket_available,
        reasons=(reason,),
        details=(
            ("premarket_volume_available", "true" if premarket_available else "false"),
            ("ratio", f"{ratio:.10f}"),
            ("fallback_reason", fallback_reason),
        ),
    )


def _premarket_ratio(
    symbol: str,
    premarket: PreMarketDataProvider,
    *,
    session: date,
    as_of: datetime,
    calendar: NYSECalendar,
    lookback: int,
) -> tuple[float | None, bool, str]:
    today = premarket.window(symbol, session, as_of)
    if not today.available or today.volume is None:
        return None, False, today.reason or "premarket volume unavailable"
    history: list[float] = []
    for offset in range(lookback, 0, -1):
        day = calendar.shift(session, -offset)
        window = premarket.window(symbol, day, as_of)
        if not window.available or window.volume is None:
            reason = window.reason or "historical premarket window unavailable"
            return None, False, reason
        history.append(window.volume)
    baseline = sum(history) / lookback
    if baseline == 0:
        return None, False, "premarket baseline is zero"
    return today.volume / baseline, True, ""


def _daily_ratio(
    symbol: str,
    bars: list[DailyBar],
    *,
    session: date,
    as_of: datetime,
    calendar: NYSECalendar,
    lookback: int,
) -> float:
    completed = require_completed_bars(
        symbol,
        bars,
        session=session,
        as_of=as_of,
        calendar=calendar,
        count=lookback + 1,
        operation="volume",
    )
    baseline = sum(bar.volume for bar in completed[:-1]) / lookback
    if baseline == 0:
        raise DataUnavailableError(
            symbol=symbol,
            provider="market",
            operation="volume",
            timestamp=as_of,
            reason="average volume is zero",
        )
    return completed[-1].volume / baseline
