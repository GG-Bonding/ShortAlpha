from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

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
