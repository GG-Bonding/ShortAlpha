from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from shortalpha.calendar import NYSECalendar
from shortalpha.domain import DailyBar, PremarketWindow

NY = ZoneInfo("America/New_York")
SESSION = date(2024, 6, 20)


def signal_time() -> datetime:
    return datetime.combine(SESSION, time(9, 0), tzinfo=NY)


def prior_sessions(calendar: NYSECalendar, count: int) -> list[date]:
    return [calendar.shift(SESSION, -offset) for offset in range(count, 0, -1)]


def bar(
    symbol: str,
    day: date,
    close: float,
    *,
    high: float | None = None,
    low: float | None = None,
    volume: float = 1_000_000,
) -> DailyBar:
    high_price = close if high is None else high
    low_price = close if low is None else low
    stamp = datetime.combine(day, time(16, 0), tzinfo=NY)
    return DailyBar(
        symbol=symbol,
        session_date=day,
        open=close,
        high=high_price,
        low=low_price,
        close=close,
        volume=volume,
        event_time=stamp,
        published_at=stamp,
        available_at=stamp,
        source="fixture",
    )


def closes(symbol: str, days: list[date], prices: list[float]) -> list[DailyBar]:
    return [bar(symbol, day, price) for day, price in zip(days, prices, strict=True)]


def premarket(
    symbol: str, price: float, *, available: bool = True, reason: str = ""
) -> PremarketWindow:
    stamp = signal_time()
    return PremarketWindow(
        symbol=symbol,
        session_date=SESSION,
        available=available,
        volume=1000 if available else None,
        last_price=price if available else None,
        high=price if available else None,
        low=price if available else None,
        event_time=stamp if available else None,
        published_at=stamp if available else None,
        available_at=stamp if available else None,
        reason=reason,
    )
