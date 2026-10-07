"""Domain records shared by storage, providers, and later factor code."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class MarketRegime(Enum):
    NORMAL = "NORMAL"
    RISK_OFF = "RISK_OFF"
    EXTREME_RISK_OFF = "EXTREME_RISK_OFF"


class SignalLabel(Enum):
    LONG_CANDIDATE = "LONG_CANDIDATE"
    WATCH = "WATCH"
    PASS = "PASS"


class EventRisk(Enum):
    NONE = "NONE"
    SEVERE_NEGATIVE = "SEVERE_NEGATIVE"


@dataclass(frozen=True)
class ClassifiedEvent:
    news_id: str
    rule_id: str
    label: str
    family: str
    source: str
    published_at: datetime
    available_at: datetime
    content_sha256: str
    rules_sha256: str
    signed: float
    direction: float
    importance: float
    freshness: float
    reaction: float | None = None

    def __post_init__(self) -> None:
        if not self.news_id or not self.rule_id or not self.label:
            raise ValueError("classified event needs an id, rule, and label")
        ensure_aware("published_at", self.published_at)
        ensure_aware("available_at", self.available_at)
        if self.published_at > self.available_at:
            raise ValueError("published_at must be <= available_at")


@dataclass(frozen=True)
class Split:
    symbol: str
    ex_date: date
    old_rate: float
    new_rate: float

    def __post_init__(self) -> None:
        validate_symbol(self.symbol)
        if self.old_rate <= 0 or self.new_rate <= 0:
            raise ValueError("split rates must be positive")


@dataclass(frozen=True)
class FactorResult:
    name: str
    raw_value: float | None
    normalized_value: float | None
    score: float | None
    available: bool
    degraded: bool = False
    reasons: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    details: tuple[tuple[str, str], ...] = ()
    events: tuple[ClassifiedEvent, ...] = ()


def ensure_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def validate_symbol(symbol: str) -> None:
    if not _SYMBOL.fullmatch(symbol):
        raise ValueError(f"invalid symbol: {symbol}")


def validate_timeline(event_time: datetime, published_at: datetime, available_at: datetime) -> None:
    ensure_aware("event_time", event_time)
    ensure_aware("published_at", published_at)
    ensure_aware("available_at", available_at)
    if event_time > published_at:
        raise ValueError("event_time must be <= published_at")
    if published_at > available_at:
        raise ValueError("published_at must be <= available_at")


@dataclass(frozen=True)
class DailyBar:
    symbol: str
    session_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    event_time: datetime
    published_at: datetime
    available_at: datetime
    source: str

    def __post_init__(self) -> None:
        validate_symbol(self.symbol)
        validate_timeline(self.event_time, self.published_at, self.available_at)
        if not self.source:
            raise ValueError("source is required")
        prices = (self.open, self.high, self.low, self.close)
        if any(price <= 0 for price in prices):
            raise ValueError(f"{self.symbol} prices must be positive")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError(f"{self.symbol} high is below another price")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError(f"{self.symbol} low is above another price")
        if self.volume < 0:
            raise ValueError(f"{self.symbol} volume must be >= 0")


@dataclass(frozen=True)
class NewsItem:
    id: str
    symbols: tuple[str, ...]
    headline: str
    summary: str
    source: str
    event_time: datetime
    published_at: datetime
    available_at: datetime
    url: str | None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("news id is required")
        if not self.headline:
            raise ValueError("headline is required")
        if not self.source:
            raise ValueError("source is required")
        if not self.symbols:
            raise ValueError("news must name at least one symbol")
        for symbol in self.symbols:
            validate_symbol(symbol)
        validate_timeline(self.event_time, self.published_at, self.available_at)


@dataclass(frozen=True)
class UniverseMember:
    symbol: str
    sources: tuple[str, ...]
    name: str = ""

    def __post_init__(self) -> None:
        validate_symbol(self.symbol)
        if not self.sources:
            raise ValueError(f"{self.symbol} needs a universe source")


@dataclass(frozen=True)
class UniverseList:
    list_as_of: date
    point_in_time_membership: bool
    members: tuple[UniverseMember, ...]
    source_as_of: tuple[tuple[str, date], ...] = ()


@dataclass(frozen=True)
class PremarketWindow:
    symbol: str
    session_date: date
    available: bool
    volume: float | None
    last_price: float | None
    high: float | None
    low: float | None
    event_time: datetime | None
    published_at: datetime | None
    available_at: datetime | None
    reason: str
    price_consolidated: bool = True

    def __post_init__(self) -> None:
        validate_symbol(self.symbol)
        if self.last_price is not None and self.last_price <= 0:
            raise ValueError(f"{self.symbol} premarket last price is required")
        if self.last_price is not None:
            if self.event_time is None or self.published_at is None or self.available_at is None:
                raise ValueError(f"{self.symbol} premarket price is missing timestamps")
            validate_timeline(self.event_time, self.published_at, self.available_at)
        if self.available:
            if self.event_time is None or self.published_at is None or self.available_at is None:
                raise ValueError(f"{self.symbol} premarket observation is missing timestamps")
            validate_timeline(self.event_time, self.published_at, self.available_at)
            if self.volume is None or self.volume < 0:
                raise ValueError(f"{self.symbol} premarket volume is required")
            if self.volume > 0 and self.last_price is None:
                raise ValueError(f"{self.symbol} premarket last price is required")
            if self.volume == 0 and self.last_price is None and not self.reason:
                raise ValueError(f"{self.symbol} empty premarket window needs a reason")
        elif not self.reason:
            raise ValueError(f"{self.symbol} unavailable premarket needs a reason")


@dataclass(frozen=True)
class SignalRun:
    run_id: str
    signal_date: date
    signal_time: datetime
    timezone: str
    config_hash: str
    code_version: str
    created_at: datetime
    universe_size: int | None
    eligible_size: int | None
    scored_size: int | None
    candidate_size: int | None
    duration_ms: int | None
    provider_errors: int
    missing_data_count: int
    market_regime: MarketRegime | None
    no_trade: bool
    status: str
    universe_list_as_of: date | None
    point_in_time_membership: bool
    notes: str
    strategy_version: str = ""
    run_mode: str = ""
    event_rules_hash: str = ""
    experiment_id: str = ""

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("run_id is required")
        ensure_aware("signal_time", self.signal_time)
        ensure_aware("created_at", self.created_at)
        if not self.status:
            raise ValueError("status is required")
        if self.run_mode and self.run_mode not in {"live", "replay", "shadow"}:
            raise ValueError("run_mode must be live, replay, or shadow")
