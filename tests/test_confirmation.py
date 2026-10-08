from shortalpha.domain import FactorResult
from shortalpha.scoring.thesis import assess_thesis

_EVENT = (
    ("event_risk", "NONE"),
    ("post_event_reaction", "missing"),
)


def _factor(
    name: str,
    *,
    raw: float | None,
    details: tuple[tuple[str, str], ...],
    reasons: tuple[str, ...] = (),
) -> FactorResult:
    return FactorResult(
        name=name,
        raw_value=raw,
        normalized_value=None,
        score=15,
        available=True,
        reasons=reasons,
        details=details,
    )


def _assess(
    *,
    news_at: str,
    price_at: str,
    prior_at: str,
    gap: float,
    reaction: str = "missing",
    consolidated: str = "true",
    baseline_at: str | None = None,
    last_price: str | None = None,
    pre_event_price: str | None = None,
    pre_event_price_time: str | None = None,
) -> str | None:
    event_details = [
        ("event_risk", "NONE"),
        ("news_available_at", news_at),
        ("post_event_reaction", reaction),
    ]
    if reaction != "missing":
        event_details.append(("reaction_baseline_time", baseline_at or prior_at))
    if pre_event_price is not None and pre_event_price_time is not None:
        event_details.append(("pre_event_price", pre_event_price))
        event_details.append(("pre_event_price_time", pre_event_price_time))
    price_details = [
        ("degraded", "false"),
        ("gap", f"{gap:.10f}"),
        ("price_time", price_at),
        ("prior_close_time", prior_at),
        ("price_consolidated", consolidated),
    ]
    if last_price is not None:
        price_details.append(("last_price", last_price))
    factors = {
        "event": _factor(
            "event",
            raw=0.4,
            reasons=("Earnings beat",),
            details=tuple(event_details),
        ),
        "relative_strength": _factor("relative_strength", raw=0.02, details=()),
        "momentum": _factor("momentum", raw=0.02, details=(("overheated", "false"),)),
        "volume": _factor("volume", raw=1.5, details=()),
        "price_action": _factor(
            "price_action",
            raw=gap,
            details=tuple(price_details),
        ),
    }
    return assess_thesis(factors).veto


def test_a_print_from_before_the_headline_does_not_confirm_it() -> None:
    veto = _assess(
        news_at="2024-06-20T08:59:00-04:00",
        price_at="2024-06-20T08:30:00-04:00",
        prior_at="2024-06-18T20:00:00-04:00",
        gap=0.03,
    )
    assert veto == "no price after the event"


def test_a_later_dip_does_not_erase_a_positive_post_event_move() -> None:
    veto = _assess(
        news_at="2024-06-18T21:00:00-04:00",
        price_at="2024-06-20T09:00:00-04:00",
        prior_at="2024-06-18T20:00:00-04:00",
        gap=-0.001,
        reaction="0.0500000000",
    )
    assert veto is None


def test_stacked_gains_use_the_cumulative_move() -> None:
    veto = _assess(
        news_at="2024-06-18T21:00:00-04:00",
        price_at="2024-06-20T09:00:00-04:00",
        prior_at="2024-06-18T20:00:00-04:00",
        gap=0.03,
        reaction="0.0600000000",
    )
    assert veto == "post-event move already exceeds 8%"
    assert (1.06 * 1.03 - 1) > 0.08


def test_a_baseline_that_skips_an_open_session_is_not_confirmation() -> None:
    veto = _assess(
        news_at="2024-06-18T21:00:00-04:00",
        price_at="2024-06-20T09:00:00-04:00",
        prior_at="2024-06-18T16:00:00-04:00",
        gap=0.03,
    )
    assert veto == "post-event baseline is approximate"


def test_an_iex_print_can_confirm_when_the_baseline_is_exact() -> None:
    thesis = assess_thesis(
        {
            "event": _factor(
                "event",
                raw=0.4,
                reasons=("Earnings beat",),
                details=(
                    *_EVENT,
                    ("news_available_at", "2024-06-18T21:00:00-04:00"),
                ),
            ),
            "relative_strength": _factor("relative_strength", raw=0.02, details=()),
            "momentum": _factor("momentum", raw=0.02, details=(("overheated", "false"),)),
            "volume": _factor("volume", raw=1.5, details=()),
            "price_action": _factor(
                "price_action",
                raw=0.03,
                details=(
                    ("degraded", "false"),
                    ("gap", "0.0300000000"),
                    ("price_time", "2024-06-20T09:00:00-04:00"),
                    ("prior_close_time", "2024-06-18T20:00:00-04:00"),
                    ("price_consolidated", "false"),
                ),
            ),
        }
    )
    assert thesis.veto is None
    assert "not the consolidated tape" in thesis.text


def test_the_same_daily_close_stays_approximate_after_the_next_session() -> None:
    monday_close = "2024-06-17T16:00:00-04:00"
    news = "2024-06-17T16:05:00-04:00"
    tuesday = _assess(
        news_at=news,
        price_at="2024-06-18T09:00:00-04:00",
        prior_at=monday_close,
        gap=0.03,
    )
    wednesday = _assess(
        news_at=news,
        price_at="2024-06-19T09:00:00-04:00",
        prior_at="2024-06-18T16:00:00-04:00",
        gap=103 / 102 - 1,
        reaction="0.0200000000",
        baseline_at=monday_close,
    )
    assert tuesday == "post-event baseline is approximate"
    assert wednesday == "post-event baseline is approximate"


def test_a_minute_ending_at_the_news_is_accepted() -> None:
    veto = _assess(
        news_at="2024-06-17T16:05:00-04:00",
        price_at="2024-06-18T09:00:00-04:00",
        prior_at="2024-06-17T16:00:00-04:00",
        gap=0.03,
        last_price="103.0000000000",
        pre_event_price="100.0000000000",
        pre_event_price_time="2024-06-17T16:05:00-04:00",
    )
    assert veto is None


def test_an_hours_old_print_in_the_same_session_is_approximate() -> None:
    veto = _assess(
        news_at="2024-06-17T15:59:30-04:00",
        price_at="2024-06-18T09:00:00-04:00",
        prior_at="2024-06-14T16:00:00-04:00",
        gap=0.03,
        last_price="103.0000000000",
        pre_event_price="90.0000000000",
        pre_event_price_time="2024-06-17T09:31:00-04:00",
    )
    assert veto == "post-event baseline is approximate"


def test_a_pre_event_minute_price_confirms_the_same_headline() -> None:
    veto = _assess(
        news_at="2024-06-17T16:05:00-04:00",
        price_at="2024-06-18T09:00:00-04:00",
        prior_at="2024-06-17T16:00:00-04:00",
        gap=0.03,
        last_price="103.0000000000",
        pre_event_price="100.4000000000",
        pre_event_price_time="2024-06-17T16:04:00-04:00",
    )
    assert veto is None
