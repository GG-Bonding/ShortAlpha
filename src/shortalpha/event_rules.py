"""Keyword event table. The numbers are a prior, not a fit to later returns."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from shortalpha.errors import ConfigError


@dataclass(frozen=True)
class EventTypeRule:
    id: str
    family: str
    direction: float
    importance: float
    confidence: float
    label: str
    patterns: tuple[str, ...]


@dataclass(frozen=True)
class EventRules:
    types: tuple[EventTypeRule, ...]


_TYPE_KEYS = {"id", "family", "direction", "importance", "confidence", "label", "patterns"}


def event_rules_content_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rules_to_json(rules: EventRules) -> str:
    payload = {
        "types": [
            {
                "confidence": rule.confidence,
                "direction": rule.direction,
                "family": rule.family,
                "id": rule.id,
                "importance": rule.importance,
                "label": rule.label,
                "patterns": list(rule.patterns),
            }
            for rule in rules.types
        ]
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def rules_from_json(text: str) -> EventRules:
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("frozen event rules are not json") from exc
    if not isinstance(loaded, dict) or set(loaded) != {"types"}:
        raise ValueError("frozen event rules must contain only types")
    raw_types = loaded["types"]
    if not isinstance(raw_types, list) or not raw_types:
        raise ValueError("frozen event rules need at least one type")
    types: list[EventTypeRule] = []
    seen: set[str] = set()
    origin = Path("strategy_registry")
    for raw in raw_types:
        types.append(_type_rule(raw, origin, seen))
    return EventRules(types=tuple(types))


def load_event_rules(path: Path) -> EventRules:
    try:
        loaded = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid event rules: {path}") from exc
    if not isinstance(loaded, dict) or set(loaded) != {"types"}:
        raise ConfigError(f"event rules must contain only types: {path}")
    raw_types = loaded["types"]
    if not isinstance(raw_types, list) or not raw_types:
        raise ConfigError(f"event rules need at least one type: {path}")
    types: list[EventTypeRule] = []
    seen: set[str] = set()
    for raw in raw_types:
        types.append(_type_rule(raw, path, seen))
    return EventRules(types=tuple(types))


def _type_rule(raw: object, path: Path, seen: set[str]) -> EventTypeRule:
    if not isinstance(raw, dict) or set(raw) != _TYPE_KEYS:
        raise ConfigError(f"event type keys mismatch: {path}")
    rule_id = _text(raw, "id", path)
    if rule_id in seen:
        raise ConfigError(f"duplicate event type {rule_id}: {path}")
    seen.add(rule_id)
    patterns = raw["patterns"]
    if not isinstance(patterns, list) or not patterns:
        raise ConfigError(f"{rule_id} needs patterns: {path}")
    if not all(isinstance(item, str) and item.strip() for item in patterns):
        raise ConfigError(f"{rule_id} patterns must be non-empty strings: {path}")
    direction = _unit(raw, "direction", path, low=-1, high=1)
    return EventTypeRule(
        id=rule_id,
        family=_text(raw, "family", path),
        direction=direction,
        importance=_unit(raw, "importance", path, low=0, high=1),
        confidence=_unit(raw, "confidence", path, low=0, high=1),
        label=_text(raw, "label", path),
        patterns=tuple(patterns),
    )


def _text(raw: dict[str, Any], key: str, path: Path) -> str:
    value = raw[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"event {key} must be a non-empty string: {path}")
    return value


def _unit(raw: dict[str, Any], key: str, path: Path, *, low: float, high: float) -> float:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"event {key} must be a number: {path}")
    number = float(value)
    if not low <= number <= high:
        raise ConfigError(f"event {key} is out of range: {path}")
    return number
