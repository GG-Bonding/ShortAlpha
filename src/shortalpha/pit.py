"""Availability gate used before any record reaches a factor."""

from datetime import datetime
from typing import Protocol, TypeVar


class _Available(Protocol):
    available_at: datetime


T = TypeVar("T", bound=_Available)


def select_available(items: list[T], as_of: datetime) -> list[T]:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    kept: list[T] = []
    for item in items:
        available_at = item.available_at
        if available_at.tzinfo is None or available_at.utcoffset() is None:
            raise ValueError("available_at must be timezone-aware")
        if available_at <= as_of:
            kept.append(item)
    return kept
