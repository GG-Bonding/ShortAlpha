from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from shortalpha.calendar import NYSECalendar
from shortalpha.domain import DailyBar
from shortalpha.universe import LiquidityStatus, evaluate_liquidity

NY = ZoneInfo("America/New_York")
SESSION = date(2024, 6, 20)


def _bar(
    day: date,
    *,
    close: float = 10,
    volume: float = 5_000_000,
    available_at: datetime | None = None,
) -> DailyBar:
    stamp = available_at or datetime.combine(day, time(16, 0), tzinfo=NY)
    return DailyBar(
        symbol="AAA",
        session_date=day,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        event_time=stamp,
        published_at=stamp,
        available_at=stamp,
        source="fixture",
    )


def _history(calendar: NYSECalendar, sessions: int = 20) -> list[DailyBar]:
    days = [calendar.shift(SESSION, -offset) for offset in range(sessions, 0, -1)]
    return [_bar(day) for day in days]


def test_twenty_completed_sessions_pass_and_ignore_the_signal_day() -> None:
    calendar = NYSECalendar()
    bars = _history(calendar)
    bars.append(_bar(SESSION, close=1, volume=1))
    as_of = datetime.combine(SESSION, time(9, 0), tzinfo=NY)
    result = evaluate_liquidity(
        "AAA",
        bars,
        session=SESSION,
        as_of=as_of,
        calendar=calendar,
        min_price=5,
        min_avg_dollar_volume=50_000_000,
    )
    assert result.status is LiquidityStatus.ELIGIBLE
    assert result.price == 10
    assert result.avg_dollar_volume == 50_000_000
    assert result.price_source == "previous_close"
    assert date(2024, 6, 19) not in {bar.session_date for bar in bars if bar.close == 10}


def test_same_day_bar_does_not_fill_a_missing_session() -> None:
    calendar = NYSECalendar()
    bars = _history(calendar, 19)
    bars.append(_bar(SESSION, close=10, volume=5_000_000))
    result = evaluate_liquidity(
        "AAA",
        bars,
        session=SESSION,
        as_of=datetime.combine(SESSION, time(9, 0), tzinfo=NY),
        calendar=calendar,
        min_price=5,
        min_avg_dollar_volume=50_000_000,
    )
    assert result.status is LiquidityStatus.MISSING
    assert result.reason == "insufficient history: 19 < 20"


def test_bar_available_after_the_signal_is_ignored() -> None:
    calendar = NYSECalendar()
    bars = _history(calendar)
    latest = calendar.shift(SESSION, -1)
    late = datetime.combine(SESSION, time(16, 0), tzinfo=NY)
    bars = [_bar(latest, available_at=late) if bar.session_date == latest else bar for bar in bars]
    result = evaluate_liquidity(
        "AAA",
        bars,
        session=SESSION,
        as_of=datetime.combine(SESSION, time(9, 0), tzinfo=NY),
        calendar=calendar,
        min_price=5,
        min_avg_dollar_volume=50_000_000,
    )
    assert result.status is LiquidityStatus.MISSING
    assert "19 < 20" in result.reason


def test_price_and_dollar_volume_thresholds() -> None:
    calendar = NYSECalendar()
    as_of = datetime.combine(SESSION, time(9, 0), tzinfo=NY)
    cheap = [_bar(bar.session_date, close=4, volume=20_000_000) for bar in _history(calendar)]
    price_fail = evaluate_liquidity(
        "AAA",
        cheap,
        session=SESSION,
        as_of=as_of,
        calendar=calendar,
        min_price=5,
        min_avg_dollar_volume=50_000_000,
    )
    assert price_fail.status is LiquidityStatus.EXCLUDED
    assert price_fail.reason == "price 4 < 5"

    thin = [_bar(bar.session_date, close=10, volume=100) for bar in _history(calendar)]
    dollar_fail = evaluate_liquidity(
        "AAA",
        thin,
        session=SESSION,
        as_of=as_of,
        calendar=calendar,
        min_price=5,
        min_avg_dollar_volume=50_000_000,
    )
    assert dollar_fail.status is LiquidityStatus.EXCLUDED
    assert "avg_dollar_volume" in dollar_fail.reason


def test_premarket_price_is_the_filter_price_when_supplied() -> None:
    calendar = NYSECalendar()
    result = evaluate_liquidity(
        "AAA",
        _history(calendar),
        session=SESSION,
        as_of=datetime.combine(SESSION, time(9, 0), tzinfo=NY),
        calendar=calendar,
        min_price=5,
        min_avg_dollar_volume=50_000_000,
        premarket_price=4,
    )
    assert result.status is LiquidityStatus.EXCLUDED
    assert result.price == 4
    assert result.price_source == "premarket"


def test_closed_session_is_rejected() -> None:
    with pytest.raises(ValueError, match="trading"):
        evaluate_liquidity(
            "AAA",
            [],
            session=date(2024, 6, 15),
            as_of=datetime(2024, 6, 15, 9, 0, tzinfo=NY),
            calendar=NYSECalendar(),
            min_price=5,
            min_avg_dollar_volume=50_000_000,
        )
