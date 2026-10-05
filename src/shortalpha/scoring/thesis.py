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
    veto = _veto(event, relative, momentum, price)
    return Thesis(supported=veto is None, veto=veto, text=_text(event, relative, volume, veto))


def _veto(
    event: FactorResult,
    relative: FactorResult,
    momentum: FactorResult,
    price: FactorResult,
) -> str | None:
    if event.raw_value is None or event.raw_value <= 0 or _EMPTY in event.reasons:
        return "no positive classified change"
    if relative.raw_value is None or relative.raw_value <= 0:
        return "price has not outperformed SPY and the sector"
    if dict(momentum.details).get("overheated") == "true":
        return "five-day move already exceeds 25%"
    gap = _gap(price)
    if gap is not None and gap >= _GAP_HARD:
        return "premarket gap already exceeds 8%"
    return None


def _gap(price: FactorResult) -> float | None:
    details = dict(price.details)
    if details.get("degraded") != "false" or "gap" not in details:
        return None
    return float(details["gap"])


def _text(
    event: FactorResult,
    relative: FactorResult,
    volume: FactorResult,
    veto: str | None,
) -> str:
    labels = [reason for reason in event.reasons if reason != _EMPTY]
    change = ", ".join(labels) if labels else "none"
    if relative.raw_value is None:
        reaction = "relative strength is unavailable"
    else:
        reaction = f"5-day excess versus SPY and the sector is {relative.raw_value * 100:+.1f}%"
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
        f"Price reaction: {reaction}. "
        f"{activity}. "
        "Positioning is unknown. "
        "Continuation over the next 1-3 days is an untested hypothesis. "
        "The judgment fails on a severe negative event, non-positive relative strength, "
        "a five-day move above 25%, or a premarket gap above 8%."
    )
    if veto is None:
        return body
    return f"Veto: {veto}. {body}"
