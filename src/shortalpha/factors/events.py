"""Rule-based event score. Articles after signal_time are ignored."""

import math
import re
from datetime import datetime

from shortalpha.config import EventConfig
from shortalpha.domain import EventRisk, FactorResult, NewsItem
from shortalpha.event_rules import EventRules, EventTypeRule
from shortalpha.factors.scale import clamp, scale_to_weight

_TOKENS = re.compile(r"[a-z0-9]+")
_EMPTY_REASON = "no qualifying events"


def compute_event(
    symbol: str,
    items: list[NewsItem],
    *,
    as_of: datetime,
    event: EventConfig,
    rules: EventRules,
    weight: float,
) -> FactorResult:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    observations = _observations(symbol, items, as_of=as_of, event=event, rules=rules)
    kept, ambiguous = observations
    if not kept:
        neutral = scale_to_weight(0.0, event.raw_low, event.raw_high, weight)
        return FactorResult(
            name="event",
            raw_value=0.0,
            normalized_value=neutral / weight,
            score=neutral,
            available=True,
            reasons=(_EMPTY_REASON,),
            risks=tuple(f"Ambiguous {family} event ignored" for family in ambiguous),
            details=(
                ("event_risk", EventRisk.NONE.value),
                ("raw_sum", "0"),
                ("kept", "0"),
            ),
        )
    contributions: list[tuple[datetime, str, float, float, float]] = []
    severe = False
    for item, rule in kept:
        age_hours = (as_of - item.published_at).total_seconds() / 3600
        freshness = math.exp(-event.freshness_lambda * age_hours)
        signed = rule.direction * rule.importance * freshness * rule.confidence
        contributions.append((item.published_at, rule.label, signed, rule.direction, freshness))
        if (
            rule.direction < 0
            and rule.importance >= event.severe_importance
            and freshness >= event.severe_freshness
        ):
            severe = True
    raw_sum = sum(item[2] for item in contributions)
    raw = clamp(raw_sum, event.raw_low, event.raw_high)
    score = scale_to_weight(raw, event.raw_low, event.raw_high, weight)
    reasons = _unique(label for _, label, signed, _, _ in sorted(contributions) if signed > 0)
    risks = _unique(label for _, label, signed, _, _ in sorted(contributions) if signed < 0)
    if severe:
        risks = (*risks, "Severe negative event")
    risks = (*risks, *(f"Ambiguous {family} event ignored" for family in ambiguous))
    return FactorResult(
        name="event",
        raw_value=raw,
        normalized_value=score / weight,
        score=score,
        available=True,
        reasons=reasons,
        risks=risks,
        details=(
            ("event_risk", EventRisk.SEVERE_NEGATIVE.value if severe else EventRisk.NONE.value),
            ("raw_sum", f"{raw_sum:.10f}"),
            ("kept", str(len(kept))),
        ),
    )


def _observations(
    symbol: str,
    items: list[NewsItem],
    *,
    as_of: datetime,
    event: EventConfig,
    rules: EventRules,
) -> tuple[list[tuple[NewsItem, EventTypeRule]], tuple[str, ...]]:
    classified: list[tuple[NewsItem, EventTypeRule]] = []
    ambiguous: set[str] = set()
    for item in items:
        if symbol not in item.symbols or item.available_at > as_of or item.published_at > as_of:
            continue
        age_hours = (as_of - item.published_at).total_seconds() / 3600
        if age_hours > event.max_age_hours:
            continue
        matches, skipped = _classify(item, rules)
        ambiguous.update(skipped)
        classified.extend((item, rule) for rule in matches)
    classified.sort(key=lambda pair: (pair[0].published_at, pair[0].id, pair[1].id))
    kept: list[tuple[NewsItem, EventTypeRule]] = []
    for item, rule in classified:
        tokens = _tokens(item.headline)
        if any(
            prior_rule.id == rule.id
            and _hours(item.published_at, prior.published_at) <= event.duplicate_window_hours
            and _jaccard(tokens, _tokens(prior.headline)) >= event.duplicate_jaccard
            for prior, prior_rule in kept
        ):
            continue
        kept.append((item, rule))
    return kept, tuple(sorted(ambiguous))


def _classify(item: NewsItem, rules: EventRules) -> tuple[list[EventTypeRule], list[str]]:
    text = f"{item.headline}\n{item.summary}".casefold()
    hits: list[EventTypeRule] = []
    for rule in rules.types:
        if any(pattern.casefold() in text for pattern in rule.patterns):
            hits.append(rule)
    by_family: dict[str, list[EventTypeRule]] = {}
    for rule in hits:
        by_family.setdefault(rule.family, []).append(rule)
    kept: list[EventTypeRule] = []
    ambiguous: list[str] = []
    for family, group in by_family.items():
        positive = any(rule.direction > 0 for rule in group)
        negative = any(rule.direction < 0 for rule in group)
        if positive and negative:
            ambiguous.append(family)
            continue
        kept.append(max(group, key=_strength))
    return kept, ambiguous


def _strength(rule: EventTypeRule) -> float:
    return abs(rule.direction) * rule.importance * rule.confidence


def _tokens(headline: str) -> set[str]:
    return set(_TOKENS.findall(headline.casefold()))


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    return len(left & right) / len(union)


def _hours(left: datetime, right: datetime) -> float:
    return abs((left - right).total_seconds()) / 3600


def _unique(values) -> tuple[str, ...]:
    seen: list[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return tuple(seen)
