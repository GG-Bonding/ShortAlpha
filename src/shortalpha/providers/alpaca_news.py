"""Alpaca Benzinga news.

The stored headline is the text returned now. If updated_at is later than
created_at, that text was not proven public at created_at, so available_at is
updated_at. An unchanged article keeps created_at.
"""

import os
from datetime import datetime

import httpx

from shortalpha.domain import NewsItem
from shortalpha.errors import ConfigError, FixtureError
from shortalpha.logging_utils import raise_unavailable
from shortalpha.pit import select_available
from shortalpha.providers.parsing import parse_dt


class AlpacaNewsProvider:
    name = "alpaca"

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str,
        client: httpx.Client,
        owns_client: bool,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._owns_client = owns_client

    @classmethod
    def from_env(
        cls,
        *,
        base_url: str,
        client: httpx.Client | None = None,
    ) -> "AlpacaNewsProvider":
        api_key = os.environ.get("APCA_API_KEY_ID", "")
        api_secret = os.environ.get("APCA_API_SECRET_KEY", "")
        if not api_key or not api_secret:
            raise ConfigError("APCA_API_KEY_ID and APCA_API_SECRET_KEY are required")
        owns_client = client is None
        if client is None:
            client = httpx.Client(timeout=30.0)
        return cls(
            api_key=api_key,
            api_secret=api_secret,
            base_url=base_url,
            client=client,
            owns_client=owns_client,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def news(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        as_of: datetime,
    ) -> list[NewsItem]:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        if end > as_of:
            end = as_of
        rows = self._fetch_rows(symbol, start, end, as_of)
        items: list[NewsItem] = []
        for raw in rows:
            try:
                items.append(_item_from_alpaca(symbol, raw))
            except (KeyError, TypeError, ValueError, FixtureError) as exc:
                raise_unavailable(
                    symbol=symbol,
                    provider=self.name,
                    operation="news",
                    timestamp=as_of,
                    reason=str(exc),
                )
        visible = [
            item
            for item in select_available(items, as_of)
            if symbol in item.symbols and start <= item.published_at <= end
        ]
        return sorted(visible, key=lambda item: (item.published_at, item.id))

    def _fetch_rows(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        as_of: datetime,
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        page_token: str | None = None
        while True:
            params: dict[str, str] = {
                "symbols": symbol,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "limit": "50",
                "sort": "asc",
                "include_content": "false",
            }
            if page_token:
                params["page_token"] = page_token
            try:
                response = self._client.get(
                    f"{self._base_url}/v1beta1/news",
                    params=params,
                    headers={
                        "APCA-API-KEY-ID": self._api_key,
                        "APCA-API-SECRET-KEY": self._api_secret,
                    },
                )
            except httpx.HTTPError as exc:
                raise_unavailable(
                    symbol=symbol,
                    provider=self.name,
                    operation="news",
                    timestamp=as_of,
                    reason=exc.__class__.__name__,
                )
            if response.status_code >= 400:
                raise_unavailable(
                    symbol=symbol,
                    provider=self.name,
                    operation="news",
                    timestamp=as_of,
                    reason=f"HTTP {response.status_code}: {response.text[:200]}",
                )
            body = response.json()
            if not isinstance(body, dict):
                raise_unavailable(
                    symbol=symbol,
                    provider=self.name,
                    operation="news",
                    timestamp=as_of,
                    reason="alpaca response was not an object",
                )
            page = body.get("news") or []
            if not isinstance(page, list):
                raise_unavailable(
                    symbol=symbol,
                    provider=self.name,
                    operation="news",
                    timestamp=as_of,
                    reason="alpaca news payload was not a list",
                )
            rows.extend(page)
            token = body.get("next_page_token")
            if not token:
                break
            page_token = str(token)
        return rows


def _item_from_alpaca(symbol: str, raw: dict[str, object]) -> NewsItem:
    created = parse_dt(raw["created_at"], "created_at")
    available = created
    updated = raw.get("updated_at")
    if updated not in (None, ""):
        revised = parse_dt(updated, "updated_at")
        if revised > created:
            available = revised
    symbols = raw.get("symbols") or [symbol]
    if not isinstance(symbols, list) or not all(isinstance(item, str) for item in symbols):
        raise ValueError("news symbols were not a list of strings")
    summary = raw.get("summary") or ""
    if not isinstance(summary, str):
        raise ValueError("news summary was not a string")
    url = raw.get("url")
    if url is not None and not isinstance(url, str):
        raise ValueError("news url was not a string")
    return NewsItem(
        id=str(raw["id"]),
        symbols=tuple(symbols),
        headline=str(raw["headline"]),
        summary=summary,
        source=str(raw["source"]),
        event_time=created,
        published_at=created,
        available_at=available,
        url=url,
    )
