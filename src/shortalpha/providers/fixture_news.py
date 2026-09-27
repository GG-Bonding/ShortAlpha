"""News from a JSON fixture. A symbol with no articles is an empty success."""

from datetime import datetime
from pathlib import Path

from shortalpha.domain import NewsItem
from shortalpha.errors import FixtureError
from shortalpha.pit import select_available
from shortalpha.providers.parsing import load_object, parse_dt


class FixtureNewsProvider:
    name = "fixture"

    def __init__(self, items: list[NewsItem]) -> None:
        self._items = list(items)

    @classmethod
    def from_json(cls, path: Path) -> "FixtureNewsProvider":
        payload = load_object(path)
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raise FixtureError(f"{path} is missing items")
        items: list[NewsItem] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                raise FixtureError(f"{path} has a news item that is not an object")
            symbols = raw.get("symbols")
            if not isinstance(symbols, list) or not all(isinstance(item, str) for item in symbols):
                raise FixtureError(f"{path} news symbols must be a list of strings")
            url = raw.get("url")
            if url is not None and not isinstance(url, str):
                raise FixtureError(f"{path} news url must be a string or null")
            try:
                items.append(
                    NewsItem(
                        id=str(raw["id"]),
                        symbols=tuple(symbols),
                        headline=str(raw["headline"]),
                        summary=str(raw.get("summary", "")),
                        source=str(raw["source"]),
                        event_time=parse_dt(raw["event_time"], "event_time"),
                        published_at=parse_dt(raw["published_at"], "published_at"),
                        available_at=parse_dt(raw["available_at"], "available_at"),
                        url=url,
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise FixtureError(f"invalid news item in {path}: {exc}") from exc
        return cls(items)

    def news(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        as_of: datetime,
    ) -> list[NewsItem]:
        matched = [
            item
            for item in self._items
            if symbol in item.symbols and start <= item.published_at <= end
        ]
        return sorted(select_available(matched, as_of), key=lambda item: item.published_at)
