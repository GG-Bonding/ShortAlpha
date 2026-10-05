"""One interpretable change: scale a single event rule's contribution."""

import json
from dataclasses import dataclass, replace

from shortalpha.event_rules import EventRules, EventTypeRule


@dataclass(frozen=True)
class EventScale:
    rule_id: str
    scale: float

    def __post_init__(self) -> None:
        if not self.rule_id:
            raise ValueError("rule_id is required")
        if self.scale <= 0 or self.scale == 1:
            raise ValueError("scale must be positive and must change the contribution")


def scale_event_contribution(rules: EventRules, change: EventScale) -> EventRules:
    found: EventTypeRule | None = None
    updated: list[EventTypeRule] = []
    for rule in rules.types:
        if rule.id != change.rule_id:
            updated.append(rule)
            continue
        if found is not None:
            raise ValueError(f"duplicate event rule {change.rule_id}")
        importance = rule.importance * change.scale
        if not 0 <= importance <= 1:
            raise ValueError("scaled importance is outside 0..1")
        found = replace(rule, importance=importance)
        updated.append(found)
    if found is None:
        raise ValueError(f"unknown event rule {change.rule_id}")
    return EventRules(types=tuple(updated))


def event_scale_from_json(text: str) -> EventScale:
    body = json.loads(text)
    if not isinstance(body, dict) or body.get("kind") != "event_contribution":
        raise ValueError("experiment change must be an event contribution")
    return EventScale(str(body["rule_id"]), float(body["scale"]))


def candidate_version(baseline: str, change: EventScale) -> str:
    scale = f"{change.scale:g}"
    return f"{baseline}.event.{change.rule_id}.x{scale}"
