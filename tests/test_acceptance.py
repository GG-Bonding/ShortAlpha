from datetime import date

from shortalpha.capture import failure_kind
from shortalpha.evaluation.outcomes import format_outcomes
from shortalpha.signal.coverage import judgment


def test_failure_kinds_stay_separate() -> None:
    assert failure_kind("HTTP 403") == "auth"
    assert failure_kind("HTTP 429") == "rate_limit"
    assert failure_kind("ConnectError") == "request"
    assert failure_kind("invalid symbol: BRK/A") == "request"
    assert failure_kind("insufficient history: 19 < 20") == "data"


def test_a_missing_price_is_not_called_a_lack_of_opportunity() -> None:
    missing = _row("AAA", 83.0, "same-day price confirmation is missing")
    stale = _row("BBB", 87.0, "post-event baseline is approximate", pre_event_price="10")
    no_minute = _row("CCC", 87.0, "post-event baseline is approximate")
    quiet = _row("DDD", 40.0, "no positive classified change")
    assert judgment(missing, long_threshold=80) == "insufficient_data"
    assert judgment(stale, long_threshold=80) == "confirmation_reject"
    assert judgment(no_minute, long_threshold=80) == "insufficient_data"
    assert judgment(quiet, long_threshold=80) == "no_opportunity"


def test_outcomes_charge_cost_and_leave_immature_horizons_missing() -> None:
    day = date(2024, 6, 18)
    published = _row("AAA", 91.0, None, signal="LONG_CANDIDATE")
    watch = _row("BBB", 75.0, None, signal="WATCH")
    rejected = _row("CCC", 87.0, "price has not confirmed the event")
    hidden = _row("DDD", 88.0, "post-event baseline is approximate")
    snapshot = {
        "no_trade": False,
        "ranked": [published, watch, rejected, hidden],
        "symbols": [published],
    }
    text = format_outcomes(
        [
            (
                day,
                snapshot,
                [
                    {
                        "horizon": 1,
                        "mae": -0.03,
                        "spy_return": 0.01,
                        "stock_return": 0.02,
                        "symbol": "AAA",
                    }
                ],
            )
        ],
        cost=0.001,
        long_threshold=80,
        watch_threshold=70,
    )
    assert "2024-06-18 AAA 91.0 1D 扣成本=0.0190 超额=0.0090 不利波动=-0.0300" in text
    assert "2024-06-18 AAA 91.0 2D 缺失" in text
    assert "2024-06-18 BBB 75.0 1D 缺失" in text
    assert "2024-06-18 CCC 87.0 1D 缺失" in text
    assert "DDD" not in text

    blocked = format_outcomes(
        [(day, {**snapshot, "no_trade": True}, [])],
        cost=0.001,
        long_threshold=80,
        watch_threshold=70,
    )
    published_section, _rest = blocked.split("观察70-80", maxsplit=1)
    assert "AAA" not in published_section


def _row(
    symbol: str,
    score: float,
    veto: str | None,
    *,
    signal: str = "PASS",
    pre_event_price: str = "",
) -> dict[str, object]:
    return {
        "factors": {"event": {"details": {"pre_event_price": pre_event_price}, "raw_value": 0.2}},
        "signal": signal,
        "symbol": symbol,
        "thesis_veto": veto,
        "total_score": score,
    }
