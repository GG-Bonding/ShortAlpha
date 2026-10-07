"""Post-event price check.

The confirming print has to be after the news. The move that matters is from
the pre-event baseline to the latest price after the news. The same-day gap
only says whether the entry itself is already extended. A baseline that skips
an open session is approximate and is not treated as confirmation.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from shortalpha.calendar import NYSECalendar
from shortalpha.domain import FactorResult

_GAP_HARD = 0.08
_NY = ZoneInfo("America/New_York")
_EMPTY = "missing"


class Confirmation:
    def __init__(
        self,
        *,
        veto: str | None,
        cumulative: float | None,
        baseline: str,
        same_day: str,
        tape: str,
    ) -> None:
        self.veto = veto
        self.cumulative = cumulative
        self.baseline = baseline
        self.same_day = same_day
        self.tape = tape


def confirm_price(event: FactorResult, price: FactorResult) -> Confirmation:
    details = dict(price.details)
    gap = _gap(details)
    price_time = _clock(details.get("price_time"))
    prior_time = _clock(details.get("prior_close_time"))
    consolidated = details.get("price_consolidated", "true") != "false"
    news_at = _news_time(event)
    reaction = _reaction_value(event)
    tape = "consolidated" if consolidated else "iex"
    same_day = _same_day(gap, price_time, tape)
    if gap is not None and gap >= _GAP_HARD:
        return Confirmation(
            veto="premarket gap already exceeds 8%",
            cumulative=None,
            baseline="missing",
            same_day=same_day,
            tape=tape,
        )
    if gap is None or price_time is None:
        return Confirmation(
            veto="same-day price confirmation is missing",
            cumulative=None,
            baseline="missing",
            same_day="Same-day price is missing",
            tape=tape,
        )
    if news_at is None or (reaction is None and price_time <= news_at):
        return Confirmation(
            veto="no price after the event",
            cumulative=None,
            baseline="missing",
            same_day=same_day,
            tape=tape,
        )
    price_after = price_time > news_at
    if reaction is not None and price_after:
        cumulative = (1 + reaction) * (1 + gap) - 1
        baseline = "measured"
    elif reaction is not None:
        cumulative = reaction
        baseline = "measured"
    else:
        cumulative = gap
        baseline = "exact"
        if prior_time is None or _session_open_between(prior_time, news_at):
            baseline = "approximate"
    if baseline == "approximate":
        return Confirmation(
            veto="post-event baseline is approximate",
            cumulative=cumulative,
            baseline=baseline,
            same_day=same_day,
            tape=tape,
        )
    if cumulative >= _GAP_HARD:
        return Confirmation(
            veto="post-event move already exceeds 8%",
            cumulative=cumulative,
            baseline=baseline,
            same_day=same_day,
            tape=tape,
        )
    if cumulative <= 0:
        return Confirmation(
            veto="price has not confirmed the event",
            cumulative=cumulative,
            baseline=baseline,
            same_day=same_day,
            tape=tape,
        )
    return Confirmation(
        veto=None,
        cumulative=cumulative,
        baseline=baseline,
        same_day=same_day,
        tape=tape,
    )


def _gap(details: dict[str, str]) -> float | None:
    if details.get("degraded") != "false" or "gap" not in details:
        return None
    return float(details["gap"])


def _reaction_value(event: FactorResult) -> float | None:
    details = dict(event.details)
    if "post_event_reaction" in details:
        raw = details["post_event_reaction"]
        if raw == _EMPTY:
            return None
        return float(raw)
    positive = [item for item in event.events if item.direction > 0]
    if not positive:
        return None
    latest = max(positive, key=lambda item: (item.available_at, item.news_id))
    return latest.reaction


def _news_time(event: FactorResult) -> datetime | None:
    positive = [item for item in event.events if item.direction > 0]
    if positive:
        latest = max(positive, key=lambda item: (item.available_at, item.news_id))
        return latest.available_at
    return _clock(dict(event.details).get("news_available_at"))


def _clock(raw: object) -> datetime | None:
    if not isinstance(raw, str) or raw in {"", _EMPTY}:
        return None
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _same_day(gap: float | None, price_time: datetime | None, tape: str) -> str:
    if gap is None or price_time is None:
        return "Same-day price is missing"
    stamp = price_time.astimezone(_NY).strftime("%H:%M")
    text = f"Same-day price at {stamp} is {gap * 100:+.1f}% from the prior close"
    if tape == "iex":
        return f"{text}. The print is not the consolidated tape"
    return text


def _session_open_between(start: datetime, end: datetime) -> bool:
    start = start.astimezone(_NY)
    end = end.astimezone(_NY)
    if end <= start:
        return False
    calendar = NYSECalendar()
    day = start.date()
    while day <= end.date():
        if calendar.is_trading_day(day):
            for open_at, close_at in _sessions(calendar, day):
                left = datetime.combine(day, open_at, tzinfo=_NY)
                right = datetime.combine(day, close_at, tzinfo=_NY)
                if min(right, end) > max(left, start):
                    return True
        day += timedelta(days=1)
    return False


def _sessions(calendar: NYSECalendar, day: date) -> tuple[tuple[time, time], ...]:
    if calendar.is_early_close(day):
        return (
            (time(4, 0), time(9, 30)),
            (time(9, 30), time(13, 0)),
            (time(13, 0), time(17, 0)),
        )
    return (
        (time(4, 0), time(9, 30)),
        (time(9, 30), time(16, 0)),
        (time(16, 0), time(20, 0)),
    )
