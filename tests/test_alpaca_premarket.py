from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import httpx
import pytest

from shortalpha.errors import ConfigError, DataUnavailableError
from shortalpha.providers.alpaca_premarket import AlpacaPremarketProvider

NY = ZoneInfo("America/New_York")
NY_SESSION = date(2024, 6, 20)


def _provider(handler, *, feed: str = "sip", secret: str = "secret") -> AlpacaPremarketProvider:
    return AlpacaPremarketProvider(
        api_key="key",
        api_secret=secret,
        feed=feed,
        base_url="https://data.alpaca.markets",
        signal_clock=time(9, 0),
        premarket_start=time(4, 0),
        timezone=NY,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        owns_client=True,
    )


def _bar(stamp: str, volume: float, close: float = 10) -> dict[str, object]:
    return {"t": stamp, "o": close, "h": close + 1, "l": close - 1, "c": close, "v": volume}


def test_iex_keeps_a_timestamped_price_and_leaves_volume_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["feed"] == "iex"
        return httpx.Response(
            200,
            json={
                "bars": {"AAA": [_bar("2024-06-20T12:30:00Z", 50, 103)]},
                "next_page_token": None,
            },
        )

    window = _provider(handler, feed="iex").window(
        "AAA", NY_SESSION, datetime(2024, 6, 20, 9, 0, tzinfo=NY)
    )
    assert window.available is False
    assert window.volume is None
    assert window.last_price == 103
    assert window.price_consolidated is False
    assert window.available_at == datetime(2024, 6, 20, 8, 31, tzinfo=NY)
    assert "premarket_volume_available is false" in window.reason


def test_iex_without_prints_does_not_invent_a_price() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"bars": {"AAA": []}, "next_page_token": None})

    window = _provider(handler, feed="iex").window(
        "AAA", NY_SESSION, datetime(2024, 6, 20, 9, 0, tzinfo=NY)
    )
    assert window.available is False
    assert window.last_price is None
    assert window.volume is None
    assert "premarket_volume_available is false" in window.reason


def test_sip_keeps_minutes_that_end_at_the_signal_clock() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["start"])
        seen.append(request.url.params["end"])
        assert request.url.params["timeframe"] == "1Min"
        assert request.url.params["feed"] == "sip"
        assert request.url.params["adjustment"] == "raw"
        return httpx.Response(
            200,
            json={
                "bars": {
                    "AAA": [
                        _bar("2024-06-20T12:59:00Z", 100, 11),
                        _bar("2024-06-20T13:00:00Z", 900, 50),
                        _bar("2024-06-19T12:59:00Z", 7, 8),
                    ]
                },
                "next_page_token": None,
            },
        )

    window = _provider(handler).window(
        "AAA",
        NY_SESSION,
        datetime(2024, 6, 20, 9, 0, tzinfo=NY),
    )
    assert window.available is True
    assert window.volume == 100
    assert window.last_price == 11
    assert window.high == 12
    assert window.low == 10
    assert seen[0].startswith("2024-06-20T04:00:00")
    assert seen[1].startswith("2024-06-20T09:00:00")


def test_past_session_uses_that_mornings_window() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["start"] = request.url.params["start"]
        seen["end"] = request.url.params["end"]
        return httpx.Response(200, json={"bars": {"AAA": []}, "next_page_token": None})

    window = _provider(handler).window(
        "AAA",
        date(2024, 6, 18),
        datetime(2024, 6, 20, 9, 0, tzinfo=NY),
    )
    assert window.available is True
    assert window.volume == 0
    assert window.last_price is None
    assert window.reason == "no premarket prints"
    assert seen["start"].startswith("2024-06-18T04:00:00")
    assert seen["end"].startswith("2024-06-18T09:00:00")


def test_http_error_does_not_echo_the_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    with pytest.raises(DataUnavailableError, match="HTTP 401") as caught:
        _provider(handler, secret="super-secret-value").window(
            "AAA",
            NY_SESSION,
            datetime(2024, 6, 20, 9, 0, tzinfo=NY),
        )
    assert "super-secret-value" not in str(caught.value)


def test_sip_without_keys_fails_before_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    with pytest.raises(ConfigError, match="APCA_API_KEY_ID"):
        AlpacaPremarketProvider.from_env(
            feed="sip",
            base_url="https://data.alpaca.markets",
            signal_clock=time(9, 0),
            premarket_start=time(4, 0),
            timezone="America/New_York",
        )
