from datetime import date, datetime, time

import pytest

from shortalpha.calendar import NYSECalendar
from shortalpha.domain import DailyBar, SignalRun, Split
from shortalpha.evaluation.forward import compute_forward_returns
from shortalpha.storage import Store
from tests.factor_setup import NY

SESSION = date(2024, 6, 17)


def _bar(
    symbol: str,
    day: date,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
) -> DailyBar:
    stamp = datetime.combine(day, time(16, 0), tzinfo=NY)
    return DailyBar(
        symbol=symbol,
        session_date=day,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1_000_000,
        event_time=stamp,
        published_at=stamp,
        available_at=stamp,
        source="fixture",
    )


def _pair(day: date, open_: float, high: float, low: float, close: float):
    return (
        _bar("AAA", day, open_=open_, high=high, low=low, close=close),
        _bar("SPY", day, open_=400, high=401, low=399, close=400),
    )


def test_three_day_return_skips_the_holiday_and_ignores_the_exit_day_range() -> None:
    calendar = NYSECalendar()
    days = [
        SESSION,
        date(2024, 6, 18),
        date(2024, 6, 20),
        date(2024, 6, 21),
    ]
    stock = []
    spy = []
    opens = [100, 101, 102, 110]
    lows = [98, 90, 95, 50]
    highs = [101, 102, 130, 200]
    for day, open_, low, high in zip(days, opens, lows, highs, strict=True):
        share, bench = _pair(day, open_, high, low, open_)
        stock.append(share)
        spy.append(bench)
    completed, missing = compute_forward_returns(
        "AAA",
        stock,
        spy,
        session=SESSION,
        as_of=datetime(2024, 6, 21, 16, 0, tzinfo=NY),
        calendar=calendar,
    )
    by_horizon = {row.horizon: row for row in completed}
    assert missing == ((5, "exit bar unavailable"),)
    assert by_horizon[2].exit_session == date(2024, 6, 20)
    row = by_horizon[3]
    assert row.exit_session == date(2024, 6, 21)
    assert row.stock_return == pytest.approx(0.10)
    assert row.spy_return == pytest.approx(0)
    assert row.excess_return == pytest.approx(0.10)
    assert row.mae == pytest.approx(-0.10)
    assert row.mfe == pytest.approx(0.30)


def test_friday_plus_one_session_is_monday_and_july_fourth_is_skipped() -> None:
    calendar = NYSECalendar()
    friday = date(2024, 6, 21)
    monday = date(2024, 6, 24)
    stock = [
        _bar("AAA", friday, open_=10, high=11, low=9, close=10),
        _bar("AAA", monday, open_=12, high=12, low=12, close=12),
    ]
    spy = [
        _bar("SPY", friday, open_=20, high=20, low=20, close=20),
        _bar("SPY", monday, open_=21, high=21, low=21, close=21),
    ]
    completed, _missing = compute_forward_returns(
        "AAA",
        stock,
        spy,
        session=friday,
        as_of=datetime(2024, 6, 28, 16, 0, tzinfo=NY),
        calendar=calendar,
    )
    one_day = next(row for row in completed if row.horizon == 1)
    assert one_day.exit_session == monday
    assert one_day.stock_return == pytest.approx(0.2)
    assert one_day.spy_return == pytest.approx(0.05)
    assert one_day.excess_return == pytest.approx(0.15)

    before = date(2024, 7, 3)
    after = date(2024, 7, 5)
    holiday, missing = compute_forward_returns(
        "AAA",
        [
            _bar("AAA", before, open_=10, high=10, low=10, close=10),
            _bar("AAA", after, open_=10, high=10, low=10, close=10),
        ],
        [
            _bar("SPY", before, open_=10, high=10, low=10, close=10),
            _bar("SPY", after, open_=10, high=10, low=10, close=10),
        ],
        session=before,
        as_of=datetime(2024, 7, 5, 16, 0, tzinfo=NY),
        calendar=calendar,
    )
    assert next(row for row in holiday if row.horizon == 1).exit_session == after
    assert all(reason[0] != 1 or "July" not in reason[1] for reason in missing)


def test_signal_morning_cannot_see_the_entry_bar() -> None:
    bar = _bar("AAA", SESSION, open_=10, high=10, low=10, close=10)
    spy = _bar("SPY", SESSION, open_=10, high=10, low=10, close=10)
    completed, missing = compute_forward_returns(
        "AAA",
        [bar],
        [spy],
        session=SESSION,
        as_of=datetime(2024, 6, 17, 9, 0, tzinfo=NY),
        calendar=NYSECalendar(),
    )
    assert completed == ()
    assert missing[0] == (1, "entry bar unavailable")


def test_missing_spy_does_not_become_a_zero_excess() -> None:
    completed, missing = compute_forward_returns(
        "AAA",
        [_bar("AAA", SESSION, open_=10, high=11, low=9, close=10)],
        [],
        session=SESSION,
        as_of=datetime(2024, 6, 18, 16, 0, tzinfo=NY),
        calendar=NYSECalendar(),
    )
    assert completed == ()
    assert missing[0][1] == "SPY entry bar unavailable"


def test_a_split_between_entry_and_exit_adjusts_the_return() -> None:
    entry = date(2024, 6, 17)
    exit_day = date(2024, 6, 18)
    completed, missing = compute_forward_returns(
        "AAA",
        [
            _bar("AAA", entry, open_=200, high=210, low=190, close=200),
            _bar("AAA", exit_day, open_=110, high=110, low=110, close=110),
        ],
        [
            _bar("SPY", entry, open_=100, high=100, low=100, close=100),
            _bar("SPY", exit_day, open_=100, high=100, low=100, close=100),
        ],
        session=entry,
        as_of=datetime(2024, 6, 18, 16, 0, tzinfo=NY),
        calendar=NYSECalendar(),
        splits=(Split("AAA", exit_day, 1, 2),),
    )
    row = next(item for item in completed if item.horizon == 1)
    assert missing == () or all(item[0] != 1 for item in missing)
    assert row.stock_return == pytest.approx(0.10)
    assert row.excess_return == pytest.approx(0.10)
    assert row.mae == pytest.approx(-0.05)


def test_forward_rows_round_trip(tmp_path, repo_root) -> None:
    store = Store(tmp_path / "forward.db", repo_root / "migrations")
    created = datetime(2024, 6, 21, 20, 0, tzinfo=NY)
    try:
        store.insert_run(
            SignalRun(
                run_id="run-a",
                signal_date=SESSION,
                signal_time=datetime(2024, 6, 17, 9, 0, tzinfo=NY),
                timezone="America/New_York",
                config_hash="hash",
                code_version="0.1.0",
                created_at=created,
                universe_size=1,
                eligible_size=1,
                scored_size=1,
                candidate_size=1,
                duration_ms=None,
                provider_errors=0,
                missing_data_count=0,
                market_regime=None,
                no_trade=False,
                status="ok",
                universe_list_as_of=None,
                point_in_time_membership=False,
                notes="",
            )
        )
        store.insert_forward_return(
            run_id="run-a",
            symbol="AAA",
            horizon=1,
            entry_session=SESSION,
            entry_price=10,
            exit_session=date(2024, 6, 18),
            exit_price=11,
            stock_return=0.1,
            spy_return=0.01,
            excess_return=0.09,
            mae=-0.02,
            mfe=0.03,
            code_version="0.1.0",
        )
        row = store.conn.execute(
            "SELECT excess_return, mae FROM forward_returns WHERE run_id = 'run-a'"
        ).fetchone()
        assert row["excess_return"] == pytest.approx(0.09)
        assert row["mae"] == pytest.approx(-0.02)
    finally:
        store.close()
