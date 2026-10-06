"""Index membership and the price / dollar-volume gate.

Membership files are a dated snapshot. They are not the index as it stood on
an earlier signal day. Liquidity uses only bars whose available_at is at or
before the signal time, and only completed sessions before the signal session.
"""

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path

from shortalpha.calendar import NYSECalendar
from shortalpha.domain import DailyBar, UniverseList, UniverseMember, validate_symbol
from shortalpha.errors import FixtureError

LOOKBACK_SESSIONS = 20
_CLASS_SHARE = re.compile(r"^[A-Z]+-[A-Z]$")


class LiquidityStatus(Enum):
    ELIGIBLE = "eligible"
    EXCLUDED = "excluded"
    MISSING = "missing"


@dataclass(frozen=True)
class LiquidityResult:
    symbol: str
    status: LiquidityStatus
    price: float | None
    avg_dollar_volume: float | None
    sessions: int
    price_source: str
    reason: str


class FileUniverseProvider:
    name = "file"

    def __init__(self, universe: UniverseList) -> None:
        self._universe = universe

    def load(self, as_of: datetime) -> UniverseList:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        return self._universe


def load_membership(
    *,
    sources: tuple[str, ...],
    files: dict[str, Path],
    as_of: dict[str, date],
) -> UniverseList:
    grouped: dict[str, set[str]] = {}
    names: dict[str, str] = {}
    for source in sources:
        path = files[source]
        for symbol, name in _read_members(path):
            grouped.setdefault(symbol, set()).add(source)
            names.setdefault(symbol, name)
    members = tuple(
        UniverseMember(
            symbol=symbol,
            sources=tuple(sorted(grouped[symbol])),
            name=names[symbol],
        )
        for symbol in sorted(grouped)
    )
    source_as_of = tuple((source, as_of[source]) for source in sorted(sources))
    return UniverseList(
        list_as_of=min(day for _, day in source_as_of),
        point_in_time_membership=False,
        members=members,
        source_as_of=source_as_of,
    )


def evaluate_liquidity(
    symbol: str,
    bars: list[DailyBar],
    *,
    session: date,
    as_of: datetime,
    calendar: NYSECalendar,
    min_price: float,
    min_avg_dollar_volume: float,
    premarket_price: float | None = None,
) -> LiquidityResult:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if not calendar.is_trading_day(session):
        raise ValueError(f"{session.isoformat()} is not a trading session")
    if premarket_price is not None and premarket_price <= 0:
        raise ValueError("premarket price must be positive")

    needed = [calendar.shift(session, -offset) for offset in range(LOOKBACK_SESSIONS, 0, -1)]
    by_day: dict[date, DailyBar] = {}
    for bar in bars:
        if bar.symbol != symbol or bar.session_date >= session or bar.available_at > as_of:
            continue
        previous = by_day.get(bar.session_date)
        if previous is not None and (previous.close != bar.close or previous.volume != bar.volume):
            raise ValueError(
                f"conflicting daily bars for {symbol} on {bar.session_date.isoformat()}"
            )
        by_day[bar.session_date] = bar

    window = [by_day[day] for day in needed if day in by_day]
    if len(window) < LOOKBACK_SESSIONS:
        return LiquidityResult(
            symbol=symbol,
            status=LiquidityStatus.MISSING,
            price=None,
            avg_dollar_volume=None,
            sessions=len(window),
            price_source="",
            reason=f"insufficient history: {len(window)} < {LOOKBACK_SESSIONS}",
        )

    ordered = [by_day[day] for day in needed]
    avg_dollar = sum(bar.close * bar.volume for bar in ordered) / LOOKBACK_SESSIONS
    if premarket_price is None:
        price = ordered[-1].close
        price_source = "previous_close"
    else:
        price = premarket_price
        price_source = "premarket"
    reasons: list[str] = []
    if price < min_price:
        reasons.append(f"price {format_number(price)} < {format_number(min_price)}")
    if avg_dollar < min_avg_dollar_volume:
        reasons.append(
            "avg_dollar_volume "
            f"{format_number(avg_dollar)} < {format_number(min_avg_dollar_volume)}"
        )
    status = LiquidityStatus.EXCLUDED if reasons else LiquidityStatus.ELIGIBLE
    return LiquidityResult(
        symbol=symbol,
        status=status,
        price=price,
        avg_dollar_volume=avg_dollar,
        sessions=LOOKBACK_SESSIONS,
        price_source=price_source,
        reason="; ".join(reasons),
    )


def format_number(value: float) -> str:
    rounded = round(float(value), 4)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.4f}"


def _read_members(path: Path) -> list[tuple[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or [name.lstrip("\ufeff") for name in reader.fieldnames] != [
            "symbol",
            "name",
        ]:
            raise FixtureError(f"{path} must have columns symbol,name")
        members: list[tuple[str, str]] = []
        seen: set[str] = set()
        for row in reader:
            name = (row.get("name") or "").strip()
            if not name:
                raise FixtureError(f"{path} has a symbol with an empty name")
            symbol = _normalize_symbol(row.get("symbol") or "")
            try:
                validate_symbol(symbol)
            except ValueError as exc:
                raise FixtureError(f"{path}: {exc}") from exc
            if symbol in seen:
                raise FixtureError(f"duplicate symbol {symbol} in {path}")
            seen.add(symbol)
            members.append((symbol, name))
    if not members:
        raise FixtureError(f"{path} has no symbols")
    return members


def _normalize_symbol(raw: str) -> str:
    text = raw.strip().upper()
    if _CLASS_SHARE.fullmatch(text):
        text = text.replace("-", ".", 1)
    if not text:
        raise FixtureError("symbol is empty")
    return text
