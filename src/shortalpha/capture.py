"""Save the inputs of one scan so the same cutoff can be scored again offline."""

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from shortalpha.domain import (
    DailyBar,
    MinutePrice,
    NewsItem,
    PremarketWindow,
    Split,
    UniverseList,
    UniverseMember,
)
from shortalpha.errors import DataUnavailableError
from shortalpha.logging_utils import raise_unavailable
from shortalpha.providers.base import MarketDataProvider, NewsProvider, PreMarketDataProvider
from shortalpha.providers.fixture_market import FixtureMarketDataProvider
from shortalpha.providers.fixture_news import FixtureNewsProvider
from shortalpha.providers.fixture_premarket import FixturePreMarketProvider
from shortalpha.providers.fixture_universe import FixtureUniverseProvider

SplitsFn = Callable[[str, date, date, datetime], tuple[Split, ...]]


def failure_kind(reason: str) -> str:
    """Separate permission errors, rate limits, and broken requests from missing data."""
    text = reason.casefold()
    if any(token in text for token in ("http 401", "http 403", "unauthorized", "forbidden")):
        return "auth"
    if any(token in text for token in ("http 429", "rate limit", "too many requests")):
        return "rate_limit"
    if text.startswith("http ") or any(
        token in text
        for token in ("connecterror", "connecttimeout", "readtimeout", "timeout", "invalid symbol")
    ):
        return "request"
    return "data"


def git_revision(root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return completed.stdout.strip() or "unknown"


@dataclass
class Failure:
    symbol: str
    provider: str
    operation: str
    timestamp: datetime
    reason: str
    kind: str


@dataclass
class Capture:
    git_revision: str = ""
    bars: list[DailyBar] = field(default_factory=list)
    prices: list[MinutePrice] = field(default_factory=list)
    windows: list[PremarketWindow] = field(default_factory=list)
    news: list[NewsItem] = field(default_factory=list)
    splits: list[Split] = field(default_factory=list)
    bar_symbols: set[str] = field(default_factory=set)
    failures: list[Failure] = field(default_factory=list)
    gaps: list[tuple[str, str]] = field(default_factory=list)
    universe: UniverseList | None = None
    _bar_keys: set[tuple[str, date]] = field(default_factory=set)
    _price_keys: set[tuple[str, datetime]] = field(default_factory=set)
    _window_keys: set[tuple[str, date, str]] = field(default_factory=set)
    _news_ids: set[str] = field(default_factory=set)
    _split_keys: set[tuple[str, date]] = field(default_factory=set)

    def remember_universe(self, loaded: UniverseList) -> None:
        self.universe = loaded

    def add_bars(self, symbol: str, bars: list[DailyBar]) -> None:
        self.bar_symbols.add(symbol)
        for bar in bars:
            key = (bar.symbol, bar.session_date)
            if key in self._bar_keys:
                continue
            self._bar_keys.add(key)
            self.bars.append(bar)

    def add_price(self, printed: MinutePrice) -> None:
        key = (printed.symbol, printed.available_at)
        if key in self._price_keys:
            return
        self._price_keys.add(key)
        self.prices.append(printed)

    def add_window(self, window: PremarketWindow) -> None:
        stamp = window.available_at.isoformat() if window.available_at is not None else ""
        key = (window.symbol, window.session_date, stamp)
        if key in self._window_keys:
            return
        self._window_keys.add(key)
        self.windows.append(window)

    def add_news(self, items: list[NewsItem]) -> None:
        for item in items:
            if item.id in self._news_ids:
                continue
            self._news_ids.add(item.id)
            self.news.append(item)

    def add_splits(self, rows: tuple[Split, ...] | list[Split]) -> None:
        for row in rows:
            key = (row.symbol, row.ex_date)
            if key in self._split_keys:
                continue
            self._split_keys.add(key)
            self.splits.append(row)

    def add_error(self, exc: DataUnavailableError) -> None:
        self.failures.append(
            Failure(
                symbol=exc.symbol,
                provider=exc.provider,
                operation=exc.operation,
                timestamp=exc.timestamp,
                reason=exc.reason,
                kind=failure_kind(exc.reason),
            )
        )

    def add_price_failure(self, symbol: str, as_of: datetime, reason: str) -> None:
        self.failures.append(
            Failure(
                symbol=symbol,
                provider="alpaca",
                operation="minute_price",
                timestamp=as_of,
                reason=reason,
                kind=failure_kind(reason),
            )
        )

    def add_gap(self, symbol: str, reason: str) -> None:
        self.gaps.append((symbol, reason))

    def to_json(self) -> str:
        if self.universe is None:
            raise ValueError("scan inputs are missing the universe")
        document = {
            "bar_symbols": sorted(self.bar_symbols),
            "bars": [_bar(bar) for bar in self.bars],
            "failures": [_failure(item) for item in self.failures],
            "gaps": [{"reason": reason, "symbol": symbol} for symbol, reason in self.gaps],
            "git_revision": self.git_revision,
            "items": [_news(item) for item in self.news],
            "prices": [_price(item) for item in self.prices],
            "splits": [_split(item) for item in self.splits],
            "universe": _universe(self.universe),
            "windows": [_window(item) for item in self.windows],
        }
        return json.dumps(document, sort_keys=True, separators=(",", ":"))


def capture_from_json(text: str) -> Capture:
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("scan inputs must be an object")
    capture = Capture(git_revision=str(payload.get("git_revision") or ""))
    bars = [_bar_from(raw) for raw in _list(payload, "bars")]
    capture.bars = bars
    capture.bar_symbols = set(bars_symbols(payload))
    capture.prices = [_price_from(raw) for raw in _list(payload, "prices")]
    capture.windows = [_window_from(raw) for raw in _list(payload, "windows")]
    capture.news = [_news_from(raw) for raw in _list(payload, "items")]
    capture.splits = [_split_from(raw) for raw in _list(payload, "splits")]
    capture.failures = [_failure_from(raw) for raw in _list(payload, "failures")]
    capture.gaps = [
        (str(raw["symbol"]), str(raw["reason"]))
        for raw in _list(payload, "gaps")
        if isinstance(raw, dict)
    ]
    capture.universe = _universe_from(payload.get("universe"))
    return capture


def providers_from_capture(
    capture: Capture,
) -> tuple[
    FixtureMarketDataProvider,
    FixturePreMarketProvider,
    FixtureNewsProvider,
    FixtureUniverseProvider,
    SplitsFn,
]:
    if capture.universe is None:
        raise ValueError("scan inputs are missing the universe")
    failed_news = {item.symbol for item in capture.failures if item.operation == "news"}
    failed_splits = {
        item.symbol for item in capture.failures if item.operation == "corporate_actions"
    }
    return (
        FixtureMarketDataProvider(capture.bars, capture.prices, known=set(capture.bar_symbols)),
        FixturePreMarketProvider(capture.windows),
        _ReplayNews(capture.news, failed_news),
        FixtureUniverseProvider(capture.universe),
        _replay_splits(capture.splits, failed_splits),
    )


class RecordingMarket:
    def __init__(self, inner: MarketDataProvider, capture: Capture) -> None:
        self._inner = inner
        self._capture = capture
        self.name = inner.name

    def daily_bars(self, symbol: str, start: date, end: date, as_of: datetime) -> list[DailyBar]:
        try:
            bars = self._inner.daily_bars(symbol, start, end, as_of)
        except DataUnavailableError as exc:
            self._capture.add_error(exc)
            raise
        self._capture.add_bars(symbol, bars)
        return bars

    def price_at(self, symbol: str, news_at: datetime, as_of: datetime) -> MinutePrice | None:
        printed = self._inner.price_at(symbol, news_at, as_of)
        reason = getattr(self._inner, "last_price_failure", None)
        if isinstance(reason, str) and reason:
            self._capture.add_price_failure(symbol, as_of, reason)
        if printed is not None:
            self._capture.add_price(printed)
        return printed

    def close(self) -> None:
        closer = getattr(self._inner, "close", None)
        if closer is not None:
            closer()


class RecordingPremarket:
    def __init__(self, inner: PreMarketDataProvider, capture: Capture) -> None:
        self._inner = inner
        self._capture = capture
        self.name = inner.name

    def window(self, symbol: str, session: date, as_of: datetime) -> PremarketWindow:
        try:
            window = self._inner.window(symbol, session, as_of)
        except DataUnavailableError as exc:
            self._capture.add_error(exc)
            raise
        self._capture.add_window(window)
        return window

    def close(self) -> None:
        closer = getattr(self._inner, "close", None)
        if closer is not None:
            closer()


class RecordingNews:
    def __init__(self, inner: NewsProvider, capture: Capture) -> None:
        self._inner = inner
        self._capture = capture
        self.name = inner.name

    def news(self, symbol: str, start: datetime, end: datetime, as_of: datetime) -> list[NewsItem]:
        try:
            items = self._inner.news(symbol, start, end, as_of)
        except DataUnavailableError as exc:
            self._capture.add_error(exc)
            raise
        self._capture.add_news(items)
        return items

    def close(self) -> None:
        closer = getattr(self._inner, "close", None)
        if closer is not None:
            closer()


class RecordingSplits:
    def __init__(self, inner: SplitsFn, capture: Capture) -> None:
        self._inner = inner
        self._capture = capture

    def __call__(self, symbol: str, start: date, end: date, as_of: datetime) -> tuple[Split, ...]:
        try:
            rows = self._inner(symbol, start, end, as_of)
        except DataUnavailableError as exc:
            self._capture.add_error(exc)
            raise
        self._capture.add_splits(rows)
        return rows


def recorded(
    market: MarketDataProvider,
    premarket: PreMarketDataProvider,
    news: NewsProvider,
    splits_for: SplitsFn,
) -> tuple[RecordingMarket, RecordingPremarket, RecordingNews, RecordingSplits, Capture]:
    capture = Capture()
    return (
        RecordingMarket(market, capture),
        RecordingPremarket(premarket, capture),
        RecordingNews(news, capture),
        RecordingSplits(splits_for, capture),
        capture,
    )


class _ReplayNews(FixtureNewsProvider):
    def __init__(self, items: list[NewsItem], failed: set[str]) -> None:
        super().__init__(items)
        self._failed = failed
        self.name = "replay"

    def news(self, symbol: str, start: datetime, end: datetime, as_of: datetime) -> list[NewsItem]:
        if symbol in self._failed:
            raise_unavailable(
                symbol=symbol,
                provider=self.name,
                operation="news",
                timestamp=as_of,
                reason="saved scan recorded a news request failure",
            )
        return super().news(symbol, start, end, as_of)


def _replay_splits(rows: list[Split], failed: set[str]) -> SplitsFn:
    def splits_for(symbol: str, start: date, end: date, as_of: datetime) -> tuple[Split, ...]:
        if symbol in failed:
            raise_unavailable(
                symbol=symbol,
                provider="replay",
                operation="corporate_actions",
                timestamp=as_of,
                reason="saved scan recorded a corporate-action request failure",
            )
        return tuple(
            item for item in rows if item.symbol == symbol and start <= item.ex_date <= end
        )

    return splits_for


def bars_symbols(payload: dict[str, object]) -> list[str]:
    raw = payload.get("bar_symbols")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw]


def _list(payload: dict[str, object], key: str) -> list[object]:
    raw = payload.get(key, [])
    if not isinstance(raw, list):
        raise ValueError(f"scan inputs {key} must be a list")
    return raw


def _bar(bar: DailyBar) -> dict[str, object]:
    return {
        "available_at": bar.available_at.isoformat(),
        "close": bar.close,
        "event_time": bar.event_time.isoformat(),
        "high": bar.high,
        "low": bar.low,
        "open": bar.open,
        "published_at": bar.published_at.isoformat(),
        "session_date": bar.session_date.isoformat(),
        "source": bar.source,
        "symbol": bar.symbol,
        "volume": bar.volume,
    }


def _price(item: MinutePrice) -> dict[str, object]:
    return {
        "available_at": item.available_at.isoformat(),
        "consolidated": item.consolidated,
        "price": item.price,
        "symbol": item.symbol,
    }


def _window(item: PremarketWindow) -> dict[str, object]:
    return {
        "available": item.available,
        "available_at": _optional_time(item.available_at),
        "event_time": _optional_time(item.event_time),
        "high": item.high,
        "last_price": item.last_price,
        "low": item.low,
        "price_consolidated": item.price_consolidated,
        "published_at": _optional_time(item.published_at),
        "reason": item.reason,
        "session_date": item.session_date.isoformat(),
        "symbol": item.symbol,
        "volume": item.volume,
    }


def _news(item: NewsItem) -> dict[str, object]:
    return {
        "available_at": item.available_at.isoformat(),
        "event_time": item.event_time.isoformat(),
        "headline": item.headline,
        "id": item.id,
        "published_at": item.published_at.isoformat(),
        "source": item.source,
        "summary": item.summary,
        "symbols": list(item.symbols),
        "url": item.url,
    }


def _split(item: Split) -> dict[str, object]:
    return {
        "ex_date": item.ex_date.isoformat(),
        "new_rate": item.new_rate,
        "old_rate": item.old_rate,
        "symbol": item.symbol,
    }


def _failure(item: Failure) -> dict[str, object]:
    return {
        "kind": item.kind,
        "operation": item.operation,
        "provider": item.provider,
        "reason": item.reason,
        "symbol": item.symbol,
        "timestamp": item.timestamp.isoformat(),
    }


def _universe(loaded: UniverseList) -> dict[str, object]:
    return {
        "list_as_of": loaded.list_as_of.isoformat(),
        "point_in_time_membership": loaded.point_in_time_membership,
        "source_as_of": [
            {"as_of": day.isoformat(), "name": name} for name, day in loaded.source_as_of
        ],
        "symbols": [
            {"name": member.name, "sources": list(member.sources), "symbol": member.symbol}
            for member in loaded.members
        ],
    }


def _optional_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _parse_time(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a timestamp")
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return stamp


def _optional_stamp(value: object, label: str) -> datetime | None:
    if value is None:
        return None
    return _parse_time(value, label)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)  # type: ignore[arg-type]


def _bar_from(raw: object) -> DailyBar:
    if not isinstance(raw, dict):
        raise ValueError("a saved bar must be an object")
    return DailyBar(
        symbol=str(raw["symbol"]),
        session_date=date.fromisoformat(str(raw["session_date"])),
        open=float(raw["open"]),
        high=float(raw["high"]),
        low=float(raw["low"]),
        close=float(raw["close"]),
        volume=float(raw["volume"]),
        event_time=_parse_time(raw["event_time"], "event_time"),
        published_at=_parse_time(raw["published_at"], "published_at"),
        available_at=_parse_time(raw["available_at"], "available_at"),
        source=str(raw["source"]),
    )


def _price_from(raw: object) -> MinutePrice:
    if not isinstance(raw, dict):
        raise ValueError("a saved minute price must be an object")
    return MinutePrice(
        symbol=str(raw["symbol"]),
        price=float(raw["price"]),
        available_at=_parse_time(raw["available_at"], "available_at"),
        consolidated=bool(raw["consolidated"]),
    )


def _window_from(raw: object) -> PremarketWindow:
    if not isinstance(raw, dict):
        raise ValueError("a saved premarket window must be an object")
    return PremarketWindow(
        symbol=str(raw["symbol"]),
        session_date=date.fromisoformat(str(raw["session_date"])),
        available=bool(raw["available"]),
        volume=_optional_float(raw.get("volume")),
        last_price=_optional_float(raw.get("last_price")),
        high=_optional_float(raw.get("high")),
        low=_optional_float(raw.get("low")),
        event_time=_optional_stamp(raw.get("event_time"), "event_time"),
        published_at=_optional_stamp(raw.get("published_at"), "published_at"),
        available_at=_optional_stamp(raw.get("available_at"), "available_at"),
        reason=str(raw.get("reason") or ""),
        price_consolidated=bool(raw.get("price_consolidated", True)),
    )


def _news_from(raw: object) -> NewsItem:
    if not isinstance(raw, dict):
        raise ValueError("a saved news item must be an object")
    symbols = raw.get("symbols")
    if not isinstance(symbols, list):
        raise ValueError("saved news symbols must be a list")
    url = raw.get("url")
    return NewsItem(
        id=str(raw["id"]),
        symbols=tuple(str(item) for item in symbols),
        headline=str(raw["headline"]),
        summary=str(raw.get("summary") or ""),
        source=str(raw["source"]),
        event_time=_parse_time(raw["event_time"], "event_time"),
        published_at=_parse_time(raw["published_at"], "published_at"),
        available_at=_parse_time(raw["available_at"], "available_at"),
        url=url if isinstance(url, str) else None,
    )


def _split_from(raw: object) -> Split:
    if not isinstance(raw, dict):
        raise ValueError("a saved split must be an object")
    return Split(
        symbol=str(raw["symbol"]),
        ex_date=date.fromisoformat(str(raw["ex_date"])),
        old_rate=float(raw["old_rate"]),
        new_rate=float(raw["new_rate"]),
    )


def _failure_from(raw: object) -> Failure:
    if not isinstance(raw, dict):
        raise ValueError("a saved failure must be an object")
    return Failure(
        symbol=str(raw["symbol"]),
        provider=str(raw["provider"]),
        operation=str(raw["operation"]),
        timestamp=_parse_time(raw["timestamp"], "timestamp"),
        reason=str(raw["reason"]),
        kind=str(raw["kind"]),
    )


def _universe_from(raw: object) -> UniverseList:
    if not isinstance(raw, dict):
        raise ValueError("saved universe must be an object")
    members: list[UniverseMember] = []
    for item in _list(raw, "symbols"):
        if not isinstance(item, dict):
            raise ValueError("saved universe symbol must be an object")
        sources = item.get("sources")
        if not isinstance(sources, list):
            raise ValueError("saved universe sources must be a list")
        members.append(
            UniverseMember(
                symbol=str(item["symbol"]),
                sources=tuple(str(source) for source in sources),
                name=str(item.get("name") or ""),
            )
        )
    source_as_of: list[tuple[str, date]] = []
    for item in raw.get("source_as_of") or []:
        if isinstance(item, dict):
            source_as_of.append((str(item["name"]), date.fromisoformat(str(item["as_of"]))))
    return UniverseList(
        list_as_of=date.fromisoformat(str(raw["list_as_of"])),
        point_in_time_membership=bool(raw["point_in_time_membership"]),
        members=tuple(members),
        source_as_of=tuple(source_as_of),
    )
