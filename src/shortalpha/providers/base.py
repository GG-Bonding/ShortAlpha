"""Provider boundaries. Factor code depends on these, not on a vendor SDK."""

from datetime import date, datetime
from typing import Protocol

from shortalpha.domain import DailyBar, NewsItem, PremarketWindow, UniverseList


class MarketDataProvider(Protocol):
    name: str

    def daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        as_of: datetime,
    ) -> list[DailyBar]:
        """Bars whose available_at is at or before as_of."""


class PreMarketDataProvider(Protocol):
    name: str

    def window(self, symbol: str, session: date, as_of: datetime) -> PremarketWindow:
        """One premarket observation, or an explicit available=false degradation."""


class NewsProvider(Protocol):
    name: str

    def news(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        as_of: datetime,
    ) -> list[NewsItem]:
        """Published items already available at as_of. An empty list is a successful empty set."""


class UniverseProvider(Protocol):
    name: str

    def load(self, as_of: datetime) -> UniverseList:
        """Membership list. point_in_time_membership reports whether the list is historical."""


class TradingCalendarProvider(Protocol):
    def is_trading_day(self, day: date) -> bool: ...

    def is_early_close(self, day: date) -> bool: ...

    def shift(self, day: date, sessions: int) -> date: ...
