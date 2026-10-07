"""Rule-based event score. Articles after signal_time are ignored."""

import hashlib
import math
import re
from collections.abc import Mapping
from datetime import date, datetime

from shortalpha.config import EventConfig
from shortalpha.domain import ClassifiedEvent, DailyBar, EventRisk, FactorResult, NewsItem, Split
from shortalpha.event_rules import EventRules, EventTypeRule
from shortalpha.factors.adjust import adjusted_close
from shortalpha.factors.scale import clamp, scale_to_weight

_TOKENS = re.compile(r"[a-z0-9]+")
_EMPTY_REASON = "no qualifying events"
_CLAUSE_FAMILIES = frozenset({"earnings", "revenue", "guidance", "offering", "price_target"})
_DEAL_RULES = frozenset({"ma_announced", "ma_buyer"})
_NAME_NOISE = re.compile(
    r"\b(incorporated|inc|corp|corporation|ltd|limited|plc|n\.v|nv|se|sa|ag|"
    r"holdings|holding|group|company|co|class|common|ordinary|shares|american|"
    r"depositary|subordinate|voting|new|york|registry|the)\b",
    re.IGNORECASE,
)
_BE_ACQUIRED = re.compile(
    r"(?P<target>.+?)\s+(?:agrees to be acquired|agreed to be acquired|to be acquired)"
    r"(?:\s+by\s+(?P<buyer>.+))?",
    re.IGNORECASE,
)
_TO_ACQUIRE = re.compile(
    r"(?P<buyer>.+?)\s+(?:agreed to acquire|agrees to acquire|will acquire|to acquire|acquires)"
    r"\s+(?P<target>.+)",
    re.IGNORECASE,
)
_LAWSUIT_AGAINST = re.compile(
    r"(?P<plaintiff>.+?)\s+files (?:a )?lawsuit against\s+(?P<defendant>.+)",
    re.IGNORECASE,
)
_FILES_LAWSUIT = re.compile(
    r"(?P<plaintiff>.+?)\s+files (?:a )?lawsuit",
    re.IGNORECASE,
)
_HIT_WITH = re.compile(
    r"(?P<defendant>.+?)\s+(?:was )?hit with (?:a )?lawsuit",
    re.IGNORECASE,
)
_SEC_CHARGES = re.compile(r"sec charges\s+(?P<defendant>.+)", re.IGNORECASE)


def content_sha256(headline: str, summary: str) -> str:
    return hashlib.sha256(f"{headline}\n{summary}".encode()).hexdigest()


def compute_event(
    symbol: str,
    items: list[NewsItem],
    *,
    as_of: datetime,
    event: EventConfig,
    rules: EventRules,
    weight: float,
    rules_sha256: str = "",
    bars: list[DailyBar] | None = None,
    session: date | None = None,
    splits: tuple[Split, ...] = (),
    names: Mapping[str, str] | None = None,
) -> FactorResult:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    observations = _observations(
        symbol,
        items,
        as_of=as_of,
        event=event,
        rules=rules,
        names=names or {},
    )
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
    classified: list[ClassifiedEvent] = []
    baselines: dict[str, datetime] = {}
    severe = False
    for item, rule in kept:
        age_hours = (as_of - item.published_at).total_seconds() / 3600
        freshness = math.exp(-event.freshness_lambda * age_hours)
        signed = rule.direction * rule.importance * freshness * rule.confidence
        contributions.append((item.published_at, rule.label, signed, rule.direction, freshness))
        move, baseline_at = _reaction(bars, item.available_at, session, as_of, splits, symbol)
        if baseline_at is not None:
            baselines[item.id] = baseline_at
        classified.append(
            ClassifiedEvent(
                news_id=item.id,
                rule_id=rule.id,
                label=rule.label,
                family=rule.family,
                source=item.source,
                published_at=item.published_at,
                available_at=item.available_at,
                content_sha256=content_sha256(item.headline, item.summary),
                rules_sha256=rules_sha256,
                signed=signed,
                direction=rule.direction,
                importance=rule.importance,
                freshness=freshness,
                reaction=move,
            )
        )
        if (
            rule.direction < 0
            and rule.importance >= event.severe_importance
            and age_hours <= event.severe_hold_hours
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
            ("post_event_reaction", _reaction_text(classified)),
            ("reaction_baseline_time", _baseline_text(classified, baselines)),
            ("news_available_at", _news_time_text(classified)),
        ),
        events=tuple(classified),
    )


def _observations(
    symbol: str,
    items: list[NewsItem],
    *,
    as_of: datetime,
    event: EventConfig,
    rules: EventRules,
    names: Mapping[str, str],
) -> tuple[list[tuple[NewsItem, EventTypeRule]], tuple[str, ...]]:
    classified: list[tuple[NewsItem, EventTypeRule]] = []
    ambiguous: set[str] = set()
    for item in items:
        if symbol not in item.symbols or item.available_at > as_of or item.published_at > as_of:
            continue
        age_hours = (as_of - item.published_at).total_seconds() / 3600
        if age_hours > event.max_age_hours:
            continue
        matches, skipped = _classify(item, rules, symbol, names)
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


def _classify(
    item: NewsItem,
    rules: EventRules,
    symbol: str,
    names: Mapping[str, str],
) -> tuple[list[EventTypeRule], list[str]]:
    original = f"{item.headline}\n{item.summary}"
    text = original.casefold()
    hits = [
        rule for rule in rules.types if any(pattern.casefold() in text for pattern in rule.patterns)
    ]
    hits, ambiguous = _select_roles(hits, original, item, symbol, names)
    by_family: dict[str, list[EventTypeRule]] = {}
    for rule in hits:
        by_family.setdefault(rule.family, []).append(rule)
    kept: list[EventTypeRule] = []
    for family, group in by_family.items():
        positive = any(rule.direction > 0 for rule in group)
        negative = any(rule.direction < 0 for rule in group)
        if positive and negative:
            ambiguous.append(family)
            continue
        kept.append(max(group, key=_strength))
    return kept, ambiguous


def _select_roles(
    hits: list[EventTypeRule],
    text: str,
    item: NewsItem,
    symbol: str,
    names: Mapping[str, str],
) -> tuple[list[EventTypeRule], list[str]]:
    by_family: dict[str, list[EventTypeRule]] = {}
    for rule in hits:
        by_family.setdefault(rule.family, []).append(rule)
    kept: list[EventTypeRule] = []
    ambiguous: list[str] = []
    for family, group in by_family.items():
        if family == "ma":
            _keep_deal(group, text, item, symbol, names, kept, ambiguous)
            continue
        if family == "legal":
            _keep_legal(group, text, item, symbol, names, kept, ambiguous)
            continue
        if family in _CLAUSE_FAMILIES:
            chosen, shared = _rules_in_symbol_clauses(text, group, symbol, item.symbols, names)
            if chosen:
                kept.extend(chosen)
            elif len(item.symbols) == 1 and _patterns_present(text, group):
                kept.extend(group)
            elif shared:
                ambiguous.append(family)
            continue
        kept.extend(group)
    return kept, ambiguous


def _keep_deal(
    group: list[EventTypeRule],
    text: str,
    item: NewsItem,
    symbol: str,
    names: Mapping[str, str],
    kept: list[EventTypeRule],
    ambiguous: list[str],
) -> None:
    deal = [rule for rule in group if rule.id in _DEAL_RULES]
    other = [rule for rule in group if rule.id not in _DEAL_RULES]
    scored = False
    if deal:
        role = _deal_role(text, symbol, item.symbols, names)
        if role == "target":
            kept.extend(rule for rule in deal if rule.direction > 0)
            scored = True
        elif role == "acquirer":
            kept.extend(rule for rule in deal if rule.direction < 0)
            scored = True
    if other and (_clause_mentions(text, other, symbol, names) or len(item.symbols) == 1):
        kept.extend(other)
        scored = True
    if (deal or other) and not scored:
        ambiguous.append("ma")


def _keep_legal(
    group: list[EventTypeRule],
    text: str,
    item: NewsItem,
    symbol: str,
    names: Mapping[str, str],
    kept: list[EventTypeRule],
    ambiguous: list[str],
) -> None:
    negatives = [rule for rule in group if rule.direction < 0]
    others = [rule for rule in group if rule.direction >= 0]
    role = _legal_role(text, symbol, item.symbols, names) if negatives else None
    scored = False
    if role == "defendant":
        kept.extend(negatives)
        scored = True
    other_applies = bool(others) and (
        _clause_mentions(text, others, symbol, names) or len(item.symbols) == 1
    )
    if other_applies:
        kept.extend(others)
        scored = True
    unresolved_negative = bool(negatives) and role not in {"defendant", "plaintiff"}
    unresolved_other = bool(others) and not other_applies
    if not scored and (unresolved_negative or unresolved_other):
        ambiguous.append("legal")


def _deal_role(
    text: str,
    symbol: str,
    symbols: tuple[str, ...],
    names: Mapping[str, str],
) -> str | None:
    buyers: list[str] = []
    targets: list[str] = []
    for match in _BE_ACQUIRED.finditer(text):
        targets.append(match.group("target"))
        buyer = match.group("buyer")
        if buyer:
            buyers.append(buyer)
    for match in _TO_ACQUIRE.finditer(text):
        buyers.append(match.group("buyer"))
        targets.append(match.group("target"))
    is_buyer = any(_mentioned(span, symbol, names) for span in buyers)
    is_target = any(_mentioned(span, symbol, names) for span in targets)
    if is_buyer and is_target:
        return None
    if is_target:
        return "target"
    if is_buyer:
        return "acquirer"
    if len(symbols) != 1 or symbols[0] != symbol:
        return None
    folded = text.casefold()
    if "to be acquired" in folded or "be acquired" in folded:
        return "target"
    if "to acquire" in folded or re.search(r"\bacquires\b", folded):
        return "acquirer"
    return None


def _legal_role(
    text: str,
    symbol: str,
    symbols: tuple[str, ...],
    names: Mapping[str, str],
) -> str | None:
    plaintiffs: list[str] = []
    defendants: list[str] = []
    for match in _LAWSUIT_AGAINST.finditer(text):
        plaintiffs.append(match.group("plaintiff"))
        defendants.append(match.group("defendant"))
    for match in _HIT_WITH.finditer(text):
        defendants.append(match.group("defendant"))
    for match in _SEC_CHARGES.finditer(text):
        defendants.append(match.group("defendant"))
    for match in _FILES_LAWSUIT.finditer(text):
        plaintiffs.append(match.group("plaintiff"))
    is_plaintiff = any(_mentioned(span, symbol, names) for span in plaintiffs)
    is_defendant = any(_mentioned(span, symbol, names) for span in defendants)
    if is_plaintiff and is_defendant:
        return None
    if is_defendant:
        return "defendant"
    if is_plaintiff:
        return "plaintiff"
    if len(symbols) != 1 or symbols[0] != symbol:
        return None
    folded = text.casefold()
    if "hit with lawsuit" in folded or "sec charges" in folded:
        return "defendant"
    if "files lawsuit" in folded or "files a lawsuit" in folded:
        return "plaintiff"
    return None


_CLAUSE_BREAK = re.compile(
    r"\s+while\s+|\s+whereas\s+|\s+but\s+|\s+although\s+|[\n.!;?,]+",
    re.IGNORECASE,
)


def _clauses(text: str) -> list[str]:
    return [part.strip() for part in _CLAUSE_BREAK.split(text) if part.strip()]


def _patterns_present(text: str, rules: list[EventTypeRule]) -> bool:
    folded = text.casefold()
    return any(pattern.casefold() in folded for rule in rules for pattern in rule.patterns)


def _rules_in_symbol_clauses(
    text: str,
    rules: list[EventTypeRule],
    symbol: str,
    symbols: tuple[str, ...],
    names: Mapping[str, str],
) -> tuple[list[EventTypeRule], bool]:
    chosen: list[EventTypeRule] = []
    shared = False
    for rule in rules:
        status = _rule_clause_status(text, rule, symbol, symbols, names)
        if status == "owner":
            chosen.append(rule)
        elif status == "shared":
            shared = True
    return chosen, shared


def _rule_clause_status(
    text: str,
    rule: EventTypeRule,
    symbol: str,
    symbols: tuple[str, ...],
    names: Mapping[str, str],
) -> str:
    shared = False
    for clause in _clauses(text):
        if not any(pattern.casefold() in clause.casefold() for pattern in rule.patterns):
            continue
        mentioned = [item for item in symbols if _mentioned(clause, item, names)]
        if mentioned == [symbol]:
            return "owner"
        if symbol in mentioned and len(mentioned) > 1:
            shared = True
    return "shared" if shared else "none"


def _clause_mentions(
    text: str,
    rules: list[EventTypeRule],
    symbol: str,
    names: Mapping[str, str],
) -> bool:
    return any(
        _rule_clause_status(text, rule, symbol, (symbol,), names) == "owner" for rule in rules
    )


def _mentioned(text: str, symbol: str, names: Mapping[str, str]) -> bool:
    if len(symbol) <= 2:
        found = re.search(rf"(?<![A-Za-z0-9]){re.escape(symbol)}(?![A-Za-z0-9])", text)
    else:
        found = re.search(
            rf"(?<![A-Za-z0-9]){re.escape(symbol)}(?![A-Za-z0-9])",
            text,
            re.IGNORECASE,
        )
    if found:
        return True
    name = _distinctive_name(names.get(symbol, ""))
    if not name:
        return False
    normalized = _normalize(text)
    if len(name) >= 5 and re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", normalized):
        return True
    parts = name.split()
    tokens = [token for token in parts if len(token) >= 5]
    if len(parts) == 1 and len(parts[0]) >= 3:
        tokens.append(parts[0])
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", normalized) for token in tokens
    )


def _distinctive_name(name: str) -> str:
    return _normalize(_NAME_NOISE.sub(" ", name))


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _latest_positive(classified: list[ClassifiedEvent]) -> ClassifiedEvent | None:
    positive = [item for item in classified if item.direction > 0]
    if not positive:
        return None
    return max(positive, key=lambda item: (item.available_at, item.news_id))


def _reaction_text(classified: list[ClassifiedEvent]) -> str:
    latest = _latest_positive(classified)
    if latest is None or latest.reaction is None:
        return "missing"
    return f"{latest.reaction:.10f}"


def _baseline_text(classified: list[ClassifiedEvent], baselines: dict[str, datetime]) -> str:
    latest = _latest_positive(classified)
    if latest is None:
        return "missing"
    baseline = baselines.get(latest.news_id)
    if baseline is None:
        return "missing"
    return baseline.isoformat()


def _news_time_text(classified: list[ClassifiedEvent]) -> str:
    latest = _latest_positive(classified)
    if latest is None:
        return "missing"
    return latest.available_at.isoformat()


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


def _reaction(
    bars: list[DailyBar] | None,
    news_available: datetime,
    session: date | None,
    as_of: datetime,
    splits: tuple[Split, ...],
    symbol: str,
) -> tuple[float | None, datetime | None]:
    if bars is None or session is None:
        return None, None
    completed = [
        bar
        for bar in bars
        if bar.symbol == symbol and bar.available_at <= as_of and bar.session_date < session
    ]
    if not completed:
        return None, None
    completed.sort(key=lambda bar: bar.session_date)
    prior = [bar for bar in completed if bar.available_at <= news_available]
    if not prior:
        return None, None
    baseline_at = prior[-1].available_at
    if prior[-1].session_date == completed[-1].session_date:
        return None, baseline_at
    start = adjusted_close(prior[-1].close, prior[-1].session_date, session, splits, symbol)
    end = adjusted_close(completed[-1].close, completed[-1].session_date, session, splits, symbol)
    if start <= 0:
        return None, baseline_at
    return end / start - 1, baseline_at


def _unique(values) -> tuple[str, ...]:
    seen: list[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return tuple(seen)
