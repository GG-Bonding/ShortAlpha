"""Daily bars from a JSON fixture, filtered at the signal time."""

from datetime import date, datetime
from pathlib import Path

from shortalpha.domain import DailyBar, MinutePrice
from shortalpha.errors import FixtureError
from shortalpha.logging_utils import raise_unavailable
from shortalpha.pit import select_available
from shortalpha.providers.parsing import load_object, parse_date, parse_dt


class FixtureMarketDataProvider:
    name = "fixture"

    def __init__(self, bars: list[DailyBar], prices: list[MinutePrice] | None = None) -> None:
        self._bars = list(bars)
        self._prices = list(prices or [])
        self._known = {bar.symbol for bar in self._bars}

    @classmethod
    def from_json(cls, path: Path) -> "FixtureMarketDataProvider":
        payload = load_object(path)
        raw_bars = payload.get("bars")
        if not isinstance(raw_bars, list):
            raise FixtureError(f"{path} is missing bars")
        bars: list[DailyBar] = []
        for raw in raw_bars:
            if not isinstance(raw, dict):
                raise FixtureError(f"{path} has a bar that is not an object")
            try:
                bars.append(
                    DailyBar(
                        symbol=str(raw["symbol"]),
                        session_date=parse_date(raw["session_date"], "session_date"),
                        open=float(raw["open"]),
                        high=float(raw["high"]),
                        low=float(raw["low"]),
                        close=float(raw["close"]),
                        volume=float(raw["volume"]),
                        event_time=parse_dt(raw["event_time"], "event_time"),
                        published_at=parse_dt(raw["published_at"], "published_at"),
                        available_at=parse_dt(raw["available_at"], "available_at"),
                        source=str(raw["source"]),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise FixtureError(f"invalid bar in {path}: {exc}") from exc
        return cls(bars)

    def daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        as_of: datetime,
    ) -> list[DailyBar]:
        if symbol not in self._known:
            raise_unavailable(
                symbol=symbol,
                provider=self.name,
                operation="daily_bars",
                timestamp=as_of,
                reason=f"no daily bars for {symbol}",
            )
        matched = [
            bar for bar in self._bars if bar.symbol == symbol and start <= bar.session_date <= end
        ]
        return sorted(select_available(matched, as_of), key=lambda bar: bar.session_date)

    def price_at(self, symbol: str, news_at: datetime, as_of: datetime) -> MinutePrice | None:
        if news_at.tzinfo is None or as_of.tzinfo is None:
            raise ValueError("news_at and as_of must be timezone-aware")
        visible = [
            item
            for item in self._prices
            if item.symbol == symbol and item.available_at <= news_at and item.available_at <= as_of
        ]
        if not visible:
            return None
        return max(visible, key=lambda item: item.available_at)
