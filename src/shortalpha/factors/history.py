"""Completed sessions only. The signal session itself is not an input."""

from datetime import date, datetime

from shortalpha.calendar import NYSECalendar
from shortalpha.domain import DailyBar
from shortalpha.errors import DataUnavailableError


def require_completed_bars(
    symbol: str,
    bars: list[DailyBar],
    *,
    session: date,
    as_of: datetime,
    calendar: NYSECalendar,
    count: int,
    operation: str,
) -> list[DailyBar]:
    if not calendar.is_trading_day(session):
        raise ValueError(f"{session.isoformat()} is not a trading session")
    needed = [calendar.shift(session, -offset) for offset in range(count, 0, -1)]
    by_day: dict[date, DailyBar] = {}
    for bar in bars:
        if bar.symbol != symbol or bar.session_date >= session or bar.available_at > as_of:
            continue
        previous = by_day.get(bar.session_date)
        if previous is not None and (previous.close != bar.close or previous.volume != bar.volume):
            raise ValueError(
                f"conflicting daily bars for {symbol} on {bar.session_date.isoformat()}"
            )
        by_day[bar.session_date] = bar
    found = [by_day[day] for day in needed if day in by_day]
    if len(found) < count:
        raise DataUnavailableError(
            symbol=symbol,
            provider="market",
            operation=operation,
            timestamp=as_of,
            reason=f"insufficient history: {len(found)} < {count}",
        )
    return [by_day[day] for day in needed]
