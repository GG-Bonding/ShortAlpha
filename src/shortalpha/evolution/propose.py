"""Propose one event-contribution change from development labels only."""

from dataclasses import dataclass

from shortalpha.event_rules import EventRules

_SCALE = 0.5
_MIN_GAP = 0.005


@dataclass(frozen=True)
class Proposal:
    rule_id: str
    label: str
    scale: float
    weak_count: int
    strong_label: str
    strong_count: int
    weak_mean_excess: float
    strong_mean_excess: float


def propose_event_scale(
    groups: dict[str, list[float]],
    rules: EventRules,
    *,
    min_count: int,
) -> Proposal | None:
    if min_count < 2:
        raise ValueError("min_count must be at least 2")
    usable: list[tuple[str, str, float, int]] = []
    for label, values in groups.items():
        if len(values) < min_count:
            continue
        rule = _positive_rule(rules, label)
        if rule is None:
            continue
        usable.append((label, rule.id, _mean(values), len(values)))
    if len(usable) < 2:
        return None
    weak = min(usable, key=lambda item: (item[2], item[0]))
    strong = max(usable, key=lambda item: (item[2], item[0]))
    if weak[1] == strong[1] or strong[2] - weak[2] < _MIN_GAP:
        return None
    return Proposal(
        rule_id=weak[1],
        label=weak[0],
        scale=_SCALE,
        weak_count=weak[3],
        strong_label=strong[0],
        strong_count=strong[3],
        weak_mean_excess=weak[2],
        strong_mean_excess=strong[2],
    )


def _positive_rule(rules: EventRules, label: str):
    matches = [rule for rule in rules.types if rule.label == label and rule.direction > 0]
    if len(matches) != 1:
        return None
    return matches[0]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)
