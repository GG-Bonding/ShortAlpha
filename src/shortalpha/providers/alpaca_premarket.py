"""Alpaca premarket windows.

Free IEX is not a consolidated premarket tape. Its volume stays unavailable and
the daily volume ratio is used instead. A last IEX print is still returned with
its timestamp so a later check can tell whether that price is after the news.
SIP minute bars are available at the end of the minute. The comparable window
is 04:00 through the signal clock on that session, never the rest of the day.
"""

import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from shortalpha.domain import PremarketWindow
from shortalpha.errors import ConfigError, FixtureError
from shortalpha.logging_utils import raise_unavailable
from shortalpha.providers.parsing import parse_dt

_NEW_YORK = ZoneInfo("America/New_York")
_IEX_REASON = "IEX feed is not consolidated premarket; premarket_volume_available is false"


class AlpacaPremarketProvider:
    name = "alpaca"

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        feed: str,
        base_url: str,
        signal_clock: time,
        premarket_start: time,
        timezone: ZoneInfo,
        client: httpx.Client,
        owns_client: bool,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._feed = feed
        self._base_url = base_url.rstrip("/")
        self._signal_clock = signal_clock
        self._premarket_start = premarket_start
        self._timezone = timezone
        self._client = client
        self._owns_client = owns_client

    @classmethod
    def from_env(
        cls,
        *,
        feed: str,
        base_url: str,
        signal_clock: time,
        premarket_start: time,
        timezone: str,
        client: httpx.Client | None = None,
    ) -> "AlpacaPremarketProvider":
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
            signal_clock=signal_clock,
            premarket_start=premarket_start,
            timezone=ZoneInfo(timezone),
            client=client,
            owns_client=owns_client,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def window(self, symbol: str, session: date, as_of: datetime) -> PremarketWindow:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        window_start = datetime.combine(session, self._premarket_start, tzinfo=self._timezone)
        window_end = datetime.combine(session, self._signal_clock, tzinfo=self._timezone)
        consolidated = self._feed == "sip"
        if as_of < window_start:
            return _unavailable(symbol, session, "premarket window has not started")
        if as_of < window_end:
            window_end = as_of
        rows = self._fetch_rows(symbol, window_start, window_end, as_of)
        included = []
        for raw in rows:
            try:
                started = parse_dt(raw["t"], "t")
                volume = float(raw["v"])
                close = float(raw["c"])
                high = float(raw["h"])
                low = float(raw["l"])
            except (KeyError, TypeError, ValueError, FixtureError) as exc:
                raise_unavailable(
                    symbol=symbol,
                    provider=self.name,
                    operation="premarket",
                    timestamp=as_of,
                    reason=str(exc),
                )
            ended = started + timedelta(minutes=1)
            if started < window_start or ended > window_end:
                continue
            included.append((started, ended, volume, close, high, low))
        if not included:
            if not consolidated:
                return _unavailable(symbol, session, _IEX_REASON)
            return PremarketWindow(
                symbol=symbol,
                session_date=session,
                available=True,
                volume=0,
                last_price=None,
                high=None,
                low=None,
                event_time=window_end,
                published_at=window_end,
                available_at=window_end,
                reason="no premarket prints",
            )
        included.sort(key=lambda item: item[0])
        last = included[-1]
        if not consolidated:
            return PremarketWindow(
                symbol=symbol,
                session_date=session,
                available=False,
                volume=None,
                last_price=last[3],
                high=max(item[4] for item in included),
                low=min(item[5] for item in included),
                event_time=last[1],
                published_at=last[1],
                available_at=last[1],
                reason=_IEX_REASON,
                price_consolidated=False,
            )
        return PremarketWindow(
            symbol=symbol,
            session_date=session,
            available=True,
            volume=sum(item[2] for item in included),
            last_price=last[3],
            high=max(item[4] for item in included),
            low=min(item[5] for item in included),
            event_time=last[1],
            published_at=last[1],
            available_at=last[1],
            reason="",
        )

    def _fetch_rows(
        self,
        symbol: str,
        window_start: datetime,
        window_end: datetime,
        as_of: datetime,
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        page_token: str | None = None
        while True:
            params: dict[str, str] = {
                "symbols": symbol,
                "timeframe": "1Min",
                "start": window_start.isoformat(),
                "end": window_end.isoformat(),
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
                    operation="premarket",
                    timestamp=as_of,
                    reason=exc.__class__.__name__,
                )
            if response.status_code >= 400:
                raise_unavailable(
                    symbol=symbol,
                    provider=self.name,
                    operation="premarket",
                    timestamp=as_of,
                    reason=f"HTTP {response.status_code}: {response.text[:200]}",
                )
            body = response.json()
            if not isinstance(body, dict):
                raise_unavailable(
                    symbol=symbol,
                    provider=self.name,
                    operation="premarket",
                    timestamp=as_of,
                    reason="alpaca response was not an object",
                )
            grouped = body.get("bars") or {}
            if not isinstance(grouped, dict):
                raise_unavailable(
                    symbol=symbol,
                    provider=self.name,
                    operation="premarket",
                    timestamp=as_of,
                    reason="alpaca bars payload was not an object",
                )
            symbol_rows = grouped.get(symbol) or []
            if not isinstance(symbol_rows, list):
                raise_unavailable(
                    symbol=symbol,
                    provider=self.name,
                    operation="premarket",
                    timestamp=as_of,
                    reason="alpaca symbol bars were not a list",
                )
            rows.extend(symbol_rows)
            token = body.get("next_page_token")
            if not token:
                break
            page_token = str(token)
        return rows


def _unavailable(symbol: str, session: date, reason: str) -> PremarketWindow:
    return PremarketWindow(
        symbol=symbol,
        session_date=session,
        available=False,
        volume=None,
        last_price=None,
        high=None,
        low=None,
        event_time=None,
        published_at=None,
        available_at=None,
        reason=reason,
    )
