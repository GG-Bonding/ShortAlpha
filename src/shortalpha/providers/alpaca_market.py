"""Alpaca daily bars.

The payload timestamp is the start of the bar. available_at is the NYSE close,
so a bar dated on the signal session is not visible at 09:00.
"""

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

from shortalpha.calendar import NYSECalendar
from shortalpha.domain import DailyBar
from shortalpha.errors import ConfigError, FixtureError
from shortalpha.logging_utils import raise_unavailable
from shortalpha.pit import select_available
from shortalpha.providers.parsing import parse_dt

_NEW_YORK = ZoneInfo("America/New_York")


class AlpacaMarketDataProvider:
    name = "alpaca"

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        feed: str,
        base_url: str,
        calendar: NYSECalendar,
        client: httpx.Client,
        owns_client: bool,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._feed = feed
        self._base_url = base_url.rstrip("/")
        self._calendar = calendar
        self._client = client
        self._owns_client = owns_client

    @classmethod
    def from_env(
        cls,
        *,
        feed: str,
        base_url: str,
        calendar: NYSECalendar,
        client: httpx.Client | None = None,
    ) -> "AlpacaMarketDataProvider":
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
            feed=feed,
            base_url=base_url,
            calendar=calendar,
            client=client,
            owns_client=owns_client,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        as_of: datetime,
    ) -> list[DailyBar]:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        raw_rows = self._fetch_rows(symbol, start, as_of)
        bars: list[DailyBar] = []
        for raw in raw_rows:
            try:
                bars.append(daily_bar_from_alpaca(symbol, raw, self._calendar))
            except (KeyError, TypeError, ValueError, FixtureError) as exc:
                raise_unavailable(
                    symbol=symbol,
                    provider=self.name,
                    operation="daily_bars",
                    timestamp=as_of,
                    reason=str(exc),
                )
        kept = [bar for bar in select_available(bars, as_of) if start <= bar.session_date <= end]
        return sorted(kept, key=lambda bar: bar.session_date)

    def _fetch_rows(self, symbol: str, start: date, as_of: datetime) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        page_token: str | None = None
        while True:
            params: dict[str, str] = {
                "symbols": symbol,
                "timeframe": "1Day",
                "start": datetime.combine(start, datetime.min.time(), tzinfo=_NEW_YORK).isoformat(),
                "end": as_of.isoformat(),
                "adjustment": "raw",
                "feed": self._feed,
                "limit": "10000",
                "sort": "asc",
            }
            if page_token:
                params["page_token"] = page_token
            try:
                response = self._client.get(
                    f"{self._base_url}/v2/stocks/bars",
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
                    operation="daily_bars",
                    timestamp=as_of,
                    reason=exc.__class__.__name__,
                )
            if response.status_code >= 400:
                raise_unavailable(
                    symbol=symbol,
                    provider=self.name,
                    operation="daily_bars",
                    timestamp=as_of,
                    reason=f"HTTP {response.status_code}: {response.text[:200]}",
                )
            body = response.json()
            if not isinstance(body, dict):
                raise_unavailable(
                    symbol=symbol,
                    provider=self.name,
                    operation="daily_bars",
                    timestamp=as_of,
                    reason="alpaca response was not an object",
                )
            grouped = body.get("bars") or {}
            if grouped is None or not isinstance(grouped, dict):
                raise_unavailable(
                    symbol=symbol,
                    provider=self.name,
                    operation="daily_bars",
                    timestamp=as_of,
                    reason="alpaca bars payload was not an object",
                )
            symbol_rows = grouped.get(symbol) or []
            if not isinstance(symbol_rows, list):
                raise_unavailable(
                    symbol=symbol,
                    provider=self.name,
                    operation="daily_bars",
                    timestamp=as_of,
                    reason="alpaca symbol bars were not a list",
                )
            rows.extend(symbol_rows)
            token = body.get("next_page_token")
            if not token:
                break
            page_token = str(token)
        return rows


def daily_bar_from_alpaca(
    symbol: str,
    raw: dict[str, object],
    calendar: NYSECalendar,
) -> DailyBar:
    started = parse_dt(raw["t"], "t")
    session_date = started.astimezone(_NEW_YORK).date()
    if not calendar.is_trading_day(session_date):
        raise ValueError(f"{symbol} daily bar falls on non-trading day {session_date.isoformat()}")
    available_at = calendar.session_close(session_date)
    return DailyBar(
        symbol=symbol,
        session_date=session_date,
        open=float(raw["o"]),
        high=float(raw["h"]),
        low=float(raw["l"]),
        close=float(raw["c"]),
        volume=float(raw["v"]),
        event_time=available_at,
        published_at=available_at,
        available_at=available_at,
        source="alpaca",
    )
