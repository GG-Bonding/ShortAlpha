"""Static universe file. It does not claim historical index membership."""

from datetime import datetime
from pathlib import Path

from shortalpha.domain import UniverseList, UniverseMember
from shortalpha.errors import FixtureError
from shortalpha.providers.parsing import load_object, parse_date


class FixtureUniverseProvider:
    name = "fixture"

    def __init__(self, universe: UniverseList) -> None:
        self._universe = universe

    @classmethod
    def from_json(cls, path: Path) -> "FixtureUniverseProvider":
        payload = load_object(path)
        if not isinstance(payload.get("point_in_time_membership"), bool):
            raise FixtureError(f"{path} must set point_in_time_membership")
        raw_symbols = payload.get("symbols")
        if not isinstance(raw_symbols, list):
            raise FixtureError(f"{path} is missing symbols")
        members: list[UniverseMember] = []
        for raw in raw_symbols:
            if not isinstance(raw, dict):
                raise FixtureError(f"{path} has a symbol that is not an object")
            sources = raw.get("sources")
            if not isinstance(sources, list) or not all(isinstance(item, str) for item in sources):
                raise FixtureError(f"{path} symbol sources must be a list of strings")
            try:
                members.append(
                    UniverseMember(
                        symbol=str(raw["symbol"]),
                        sources=tuple(sorted(set(sources))),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise FixtureError(f"invalid universe member in {path}: {exc}") from exc
        members.sort(key=lambda member: member.symbol)
        return cls(
            UniverseList(
                list_as_of=parse_date(payload.get("list_as_of"), "list_as_of"),
                point_in_time_membership=payload["point_in_time_membership"],
                members=tuple(members),
            )
        )

    def load(self, as_of: datetime) -> UniverseList:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        return self._universe
