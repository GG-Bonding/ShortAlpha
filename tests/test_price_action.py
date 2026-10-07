from datetime import datetime

import pytest

from shortalpha.calendar import NYSECalendar
from shortalpha.config import load_config
from shortalpha.domain import PremarketWindow, Split
from shortalpha.errors import DataUnavailableError
from shortalpha.factors.price_action import compute_price_action
from tests.factor_setup import NY, SESSION, bar, premarket, prior_sessions, signal_time


def _flat(calendar: NYSECalendar, close: float = 100) -> list:
    return [bar("AAA", day, close) for day in prior_sessions(calendar, 20)]


def _score(cfg, calendar, gap: float, bars=None):
    history = _flat(calendar) if bars is None else bars
    return compute_price_action(
        "AAA",
        history,
        premarket("AAA", 100 * (1 + gap)),
        session=SESSION,
        as_of=signal_time(),
        calendar=calendar,
        price_action=cfg.price_action,
        weight=cfg.weights.price_action,
    ).score


def test_a_larger_gap_does_not_score_higher(repo_root) -> None:
    cfg = load_config(repo_root / "config" / "default.yaml", root=repo_root)
    calendar = NYSECalendar()
    at_start = _score(cfg, calendar, 0.03)
    mid = _score(cfg, calendar, 0.05)
    at_hard = _score(cfg, calendar, 0.08)
    beyond = _score(cfg, calendar, 0.10)
    assert at_start == pytest.approx(15)
    assert beyond == pytest.approx(at_hard)
    assert at_hard < mid < at_start
    hot = compute_price_action(
        "AAA",
        _flat(calendar),
        premarket("AAA", 106.8),
        session=SESSION,
        as_of=signal_time(),
        calendar=calendar,
        price_action=cfg.price_action,
        weight=cfg.weights.price_action,
    )
    assert any(risk.startswith("Premarket gap already") for risk in hot.risks)
    assert "Broke previous high" in hot.reasons
    assert "Broke 20-day high" in hot.reasons


def test_touching_the_prior_high_is_not_a_break(repo_root) -> None:
    cfg = load_config(repo_root / "config" / "default.yaml", root=repo_root)
    result = compute_price_action(
        "AAA",
        _flat(NYSECalendar()),
        premarket("AAA", 100),
        session=SESSION,
        as_of=signal_time(),
        calendar=NYSECalendar(),
        price_action=cfg.price_action,
        weight=cfg.weights.price_action,
    )
    assert result.raw_value == pytest.approx(0)
    assert result.score == pytest.approx(0)
    assert result.reasons == ()


def test_elevated_atr_scales_the_score_and_adds_a_risk(repo_root) -> None:
    cfg = load_config(repo_root / "config" / "default.yaml", root=repo_root)
    calendar = NYSECalendar()
    bars = [bar("AAA", day, 100, high=102, low=80) for day in prior_sessions(calendar, 20)]
    result = compute_price_action(
        "AAA",
        bars,
        premarket("AAA", 103),
        session=SESSION,
        as_of=signal_time(),
        calendar=calendar,
        price_action=cfg.price_action,
        weight=cfg.weights.price_action,
    )
    assert "ATR elevated" in result.risks
    assert result.score == pytest.approx(15 * 0.8)


def test_missing_premarket_is_neutral_not_a_zero(repo_root) -> None:
    cfg = load_config(repo_root / "config" / "default.yaml", root=repo_root)
    result = compute_price_action(
        "AAA",
        [],
        premarket("AAA", 0, available=False, reason="feed does not include consolidated premarket"),
        session=SESSION,
        as_of=signal_time(),
        calendar=NYSECalendar(),
        price_action=cfg.price_action,
        weight=cfg.weights.price_action,
    )
    assert result.degraded is True
    assert result.raw_value is None
    assert result.score == pytest.approx(7.5)
    assert "neutral" in result.reasons[0]
    assert "consolidated premarket" in result.reasons[0]


def test_a_non_consolidated_print_still_scores_the_gap(repo_root) -> None:
    cfg = load_config(repo_root / "config" / "default.yaml", root=repo_root)
    stamp = datetime(2024, 6, 20, 8, 30, tzinfo=NY)
    window = PremarketWindow(
        symbol="AAA",
        session_date=SESSION,
        available=False,
        volume=None,
        last_price=103,
        high=104,
        low=102,
        event_time=stamp,
        published_at=stamp,
        available_at=stamp,
        reason="IEX feed is not consolidated premarket; premarket_volume_available is false",
        price_consolidated=False,
    )
    result = compute_price_action(
        "AAA",
        _flat(NYSECalendar()),
        window,
        session=SESSION,
        as_of=signal_time(),
        calendar=NYSECalendar(),
        price_action=cfg.price_action,
        weight=cfg.weights.price_action,
    )
    details = dict(result.details)
    assert result.degraded is False
    assert result.raw_value == pytest.approx(0.03)
    assert details["price_consolidated"] == "false"
    assert details["price_time"] == stamp.isoformat()
    assert "not the consolidated tape" in " ".join(result.reasons)


def test_split_on_the_signal_session_blocks_the_gap(repo_root) -> None:
    cfg = load_config(repo_root / "config" / "default.yaml", root=repo_root)
    with pytest.raises(DataUnavailableError, match="split") as caught:
        compute_price_action(
            "AAA",
            _flat(NYSECalendar()),
            premarket("AAA", 103),
            session=SESSION,
            as_of=signal_time(),
            calendar=NYSECalendar(),
            price_action=cfg.price_action,
            weight=cfg.weights.price_action,
            splits=(Split(symbol="AAA", ex_date=SESSION, old_rate=1, new_rate=10),),
        )
    assert caught.value.operation == "price_action"
