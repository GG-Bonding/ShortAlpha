import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from shortalpha.domain import PremarketWindow
from shortalpha.errors import DataUnavailableError
from shortalpha.providers.fixture_premarket import FixturePreMarketProvider
from shortalpha.providers.fixture_universe import FixtureUniverseProvider

NY = ZoneInfo("America/New_York")
SIGNAL = datetime(2025, 4, 10, 9, 0, tzinfo=NY)


def test_universe_fixture_is_not_point_in_time_membership(repo_root) -> None:
    provider = FixtureUniverseProvider.from_json(
        repo_root / "fixtures" / "universe" / "sample.json"
    )
    loaded = provider.load(SIGNAL)
    assert loaded.point_in_time_membership is False
    assert loaded.list_as_of.isoformat() == "2026-09-01"
    symbols = {member.symbol: member.sources for member in loaded.members}
    assert symbols["BBB"] == ("nasdaq100", "sp500")
    assert set(symbols) == {"AAA", "BBB", "CCC"}


def test_premarket_capability_and_future_observation_are_explicit(repo_root) -> None:
    provider = FixturePreMarketProvider.from_json(
        repo_root / "fixtures" / "premarket" / "sample_premarket.json"
    )
    aaa = provider.window("AAA", SIGNAL.date(), SIGNAL)
    assert aaa.available is True
    assert aaa.last_price == 11.2
    assert aaa.volume == 180000

    bbb = provider.window("BBB", SIGNAL.date(), SIGNAL)
    assert bbb.available is False
    assert "consolidated premarket" in bbb.reason
    assert bbb.last_price is None

    ccc = provider.window("CCC", SIGNAL.date(), SIGNAL)
    assert ccc.available is False
    assert ccc.reason

    with pytest.raises(DataUnavailableError) as caught:
        provider.window("DDD", SIGNAL.date(), SIGNAL)
    assert caught.value.symbol == "DDD"
    assert caught.value.operation == "premarket"


def test_an_iex_price_survives_fixture_replay(tmp_path) -> None:
    stamp = datetime(2025, 4, 10, 8, 30, tzinfo=NY)
    window = PremarketWindow(
        symbol="AAA",
        session_date=SIGNAL.date(),
        available=False,
        volume=None,
        last_price=103,
        high=104,
        low=102,
        event_time=stamp,
        published_at=stamp,
        available_at=stamp,
        reason="IEX feed is not consolidated premarket; premarket_volume_available is false",
        price_consolidated=False,
    )
    direct = FixturePreMarketProvider([window]).window("AAA", SIGNAL.date(), SIGNAL)
    assert direct.available is False
    assert direct.volume is None
    assert direct.last_price == 103
    assert direct.available_at == stamp
    assert direct.price_consolidated is False

    path = tmp_path / "premarket.json"
    path.write_text(
        json.dumps(
            {
                "windows": [
                    {
                        "symbol": "AAA",
                        "session_date": "2025-04-10",
                        "available": False,
                        "volume": None,
                        "last_price": 103,
                        "high": 104,
                        "low": 102,
                        "event_time": stamp.isoformat(),
                        "published_at": stamp.isoformat(),
                        "available_at": stamp.isoformat(),
                        "reason": window.reason,
                        "price_consolidated": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    loaded = FixturePreMarketProvider.from_json(path).window("AAA", SIGNAL.date(), SIGNAL)
    assert loaded.last_price == 103
    assert loaded.volume is None
    assert loaded.available is False
    assert loaded.price_consolidated is False
    assert loaded.available_at == stamp
