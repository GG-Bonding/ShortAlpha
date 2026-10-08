from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from shortalpha.calendar import NYSECalendar
from shortalpha.errors import ConfigError, DataUnavailableError
from shortalpha.providers.alpaca_market import AlpacaMarketDataProvider, daily_bar_from_alpaca

NY = ZoneInfo("America/New_York")


def _raw(stamp: str, close: float = 10) -> dict[str, object]:
    return {"t": stamp, "o": close, "h": close, "l": close, "c": close, "v": 100}


def test_daily_bar_available_at_is_the_close_not_the_bar_start() -> None:
    calendar = NYSECalendar()
    regular = daily_bar_from_alpaca("AAA", _raw("2024-01-02T05:00:00Z"), calendar)
    assert regular.session_date == date(2024, 1, 2)
    assert regular.available_at == datetime(2024, 1, 2, 16, 0, tzinfo=NY)
    assert regular.available_at != datetime(2024, 1, 2, 5, 0, tzinfo=ZoneInfo("UTC"))

    early = daily_bar_from_alpaca("AAA", _raw("2024-07-03T04:00:00Z"), calendar)
    assert early.session_date == date(2024, 7, 3)
    assert early.available_at == datetime(2024, 7, 3, 13, 0, tzinfo=NY)


def test_signal_morning_cannot_see_that_sessions_daily_bar() -> None:
    calendar = NYSECalendar()
    as_of = datetime(2024, 1, 2, 9, 0, tzinfo=NY)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url.params["adjustment"]))
        assert request.url.params["feed"] == "iex"
        assert request.url.params["end"] == as_of.isoformat()
        assert request.headers["APCA-API-KEY-ID"] == "key"
        return httpx.Response(
            200,
            json={"bars": {"AAA": [_raw("2024-01-02T05:00:00Z")]}, "next_page_token": None},
        )

    provider = _provider(calendar, handler)
    assert provider.daily_bars("AAA", date(2024, 1, 2), date(2024, 1, 2), as_of) == []
    assert seen == ["raw"]


def test_pages_are_followed_and_prior_closes_remain() -> None:
    calendar = NYSECalendar()
    as_of = datetime(2024, 1, 4, 9, 0, tzinfo=NY)
    pages = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        pages["n"] += 1
        if pages["n"] == 1:
            return httpx.Response(
                200,
                json={
                    "bars": {"AAA": [_raw("2024-01-02T05:00:00Z", 10)]},
                    "next_page_token": "next",
                },
            )
        assert request.url.params["page_token"] == "next"
        return httpx.Response(
            200,
            json={"bars": {"AAA": [_raw("2024-01-03T05:00:00Z", 11)]}, "next_page_token": None},
        )

    bars = _provider(calendar, handler).daily_bars("AAA", date(2024, 1, 2), date(2024, 1, 3), as_of)
    assert [bar.session_date.isoformat() for bar in bars] == ["2024-01-02", "2024-01-03"]
    assert bars[0].available_at.hour == 16


def test_http_error_does_not_echo_the_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    provider = _provider(NYSECalendar(), handler, secret="super-secret-value")
    as_of = datetime(2024, 1, 4, 9, 0, tzinfo=NY)
    with pytest.raises(DataUnavailableError, match="503") as caught:
        provider.daily_bars("AAA", date(2024, 1, 2), date(2024, 1, 3), as_of)
    assert "super-secret-value" not in str(caught.value)
    assert caught.value.symbol == "AAA"
    assert caught.value.provider == "alpaca"


def test_old_news_uses_the_last_sip_minute_before_the_headline() -> None:
    news_at = datetime(2024, 6, 17, 16, 5, tzinfo=NY)
    as_of = datetime(2024, 6, 18, 9, 0, tzinfo=NY)
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["feed"] = request.url.params["feed"]
        seen["timeframe"] = request.url.params["timeframe"]
        return httpx.Response(
            200,
            json={
                "bars": {
                    "AAA": [
                        _raw("2024-06-17T20:03:00Z", 100.4),
                        _raw("2024-06-17T20:05:00Z", 109),
                    ]
                },
                "next_page_token": None,
            },
        )

    printed = _provider(NYSECalendar(), handler).price_at("AAA", news_at, as_of)
    assert printed is not None
    assert seen["feed"] == "sip"
    assert seen["timeframe"] == "1Min"
    assert printed.price == 100.4
    assert printed.consolidated is True
    assert printed.available_at == datetime(2024, 6, 17, 16, 4, tzinfo=NY)


def test_a_minute_lookup_miss_leaves_the_daily_baseline_unchanged() -> None:
    news_at = datetime(2024, 6, 17, 16, 5, tzinfo=NY)
    as_of = datetime(2024, 6, 18, 9, 0, tzinfo=NY)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="sip unavailable")

    assert _provider(NYSECalendar(), handler).price_at("AAA", news_at, as_of) is None


def test_missing_credentials_fail_before_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    with pytest.raises(ConfigError, match="APCA_API_KEY_ID"):
        AlpacaMarketDataProvider.from_env(
            feed="iex",
            base_url="https://data.alpaca.markets",
            calendar=NYSECalendar(),
            client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))),
        )


def _provider(
    calendar: NYSECalendar,
    handler,
    secret: str = "secret",
) -> AlpacaMarketDataProvider:
    return AlpacaMarketDataProvider(
        api_key="key",
        api_secret=secret,
        feed="iex",
        base_url="https://data.alpaca.markets",
        calendar=calendar,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        owns_client=True,
    )
