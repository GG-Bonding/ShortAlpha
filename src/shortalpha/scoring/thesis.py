"""A classified change proposes the trade. Price can reject it.

Prior expectations, earnings impact, and positioning are not invented when the
inputs do not contain them. The five-day relative-strength window is the
window ending at the last completed session. It is called pre-event only when
that window actually closed before the news.
"""

from datetime import datetime

from shortalpha.domain import FactorResult
from shortalpha.scoring.confirmation import (
    BASELINE_MAX_TRADED_SECONDS,
    Confirmation,
    confirm_price,
)

_EMPTY = "no qualifying events"


class Thesis:
    def __init__(self, *, supported: bool, veto: str | None, text: str) -> None:
        self.supported = supported
        self.veto = veto
        self.text = text


def assess_thesis(factors: dict[str, FactorResult]) -> Thesis:
    event = factors["event"]
    relative = factors["relative_strength"]
    momentum = factors["momentum"]
    volume = factors["volume"]
    price = factors["price_action"]
    confirmation = confirm_price(event, price)
    veto = _veto(event, momentum, confirmation)
    setup = _setup(relative, veto)
    text = _text(event, relative, momentum, volume, confirmation, setup, veto)
    return Thesis(supported=veto is None, veto=veto, text=text)


def _veto(
    event: FactorResult,
    momentum: FactorResult,
    confirmation: Confirmation,
) -> str | None:
    if event.raw_value is None or event.raw_value <= 0 or _EMPTY in event.reasons:
        return "no positive classified change"
    if dict(momentum.details).get("overheated") == "true":
        return "five-day move already exceeds 25%"
    return confirmation.veto


def _setup(relative: FactorResult, veto: str | None) -> str:
    if veto is not None:
        return "unconfirmed"
    if relative.raw_value is not None and relative.raw_value > 0:
        return "continuation"
    return "new catalyst"


def _text(
    event: FactorResult,
    relative: FactorResult,
    momentum: FactorResult,
    volume: FactorResult,
    confirmation: Confirmation,
    setup: str,
    veto: str | None,
) -> str:
    labels = [reason for reason in event.reasons if reason != _EMPTY]
    change = ", ".join(labels) if labels else "none"
    if confirmation.cumulative is None:
        after = "no price after the event"
    else:
        after = f"{confirmation.cumulative * 100:+.1f}% from the pre-event baseline"
    baseline = {
        "measured": "The baseline is the last completed close at or before the news",
        "exact": "The baseline is the last observed price at or before the news",
        "approximate": (
            "The baseline is approximate because more than "
            f"{BASELINE_MAX_TRADED_SECONDS} seconds of trading separate it from the news"
        ),
        "missing": "The baseline was not observed",
    }[confirmation.baseline]
    if volume.raw_value is None:
        activity = "volume check is unavailable"
    elif volume.raw_value >= 1:
        activity = f"volume is {volume.raw_value:.1f}x the baseline"
    else:
        activity = (
            f"volume is {volume.raw_value:.1f}x the baseline and does not show unusual activity"
        )
    ceiling = ""
    if _non_positive(momentum) and _non_positive(relative):
        ceiling = (
            "Momentum and relative strength are not positive. "
            "The new-catalyst label does not add points, and these two factors at or below zero "
            "leave the total below 80 even when event, volume, and price action are full. "
        )
    body = (
        "Prior expectation is unknown; a headline class is not a consensus estimate. "
        f"Classified change: {change}. "
        "The earnings or valuation channel is not measured. "
        f"{_trend(relative, _news_time(event))}. "
        f"Post-event move: {after}. {baseline}.{_age_sentence(event)} "
        f"{confirmation.same_day}. "
        "The same-day gap checks whether the entry is already extended. "
        f"Setup: {setup}. "
        "A positive recent relative-strength window labels continuation. "
        "A new catalyst does not need that window once a post-event price confirms the change. "
        f"{ceiling}"
        f"{activity}. "
        "Positioning is unknown. "
        "Continuation over the next 1-3 days is an untested hypothesis. "
        "The judgment fails when there is no positive classified change, "
        "the five-day move exceeds 25%, the cumulative post-event move or the same-day gap "
        "reaches 8%, the same-day price is missing, the price is not after the news, "
        "or the pre-event baseline is approximate."
    )
    if veto is None:
        return body
    return f"Veto: {veto}. {body}"


def _age_sentence(event: FactorResult) -> str:
    age = dict(event.details).get("pre_event_age_seconds")
    if not isinstance(age, str) or age in {"", "missing"}:
        return ""
    return f" The pre-event price is {age} seconds before the news."


def _non_positive(factor: FactorResult) -> bool:
    return factor.raw_value is not None and factor.raw_value <= 0


def _trend(relative: FactorResult, news_at: datetime | None) -> str:
    if relative.raw_value is None:
        amount = "relative strength is unavailable"
    else:
        amount = f"5-day excess versus SPY and the sector is {relative.raw_value * 100:+.1f}%"
    window_end = _clock(dict(relative.details).get("window_end"))
    if news_at is not None and window_end is not None and news_at < window_end:
        return (
            "Recent relative strength through the last completed session "
            f"includes sessions after the event; {amount}"
        )
    if news_at is not None and window_end is not None and news_at >= window_end:
        return f"Recent relative strength completed before the event; {amount}"
    return f"Recent relative strength through the last completed session; {amount}"


def _news_time(event: FactorResult) -> datetime | None:
    positive = [item for item in event.events if item.direction > 0]
    if positive:
        latest = max(positive, key=lambda item: (item.available_at, item.news_id))
        return latest.available_at
    return _clock(dict(event.details).get("news_available_at"))


def _clock(raw: object) -> datetime | None:
    if not isinstance(raw, str) or raw in {"", "missing"}:
        return None
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed
