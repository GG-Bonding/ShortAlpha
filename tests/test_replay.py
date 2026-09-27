import json
from datetime import date, datetime, time

from shortalpha.calendar import NYSECalendar
from shortalpha.cli import main
from shortalpha.config import load_config
from shortalpha.domain import DailyBar, NewsItem, PremarketWindow, UniverseList, UniverseMember
from shortalpha.paths import project_root
from shortalpha.providers.fixture_market import FixtureMarketDataProvider
from shortalpha.providers.fixture_news import FixtureNewsProvider
from shortalpha.providers.fixture_premarket import FixturePreMarketProvider
from shortalpha.providers.fixture_universe import FixtureUniverseProvider
from shortalpha.replay.engine import replay
from shortalpha.storage import Store
from tests.factor_setup import NY

SESSION = date(2024, 6, 20)
SYMBOLS = ("AAA", "BBB", "CCC", "SPY", "QQQ", "XLK", "XLF", "XLE")


def _cfg():
    return load_config(project_root() / "config" / "default.yaml", root=project_root())


def _bars() -> list[DailyBar]:
    calendar = NYSECalendar()
    days = [calendar.shift(SESSION, -offset) for offset in range(40, -1, -1)]
    bars: list[DailyBar] = []
    for index, day in enumerate(days):
        close = 100 + index * 0.2
        stamp = datetime.combine(day, time(16, 0), tzinfo=NY)
        for symbol in SYMBOLS:
            bars.append(
                DailyBar(
                    symbol=symbol,
                    session_date=day,
                    open=close,
                    high=close + 1,
                    low=close - 0.5,
                    close=close,
                    volume=1_000_000,
                    event_time=stamp,
                    published_at=stamp,
                    available_at=stamp,
                    source="fixture",
                )
            )
    return bars


def _windows() -> list[PremarketWindow]:
    windows = []
    for day in (date(2024, 6, 18), SESSION):
        for symbol in ("AAA", "BBB", "CCC"):
            windows.append(
                PremarketWindow(
                    symbol=symbol,
                    session_date=day,
                    available=False,
                    volume=None,
                    last_price=None,
                    high=None,
                    low=None,
                    event_time=None,
                    published_at=None,
                    available_at=None,
                    reason="feed has no premarket",
                )
            )
    return windows


def _news() -> list[NewsItem]:
    early = datetime.combine(SESSION, time(8, 30), tzinfo=NY)
    late = datetime.combine(SESSION, time(9, 15), tzinfo=NY)
    return [
        NewsItem(
            id="early",
            symbols=("AAA",),
            headline="AAA earnings beat",
            summary="",
            source="fixture",
            event_time=early,
            published_at=early,
            available_at=early,
            url=None,
        ),
        NewsItem(
            id="late",
            symbols=("AAA",),
            headline="AAA guidance cut",
            summary="Company cuts guidance",
            source="fixture",
            event_time=late,
            published_at=late,
            available_at=late,
            url=None,
        ),
    ]


def _universe() -> FixtureUniverseProvider:
    return FixtureUniverseProvider(
        UniverseList(
            list_as_of=date(2026, 9, 1),
            point_in_time_membership=False,
            members=(
                UniverseMember("AAA", ("sp500",)),
                UniverseMember("BBB", ("nasdaq100", "sp500")),
                UniverseMember("CCC", ("nasdaq100",)),
            ),
        )
    )


def _replay(store: Store, start: date, end: date):
    cfg = _cfg()
    return replay(
        cfg,
        root=project_root(),
        calendar=NYSECalendar(),
        market=FixtureMarketDataProvider(_bars()),
        premarket=FixturePreMarketProvider(_windows()),
        news=FixtureNewsProvider(_news()),
        universe=_universe(),
        store=store,
        start=start,
        end=end,
        splits_for=lambda symbol, start, end, as_of: (),
        notes="corporate_actions=empty",
    )


def test_replay_is_deterministic_and_skips_the_holiday(tmp_path, repo_root) -> None:
    store = Store(tmp_path / "replay.db", repo_root / "migrations")
    try:
        first = _replay(store, date(2024, 6, 18), SESSION)
        second = _replay(store, date(2024, 6, 18), SESSION)
        assert [item.signal_date for item in first] == [date(2024, 6, 18), SESSION]
        assert [item.snapshot_hash for item in first] == [item.snapshot_hash for item in second]
        assert {item.run_id for item in first}.isdisjoint({item.run_id for item in second})
        original = store.get_snapshot(first[1].run_id)
        assert original is not None
        again = store.get_snapshot(first[1].run_id)
        assert again == original
        document = json.loads(original[0])
        assert document["signal_time"] == "09:00:00"
        aaa = next(row for row in document["ranked"] if row["symbol"] == "AAA")
        reasons = aaa["factors"]["event"]["reasons"]
        risks = aaa["factors"]["event"]["risks"]
        assert "Earnings beat" in reasons
        assert "Guidance cut" not in risks
        forward = store.conn.execute("SELECT COUNT(*) FROM forward_returns").fetchone()[0]
        assert forward == 0
    finally:
        store.close()


def test_weekend_range_runs_no_sessions(tmp_path, repo_root) -> None:
    store = Store(tmp_path / "replay.db", repo_root / "migrations")
    try:
        assert _replay(store, date(2024, 6, 15), date(2024, 6, 16)) == ()
    finally:
        store.close()


def test_replay_command_skips_a_weekend(tmp_path, repo_root, capsys) -> None:
    code = main(
        [
            "replay",
            "--from",
            "2024-06-15",
            "--to",
            "2024-06-16",
            "--config",
            str(repo_root / "config" / "default.yaml"),
            "--database",
            str(tmp_path / "replay.db"),
        ]
    )
    assert code == 0
    assert "sessions: 0" in capsys.readouterr().out
