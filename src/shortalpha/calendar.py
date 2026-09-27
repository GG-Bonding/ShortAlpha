"""NYSE sessions. Shifts count trading days, not calendar days."""

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from shortalpha.config import AppConfig


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    shift = (weekday - first.weekday()) % 7
    return first + timedelta(days=shift + 7 * (n - 1))


def _last_monday(year: int, month: int) -> date:
    if month == 12:
        cursor = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        cursor = date(year, month + 1, 1) - timedelta(days=1)
    return cursor - timedelta(days=cursor.weekday())


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm. Good Friday is two days earlier."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


@lru_cache
def _statutory_holidays(year: int) -> frozenset[date]:
    holidays: set[date] = set()

    def add(day: date) -> None:
        observed = _observed(day)
        if observed.year == year:
            holidays.add(observed)

    add(date(year, 1, 1))
    add(date(year + 1, 1, 1))
    add(_nth_weekday(year, 1, 0, 3))
    add(_nth_weekday(year, 2, 0, 3))
    holidays.add(_easter(year) - timedelta(days=2))
    add(_last_monday(year, 5))
    if year >= 2022:
        add(date(year, 6, 19))
    add(date(year, 7, 4))
    add(_nth_weekday(year, 9, 0, 1))
    holidays.add(_nth_weekday(year, 11, 3, 4))
    add(date(year, 12, 25))
    return frozenset(holidays)


@lru_cache
def _statutory_early_closes(year: int) -> frozenset[date]:
    holidays = _statutory_holidays(year)
    days = {_nth_weekday(year, 11, 3, 4) + timedelta(days=1)}
    for candidate in (date(year, 7, 3), date(year, 12, 24)):
        if candidate.weekday() < 5 and candidate not in holidays:
            days.add(candidate)
    return frozenset(days)


class NYSECalendar:
    def __init__(
        self,
        extra_holidays: frozenset[date] | set[date] | None = None,
        extra_early_closes: frozenset[date] | set[date] | None = None,
    ) -> None:
        self.extra_holidays = frozenset(extra_holidays or ())
        self.extra_early_closes = frozenset(extra_early_closes or ())

    def is_trading_day(self, day: date) -> bool:
        if day.weekday() >= 5:
            return False
        if day in self.extra_holidays:
            return False
        return day not in _statutory_holidays(day.year)

    def is_early_close(self, day: date) -> bool:
        if not self.is_trading_day(day):
            return False
        if day in self.extra_early_closes:
            return True
        return day in _statutory_early_closes(day.year)

    def session_close(self, day: date) -> datetime:
        if not self.is_trading_day(day):
            raise ValueError(f"{day.isoformat()} is not a trading session")
        clock = time(13, 0) if self.is_early_close(day) else time(16, 0)
        return datetime.combine(day, clock, tzinfo=ZoneInfo("America/New_York"))

    def signal_time(self, day: date, clock: time, tz: ZoneInfo) -> datetime:
        if not self.is_trading_day(day):
            raise ValueError(f"{day.isoformat()} is not a trading session")
        return datetime.combine(day, clock, tzinfo=tz)

    def shift(self, day: date, sessions: int) -> date:
        if sessions == 0:
            if not self.is_trading_day(day):
                raise ValueError(f"{day.isoformat()} is not a trading session")
            return day
        step = 1 if sessions > 0 else -1
        remaining = abs(sessions)
        cursor = day
        searched = 0
        while remaining:
            cursor += timedelta(days=step)
            searched += 1
            if searched > 366 * 3:
                raise RuntimeError(f"no trading session within range of {day.isoformat()}")
            if self.is_trading_day(cursor):
                remaining -= 1
        return cursor

    def sessions_in_range(self, start: date, end: date) -> list[date]:
        if end < start:
            raise ValueError("end is before start")
        days: list[date] = []
        cursor = start
        while cursor <= end:
            if self.is_trading_day(cursor):
                days.append(cursor)
            cursor += timedelta(days=1)
        return days


def calendar_from_config(cfg: AppConfig) -> NYSECalendar:
    return NYSECalendar(
        extra_holidays=set(cfg.calendar.extra_holidays),
        extra_early_closes=set(cfg.calendar.extra_early_closes),
    )
