from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from shortalpha.calendar import NYSECalendar

NY = ZoneInfo("America/New_York")

CLOSED = [
    date(2021, 12, 31),
    date(2022, 6, 20),
    date(2023, 1, 2),
    date(2024, 1, 1),
    date(2024, 1, 15),
    date(2024, 2, 19),
    date(2024, 3, 29),
    date(2024, 5, 27),
    date(2024, 6, 19),
    date(2024, 7, 4),
    date(2024, 9, 2),
    date(2024, 11, 28),
    date(2024, 12, 25),
    date(2025, 1, 1),
    date(2025, 1, 20),
    date(2025, 2, 17),
    date(2025, 4, 18),
    date(2025, 5, 26),
    date(2025, 6, 19),
    date(2025, 7, 4),
    date(2025, 9, 1),
    date(2025, 11, 27),
    date(2025, 12, 25),
    date(2026, 1, 1),
    date(2026, 1, 19),
    date(2026, 2, 16),
    date(2026, 4, 3),
    date(2026, 5, 25),
    date(2026, 6, 19),
    date(2026, 7, 3),
    date(2026, 9, 7),
    date(2026, 11, 26),
    date(2026, 12, 25),
]

EARLY = [
    date(2024, 7, 3),
    date(2024, 11, 29),
    date(2024, 12, 24),
    date(2025, 7, 3),
    date(2025, 11, 28),
    date(2025, 12, 24),
    date(2026, 11, 27),
    date(2026, 12, 24),
]


@pytest.fixture
def calendar() -> NYSECalendar:
    return NYSECalendar()


@pytest.mark.parametrize("day", CLOSED)
def test_nyse_holidays_and_weekend_observance(calendar: NYSECalendar, day: date) -> None:
    assert calendar.is_trading_day(day) is False


def test_weekend_is_not_a_trading_day(calendar: NYSECalendar) -> None:
    assert calendar.is_trading_day(date(2024, 6, 15)) is False
    assert calendar.is_trading_day(date(2024, 6, 16)) is False


def test_juneteenth_was_not_observed_in_2021(calendar: NYSECalendar) -> None:
    assert calendar.is_trading_day(date(2021, 6, 18)) is True


@pytest.mark.parametrize("day", EARLY)
def test_early_closes(calendar: NYSECalendar, day: date) -> None:
    assert calendar.is_trading_day(day) is True
    assert calendar.is_early_close(day) is True
    assert calendar.session_close(day) == datetime.combine(day, time(13, 0), tzinfo=NY)


def test_observed_holiday_is_not_an_early_close(calendar: NYSECalendar) -> None:
    assert calendar.is_trading_day(date(2026, 7, 3)) is False
    assert calendar.is_early_close(date(2026, 7, 3)) is False
    assert calendar.is_early_close(date(2021, 12, 24)) is False


def test_regular_session_closes_at_16(calendar: NYSECalendar) -> None:
    day = date(2024, 1, 2)
    assert calendar.is_trading_day(day) is True
    assert calendar.is_early_close(day) is False
    assert calendar.session_close(day) == datetime(2024, 1, 2, 16, 0, tzinfo=NY)


def test_shift_skips_weekends_and_holidays(calendar: NYSECalendar) -> None:
    assert calendar.shift(date(2024, 7, 3), 1) == date(2024, 7, 5)
    assert calendar.shift(date(2024, 7, 5), 1) == date(2024, 7, 8)
    assert calendar.shift(date(2024, 7, 8), -1) == date(2024, 7, 5)
    assert calendar.shift(date(2024, 7, 5), -1) == date(2024, 7, 3)
    assert calendar.shift(date(2024, 12, 20), 3) == date(2024, 12, 26)
    assert calendar.shift(date(2024, 5, 24), 1) == date(2024, 5, 28)


def test_zero_shift_rejects_a_closed_day(calendar: NYSECalendar) -> None:
    with pytest.raises(ValueError, match="trading"):
        calendar.shift(date(2024, 7, 6), 0)


def test_sessions_in_range_skip_holiday_and_weekend(calendar: NYSECalendar) -> None:
    assert calendar.sessions_in_range(date(2024, 7, 3), date(2024, 7, 8)) == [
        date(2024, 7, 3),
        date(2024, 7, 5),
        date(2024, 7, 8),
    ]


def test_signal_time_follows_new_york_dst(calendar: NYSECalendar) -> None:
    winter = calendar.signal_time(date(2024, 1, 2), time(9, 0), NY)
    summer = calendar.signal_time(date(2024, 7, 5), time(9, 0), NY)
    assert winter == datetime(2024, 1, 2, 9, 0, tzinfo=NY)
    assert winter.utcoffset() is not None
    assert winter.isoformat().endswith("-05:00")
    assert summer.isoformat().endswith("-04:00")


def test_signal_time_rejects_holiday(calendar: NYSECalendar) -> None:
    with pytest.raises(ValueError, match="trading"):
        calendar.signal_time(date(2024, 7, 4), time(9, 0), NY)


def test_extra_closure_and_early_close() -> None:
    calendar = NYSECalendar(
        extra_holidays=frozenset({date(2024, 7, 5)}),
        extra_early_closes=frozenset({date(2024, 7, 2)}),
    )
    assert calendar.is_trading_day(date(2024, 7, 5)) is False
    assert calendar.is_early_close(date(2024, 7, 2)) is True
    assert calendar.session_close(date(2024, 7, 2)).hour == 13
