import pytest

from shortalpha.calendar import NYSECalendar
from shortalpha.config import load_config
from shortalpha.errors import DataUnavailableError
from shortalpha.factors.relative_strength import (
    compute_relative_strength,
    load_sector_map,
    sector_etf_for,
)
from tests.factor_setup import SESSION, closes, prior_sessions, signal_time


def test_sector_map_uses_the_sp500_gics_snapshot(repo_root) -> None:
    mapping = load_sector_map(repo_root / "config" / "sector_map.yaml")
    assert mapping["AAPL"] == "XLK"
    assert mapping["JPM"] == "XLF"
    assert mapping["XOM"] == "XLE"
    as_of = signal_time()
    with pytest.raises(DataUnavailableError, match="SHOP") as caught:
        sector_etf_for("SHOP", mapping, as_of)
    assert caught.value.provider == "sector_map"
    assert caught.value.operation == "relative_strength"


def test_relative_strength_blends_market_and_sector(repo_root) -> None:
    cfg = load_config(repo_root / "config" / "default.yaml", root=repo_root)
    calendar = NYSECalendar()
    days = prior_sessions(calendar, 6)
    stock = closes("NVDA", days, [100, 100, 100, 100, 100, 110])
    flat = [100, 100, 100, 100, 100, 100]
    result = compute_relative_strength(
        "NVDA",
        stock,
        closes("SPY", days, flat),
        closes("SOXX", days, flat),
        session=SESSION,
        as_of=signal_time(),
        calendar=calendar,
        relative=cfg.relative_strength,
        weight=cfg.weights.relative_strength,
        market_symbol="SPY",
        sector_etf="SOXX",
    )
    assert result.raw_value == pytest.approx(0.10)
    assert result.score == pytest.approx(15)
    assert "Outperforming SPY" in result.reasons
    assert "Outperforming SOXX" in result.reasons


def test_zero_spread_is_the_midpoint(repo_root) -> None:
    cfg = load_config(repo_root / "config" / "default.yaml", root=repo_root)
    calendar = NYSECalendar()
    days = prior_sessions(calendar, 6)
    flat = closes("NVDA", days, [100] * 6)
    result = compute_relative_strength(
        "NVDA",
        flat,
        closes("SPY", days, [50] * 6),
        closes("SOXX", days, [20] * 6),
        session=SESSION,
        as_of=signal_time(),
        calendar=calendar,
        relative=cfg.relative_strength,
        weight=cfg.weights.relative_strength,
        market_symbol="SPY",
        sector_etf="SOXX",
    )
    assert result.raw_value == pytest.approx(0)
    assert result.score == pytest.approx(7.5)


def test_missing_benchmark_history_is_an_error(repo_root) -> None:
    cfg = load_config(repo_root / "config" / "default.yaml", root=repo_root)
    calendar = NYSECalendar()
    days = prior_sessions(calendar, 6)
    with pytest.raises(DataUnavailableError, match="SPY") as caught:
        compute_relative_strength(
            "NVDA",
            closes("NVDA", days, [100] * 6),
            closes("SPY", days[:5], [100] * 5),
            closes("SOXX", days, [100] * 6),
            session=SESSION,
            as_of=signal_time(),
            calendar=calendar,
            relative=cfg.relative_strength,
            weight=cfg.weights.relative_strength,
            market_symbol="SPY",
            sector_etf="SOXX",
        )
    assert caught.value.symbol == "SPY"
    assert caught.value.operation == "relative_strength"
