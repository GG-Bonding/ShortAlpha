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
) -> str | None:
    factors = {
        "event": _factor(
            "event",
            raw=0.4,
            reasons=("Earnings beat",),
            details=(
                ("event_risk", "NONE"),
                ("news_available_at", news_at),
                ("post_event_reaction", reaction),
            ),
        ),
        "relative_strength": _factor("relative_strength", raw=0.02, details=()),
        "momentum": _factor("momentum", raw=0.02, details=(("overheated", "false"),)),
        "volume": _factor("volume", raw=1.5, details=()),
        "price_action": _factor(
            "price_action",
            raw=gap,
            details=(
                ("degraded", "false"),
                ("gap", f"{gap:.10f}"),
                ("price_time", price_at),
                ("prior_close_time", prior_at),
                ("price_consolidated", consolidated),
            ),
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
