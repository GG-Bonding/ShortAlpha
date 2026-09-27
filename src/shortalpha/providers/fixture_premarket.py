"""Premarket windows. Unavailable feeds stay unavailable."""

from datetime import date, datetime
from pathlib import Path

from shortalpha.domain import PremarketWindow
from shortalpha.errors import FixtureError
from shortalpha.logging_utils import raise_unavailable
from shortalpha.providers.parsing import load_object, parse_date, parse_optional_dt


class FixturePreMarketProvider:
    name = "fixture"

    def __init__(self, windows: list[PremarketWindow]) -> None:
        self._windows = list(windows)

    @classmethod
    def from_json(cls, path: Path) -> "FixturePreMarketProvider":
        payload = load_object(path)
        raw_windows = payload.get("windows")
        if not isinstance(raw_windows, list):
            raise FixtureError(f"{path} is missing windows")
        windows: list[PremarketWindow] = []
        for raw in raw_windows:
            if not isinstance(raw, dict):
                raise FixtureError(f"{path} has a window that is not an object")
            try:
                windows.append(
                    PremarketWindow(
                        symbol=str(raw["symbol"]),
                        session_date=parse_date(raw["session_date"], "session_date"),
                        available=bool(raw["available"]),
                        volume=_optional_float(raw.get("volume")),
                        last_price=_optional_float(raw.get("last_price")),
                        high=_optional_float(raw.get("high")),
                        low=_optional_float(raw.get("low")),
                        event_time=parse_optional_dt(raw.get("event_time"), "event_time"),
                        published_at=parse_optional_dt(raw.get("published_at"), "published_at"),
                        available_at=parse_optional_dt(raw.get("available_at"), "available_at"),
                        reason=str(raw.get("reason") or ""),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise FixtureError(f"invalid premarket window in {path}: {exc}") from exc
        return cls(windows)

    def window(self, symbol: str, session: date, as_of: datetime) -> PremarketWindow:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        matches = [
            item for item in self._windows if item.symbol == symbol and item.session_date == session
        ]
        if not matches:
            raise_unavailable(
                symbol=symbol,
                provider=self.name,
                operation="premarket",
                timestamp=as_of,
                reason=f"no premarket row for {symbol}",
            )
        explicit = [item for item in matches if not item.available and item.available_at is None]
        if explicit:
            return explicit[0]
        visible = [
            item
            for item in matches
            if item.available and item.available_at is not None and item.available_at <= as_of
        ]
        if not visible:
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
                reason="no premarket observation at or before signal_time",
            )
        return sorted(visible, key=lambda item: item.available_at or as_of)[-1]


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FixtureError("premarket numeric field must be a number or null")
    return float(value)
