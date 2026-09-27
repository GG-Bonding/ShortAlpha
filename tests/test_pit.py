import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from shortalpha.domain import DailyBar, NewsItem
from shortalpha.errors import DataUnavailableError
from shortalpha.logging_utils import raise_unavailable
from shortalpha.pit import select_available
from shortalpha.providers.fixture_market import FixtureMarketDataProvider
from shortalpha.providers.fixture_news import FixtureNewsProvider

NY = ZoneInfo("America/New_York")
SIGNAL = datetime(2025, 4, 10, 9, 0, tzinfo=NY)


def _news(news_id: str, hour: int, minute: int) -> NewsItem:
    stamp = datetime(2025, 4, 10, hour, minute, tzinfo=NY)
    return NewsItem(
        id=news_id,
        symbols=("NVDA",),
        headline=news_id,
        summary="",
        source="fixture",
        event_time=stamp,
        published_at=stamp,
        available_at=stamp,
        url=None,
    )


def test_spec_example_keeps_0830_and_drops_0915() -> None:
    items = [_news("early", 8, 30), _news("late", 9, 15)]
    kept = select_available(items, SIGNAL)
    assert [item.id for item in kept] == ["early"]


def test_record_at_exact_signal_time_is_available() -> None:
    exact = _news("exact", 9, 0)
    assert [item.id for item in select_available([exact], SIGNAL)] == ["exact"]


def test_naive_signal_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        select_available([_news("early", 8, 30)], datetime(2025, 4, 10, 9, 0))


def test_timeline_must_be_ordered() -> None:
    with pytest.raises(ValueError, match="published_at"):
        DailyBar(
            symbol="AAA",
            session_date=SIGNAL.date(),
            open=10,
            high=11,
            low=9,
            close=10.5,
            volume=100,
            event_time=datetime(2025, 4, 10, 14, 0, tzinfo=NY),
            published_at=datetime(2025, 4, 10, 16, 0, tzinfo=NY),
            available_at=datetime(2025, 4, 10, 15, 0, tzinfo=NY),
            source="fixture",
        )


def test_fixture_files_apply_signal_time(repo_root) -> None:
    market = FixtureMarketDataProvider.from_json(
        repo_root / "fixtures" / "market" / "sample_bars.json"
    )
    bars = market.daily_bars("AAA", SIGNAL.date().replace(month=4, day=1), SIGNAL.date(), SIGNAL)
    assert [bar.session_date.isoformat() for bar in bars] == ["2025-04-09"]

    news = FixtureNewsProvider.from_json(repo_root / "fixtures" / "news" / "sample_news.json")
    items = news.news("NVDA", datetime(2025, 4, 1, tzinfo=NY), SIGNAL, SIGNAL)
    assert [item.id for item in items] == ["news-0830"]
    assert news.news("ZZZ", datetime(2025, 4, 1, tzinfo=NY), SIGNAL, SIGNAL) == []


def test_unknown_market_symbol_is_an_error(repo_root) -> None:
    market = FixtureMarketDataProvider.from_json(
        repo_root / "fixtures" / "market" / "sample_bars.json"
    )
    with pytest.raises(DataUnavailableError, match="NOPE") as caught:
        market.daily_bars("NOPE", SIGNAL.date(), SIGNAL.date(), SIGNAL)
    err = caught.value
    assert err.symbol == "NOPE"
    assert err.provider == "fixture"
    assert err.operation == "daily_bars"
    assert err.timestamp == SIGNAL


def test_raise_unavailable_logs_required_fields(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.ERROR, logger="shortalpha")
    with pytest.raises(DataUnavailableError, match="timeout"):
        raise_unavailable(
            symbol="NVDA",
            provider="alpaca",
            operation="daily_bars",
            timestamp=SIGNAL,
            reason="timeout",
        )
    message = caplog.records[-1].getMessage()
    for field in ("NVDA", "alpaca", "daily_bars", "timeout", "2025-04-10"):
        assert field in message
