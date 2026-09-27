from datetime import date

import pytest

from shortalpha.calendar import NYSECalendar
from shortalpha.config import load_config
from shortalpha.domain import Split
from shortalpha.errors import DataUnavailableError
from shortalpha.factors.momentum import compute_momentum
from tests.factor_setup import SESSION, bar, closes, prior_sessions, signal_time


def test_default_momentum_breakpoints_stay_locked(repo_root) -> None:
    cfg = load_config(repo_root / "config" / "default.yaml", root=repo_root)
    assert cfg.momentum.raw_low == -0.05
    assert cfg.momentum.raw_high == 0.08
    assert cfg.momentum.overheat_r5 == 0.25
    assert cfg.momentum.overheat_multiplier == 0.5
    assert (cfg.momentum.w1, cfg.momentum.w3, cfg.momentum.w5) == (0.20, 0.35, 0.45)


def test_midpoint_raw_maps_to_half_the_weight(repo_root) -> None:
    cfg = load_config(repo_root / "config" / "default.yaml", root=repo_root)
    calendar = NYSECalendar()
    days = prior_sessions(calendar, 6)
    prices = [100, 100, 100, 100, 100, 101.5]
    result = compute_momentum(
        "AAA",
        closes("AAA", days, prices),
        session=SESSION,
        as_of=signal_time(),
        calendar=calendar,
        momentum=cfg.momentum,
        weight=cfg.weights.momentum,
    )
    assert result.raw_value == pytest.approx(0.015)
    assert result.score == pytest.approx(12.5)
    assert result.normalized_value == pytest.approx(0.5)
    assert result.details[-1] == ("overheated", "false")


def test_r5_above_25_percent_halves_the_score(repo_root) -> None:
    cfg = load_config(repo_root / "config" / "default.yaml", root=repo_root)
    calendar = NYSECalendar()
    days = prior_sessions(calendar, 6)
    result = compute_momentum(
        "AAA",
        closes("AAA", days, [100, 100, 100, 100, 100, 130]),
        session=SESSION,
        as_of=signal_time(),
        calendar=calendar,
        momentum=cfg.momentum,
        weight=cfg.weights.momentum,
    )
    assert result.raw_value == pytest.approx(0.30)
    assert result.score == pytest.approx(12.5)
    assert result.risks == ("R5 above 25%; momentum score halved",)


def test_r5_at_the_threshold_is_not_penalized(repo_root) -> None:
    cfg = load_config(repo_root / "config" / "default.yaml", root=repo_root)
    calendar = NYSECalendar()
    days = prior_sessions(calendar, 6)
    result = compute_momentum(
        "AAA",
        closes("AAA", days, [100, 100, 100, 100, 100, 125]),
        session=SESSION,
        as_of=signal_time(),
        calendar=calendar,
        momentum=cfg.momentum,
        weight=cfg.weights.momentum,
    )
    assert result.details[-1] == ("overheated", "false")
    assert result.score == pytest.approx(25)


def test_signal_day_bar_is_not_a_completed_session(repo_root) -> None:
    cfg = load_config(repo_root / "config" / "default.yaml", root=repo_root)
    calendar = NYSECalendar()
    days = prior_sessions(calendar, 5)
    bars = closes("AAA", days, [100, 100, 100, 100, 100])
    bars.append(bar("AAA", SESSION, 130))
    with pytest.raises(DataUnavailableError, match="5 < 6"):
        compute_momentum(
            "AAA",
            bars,
            session=SESSION,
            as_of=signal_time(),
            calendar=calendar,
            momentum=cfg.momentum,
            weight=cfg.weights.momentum,
        )


def test_split_inside_the_window_is_applied_and_a_future_split_is_not(repo_root) -> None:
    cfg = load_config(repo_root / "config" / "default.yaml", root=repo_root)
    calendar = NYSECalendar()
    days = prior_sessions(calendar, 6)
    bars = closes("AAA", days, [200, 200, 200, 110, 110, 110])
    split = Split(symbol="AAA", ex_date=days[3], old_rate=1, new_rate=2)
    adjusted = compute_momentum(
        "AAA",
        bars,
        session=SESSION,
        as_of=signal_time(),
        calendar=calendar,
        momentum=cfg.momentum,
        weight=cfg.weights.momentum,
        splits=(split,),
    )
    future = Split(symbol="AAA", ex_date=date(2024, 6, 21), old_rate=1, new_rate=2)
    ignored = compute_momentum(
        "AAA",
        bars,
        session=SESSION,
        as_of=signal_time(),
        calendar=calendar,
        momentum=cfg.momentum,
        weight=cfg.weights.momentum,
        splits=(future,),
    )
    assert float(adjusted.details[2][1]) == pytest.approx(0.10)
    assert float(ignored.details[2][1]) == pytest.approx(110 / 200 - 1)
