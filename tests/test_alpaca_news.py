from datetime import datetime, timedelta

import httpx
import pytest

from shortalpha.errors import ConfigError, DataUnavailableError
from shortalpha.providers.alpaca_news import AlpacaNewsProvider
from tests.factor_setup import NY

AS_OF = datetime(2025, 4, 10, 9, 0, tzinfo=NY)


def _provider(handler, *, secret: str = "secret") -> AlpacaNewsProvider:
    return AlpacaNewsProvider(
        api_key="key",
        api_secret=secret,
        base_url="https://data.alpaca.markets",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        owns_client=True,
    )


def _article(
    stamp: str, headline: str, article_id: int, *, updated_at: str | None = None
) -> dict[str, object]:
    return {
        "id": article_id,
        "headline": headline,
        "summary": "",
        "source": "benzinga",
        "created_at": stamp,
        "updated_at": stamp if updated_at is None else updated_at,
        "url": "https://example.test/news",
        "symbols": ["NVDA"],
    }


def test_created_at_is_available_at_and_a_later_article_is_dropped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["end"] == AS_OF.isoformat()
        assert request.url.params["include_content"] == "false"
        return httpx.Response(
            200,
            json={
                "news": [
                    _article("2025-04-10T12:30:00Z", "NVDA earnings beat", 1),
                    _article("2025-04-10T13:15:00Z", "NVDA guidance cut", 2),
                ],
                "next_page_token": None,
            },
        )

    items = _provider(handler).news(
        "NVDA", AS_OF - timedelta(hours=72), AS_OF + timedelta(hours=2), AS_OF
    )
    assert [item.id for item in items] == ["1"]
    assert items[0].available_at == datetime(2025, 4, 10, 8, 30, tzinfo=NY)
    assert items[0].published_at == items[0].available_at


def test_a_revised_article_is_not_visible_before_updated_at() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "news": [
                    _article(
                        "2025-04-10T12:30:00Z",
                        "NVDA earnings beat",
                        1,
                        updated_at="2025-04-10T14:00:00Z",
                    )
                ],
                "next_page_token": None,
            },
        )

    items = _provider(handler).news("NVDA", AS_OF - timedelta(hours=72), AS_OF, AS_OF)
    assert items == []


def test_an_empty_payload_is_an_empty_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"news": [], "next_page_token": None})

    assert _provider(handler).news("NVDA", AS_OF - timedelta(hours=1), AS_OF, AS_OF) == []


def test_http_error_does_not_echo_the_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    with pytest.raises(DataUnavailableError, match="HTTP 401") as caught:
        _provider(handler, secret="super-secret-value").news(
            "NVDA", AS_OF - timedelta(hours=1), AS_OF, AS_OF
        )
    assert "super-secret-value" not in str(caught.value)


def test_missing_credentials_fail_before_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    with pytest.raises(ConfigError, match="APCA_API_KEY_ID"):
        AlpacaNewsProvider.from_env(base_url="https://data.alpaca.markets")
