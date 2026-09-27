"""Shared fixture parsing."""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from shortalpha.errors import FixtureError


def load_object(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise FixtureError(f"invalid JSON in {path}") from exc
    if not isinstance(loaded, dict):
        raise FixtureError(f"fixture root must be an object: {path}")
    return loaded


def parse_date(value: object, label: str) -> date:
    if not isinstance(value, str):
        raise FixtureError(f"{label} must be a YYYY-MM-DD string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise FixtureError(f"invalid date for {label}: {value}") from exc


def parse_dt(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise FixtureError(f"{label} must be a timestamp string")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise FixtureError(f"invalid timestamp for {label}: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FixtureError(f"{label} must be timezone-aware")
    return parsed


def parse_optional_dt(value: object, label: str) -> datetime | None:
    if value is None:
        return None
    return parse_dt(value, label)
