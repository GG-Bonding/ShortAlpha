from datetime import datetime, time

import pytest

from shortalpha.calendar import NYSECalendar
from shortalpha.config import VolumeConfig, load_config
from shortalpha.domain import PremarketWindow
from shortalpha.errors import DataUnavailableError
from shortalpha.factors.volume import compute_volume
from shortalpha.paths import project_root
from shortalpha.providers.fixture_premarket import FixturePreMarketProvider
from tests.factor_setup import NY, SESSION, bar, prior_sessions, signal_time


def _volume_config() -> VolumeConfig:
    return load_config(project_root() / "config" / "default.yaml", root=project_root()).volume


def _daily(volumes: list[float], *, extra_signal_volume: float | None = None):
    calendar = NYSECalendar()
    days = prior_sessions(calendar, len(volumes))
    bars = [bar("AAA", day, 100, volume=volume) for day, volume in zip(days, volumes, strict=True)]
    if extra_signal_volume is not None:
        bars.append(bar("AAA", SESSION, 100, volume=extra_signal_volume))
    return calendar, bars


def _window(day, volume: float, *, available: bool = True, available_at=None, reason: str = ""):
    stamp = available_at or datetime.combine(day, time(9, 0), tzinfo=NY)
    return PremarketWindow(
        symbol="AAA",
        session_date=day,
        available=available,
        volume=volume if available else None,
        last_price=100 if available and volume > 0 else None,
        high=100 if available and volume > 0 else None,
        low=100 if available and volume > 0 else None,
        event_time=stamp if available else None,
        published_at=stamp if available else None,
        available_at=stamp if available else None,
        reason=reason or ("" if available else "unavailable"),
    )


def test_default_volume_breakpoints_stay_locked() -> None:
    cfg = _volume_config()
    assert cfg.lookback == 20
    assert cfg.ratio_low == 0.5
    assert cfg.ratio_high == 3.0


def test_daily_ratio_excludes_the_numerator_and_the_signal_session() -> None:
    volumes = [100.0] * 20 + [300.0]
    calendar, bars = _daily(volumes, extra_signal_volume=9_000_000)
    provider = FixturePreMarketProvider(
        [_window(SESSION, 1, available=False, reason="feed has no premarket")]
    )
    result = compute_volume(
        "AAA",
        bars,
        provider,
        session=SESSION,
        as_of=signal_time(),
        calendar=calendar,
        volume=_volume_config(),
        weight=20,
    )
    assert result.raw_value == pytest.approx(3.0)
    assert result.score == pytest.approx(20)
    assert result.degraded is True
    assert dict(result.details)["premarket_volume_available"] == "false"
    assert result.reasons == ("Volume 3.0x",)


def test_ratio_maps_linearly_and_clamps() -> None:
    calendar, quiet = _daily([100.0] * 20 + [50.0])
    _, mid = _daily([100.0] * 20 + [175.0])
    _, hot = _daily([100.0] * 20 + [400.0])
    provider = FixturePreMarketProvider(
        [_window(SESSION, 1, available=False, reason="feed has no premarket")]
    )
    cfg = _volume_config()

    def score(bars):
        return compute_volume(
            "AAA",
            bars,
            provider,
            session=SESSION,
            as_of=signal_time(),
            calendar=calendar,
            volume=cfg,
            weight=20,
        ).score

    assert score(quiet) == pytest.approx(0)
    assert score(mid) == pytest.approx(10)
    assert score(hot) == pytest.approx(20)


def test_premarket_ratio_is_preferred_over_the_daily_print() -> None:
    calendar, bars = _daily([1.0] * 21)
    days = prior_sessions(calendar, 20) + [SESSION]
    windows = [_window(day, 100 if day != SESSION else 300) for day in days]
    result = compute_volume(
        "AAA",
        bars,
        FixturePreMarketProvider(windows),
        session=SESSION,
        as_of=signal_time(),
        calendar=calendar,
        volume=_volume_config(),
        weight=20,
    )
    assert result.raw_value == pytest.approx(3.0)
    assert result.score == pytest.approx(20)
    assert result.degraded is False
    assert dict(result.details)["premarket_volume_available"] == "true"
    assert result.reasons == ("Premarket volume 3.0x",)


def test_a_premarket_print_after_the_signal_is_not_used() -> None:
    calendar, bars = _daily([100.0] * 20 + [300.0])
    late = datetime.combine(SESSION, time(9, 15), tzinfo=NY)
    days = prior_sessions(calendar, 20)
    windows = [_window(day, 1) for day in days]
    windows.append(_window(SESSION, 9_000, available_at=late))
    result = compute_volume(
        "AAA",
        bars,
        FixturePreMarketProvider(windows),
        session=SESSION,
        as_of=signal_time(),
        calendar=calendar,
        volume=_volume_config(),
        weight=20,
    )
    assert result.raw_value == pytest.approx(3.0)
    assert dict(result.details)["premarket_volume_available"] == "false"


def test_zero_baseline_is_an_error() -> None:
    calendar, bars = _daily([0.0] * 20 + [100.0])
    provider = FixturePreMarketProvider(
        [_window(SESSION, 1, available=False, reason="feed has no premarket")]
    )
    with pytest.raises(DataUnavailableError, match="zero") as caught:
        compute_volume(
            "AAA",
            bars,
            provider,
            session=SESSION,
            as_of=signal_time(),
            calendar=calendar,
            volume=_volume_config(),
            weight=20,
        )
    assert caught.value.symbol == "AAA"
    assert caught.value.operation == "volume"


def test_short_history_is_an_error() -> None:
    calendar, bars = _daily([100.0] * 5)
    provider = FixturePreMarketProvider(
        [_window(SESSION, 1, available=False, reason="feed has no premarket")]
    )
    with pytest.raises(DataUnavailableError, match="insufficient history"):
        compute_volume(
            "AAA",
            bars,
            provider,
            session=SESSION,
            as_of=signal_time(),
            calendar=calendar,
            volume=_volume_config(),
            weight=20,
        )
