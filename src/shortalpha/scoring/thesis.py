"""A classified change proposes the trade. Price can reject it.

Prior expectations, earnings impact, and positioning are not invented when the
inputs do not contain them.
"""

from shortalpha.domain import FactorResult

_EMPTY = "no qualifying events"
_GAP_HARD = 0.08


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
    gap = _gap(price)
    reaction = _completed_reaction(event)
    veto = _veto(event, momentum, gap, reaction)
    setup = _setup(relative, veto)
    text = _text(event, relative, volume, gap, reaction, setup, veto)
    return Thesis(supported=veto is None, veto=veto, text=text)


def _veto(
    event: FactorResult,
    momentum: FactorResult,
    gap: float | None,
    reaction: float | None,
) -> str | None:
    if event.raw_value is None or event.raw_value <= 0 or _EMPTY in event.reasons:
        return "no positive classified change"
    if dict(momentum.details).get("overheated") == "true":
        return "five-day move already exceeds 25%"
    if gap is not None and gap >= _GAP_HARD:
        return "premarket gap already exceeds 8%"
    if reaction is not None and reaction >= _GAP_HARD:
        return "post-event move already exceeds 8%"
    if gap is None:
        return "same-day price confirmation is missing"
    if gap <= 0 or (reaction is not None and reaction <= 0):
        return "price has not confirmed the event"
    return None


def _gap(price: FactorResult) -> float | None:
    details = dict(price.details)
    if details.get("degraded") != "false" or "gap" not in details:
        return None
    return float(details["gap"])


def _completed_reaction(event: FactorResult) -> float | None:
    details = dict(event.details)
    if "post_event_reaction" in details:
        raw = details["post_event_reaction"]
        if raw == "missing":
            return None
        return float(raw)
    positive = [item for item in event.events if item.direction > 0]
    if not positive:
        return None
    latest = max(positive, key=lambda item: (item.available_at, item.news_id))
    return latest.reaction


def _setup(relative: FactorResult, veto: str | None) -> str:
    if veto is not None:
        return "unconfirmed"
    if relative.raw_value is not None and relative.raw_value > 0:
        return "continuation"
    return "new catalyst"


def _text(
    event: FactorResult,
    relative: FactorResult,
    volume: FactorResult,
    gap: float | None,
    reaction: float | None,
    setup: str,
    veto: str | None,
) -> str:
    labels = [reason for reason in event.reasons if reason != _EMPTY]
    change = ", ".join(labels) if labels else "none"
    if relative.raw_value is None:
        trend = "relative strength is unavailable"
    else:
        trend = f"5-day excess versus SPY and the sector is {relative.raw_value * 100:+.1f}%"
    if reaction is None:
        after = "no completed session after the event"
    else:
        after = f"{reaction * 100:+.1f}%"
    if gap is None:
        same_day = "Same-day price is missing"
    else:
        same_day = f"Same-day premarket gap is {gap * 100:+.1f}%"
    if volume.raw_value is None:
        activity = "volume check is unavailable"
    elif volume.raw_value >= 1:
        activity = f"volume is {volume.raw_value:.1f}x the baseline"
    else:
        activity = (
            f"volume is {volume.raw_value:.1f}x the baseline and does not show unusual activity"
        )
    body = (
        "Prior expectation is unknown; a headline class is not a consensus estimate. "
        f"Classified change: {change}. "
        "The earnings or valuation channel is not measured. "
        f"Pre-event trend: {trend}. "
        f"Post-event reaction: {after}. "
        f"{same_day}. "
        f"Setup: {setup}. "
        "A positive pre-event trend labels continuation. "
        "A new catalyst does not need that trend once the post-event price confirms the change. "
        f"{activity}. "
        "Positioning is unknown. "
        "Continuation over the next 1-3 days is an untested hypothesis. "
        "The judgment fails when there is no positive classified change, "
        "the five-day move exceeds 25%, the post-event move or premarket gap reaches 8%, "
        "the same-day price is missing, or the post-event price does not confirm the event."
    )
    if veto is None:
        return body
    return f"Veto: {veto}. {body}"
