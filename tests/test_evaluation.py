from datetime import date, datetime, time

import pytest

from shortalpha.calendar import NYSECalendar
from shortalpha.cli import main
from shortalpha.config import load_config
from shortalpha.domain import DailyBar, FactorResult, MarketRegime
from shortalpha.evaluation.fill import fill_forward_returns
from shortalpha.evaluation.report import render_evaluation
from shortalpha.paths import project_root
from shortalpha.providers.fixture_market import FixtureMarketDataProvider
from shortalpha.scoring.rank import SymbolFactors, rank_symbols
from shortalpha.signal.snapshot import save_signal
from shortalpha.storage import Store
from tests.factor_setup import NY

_FACTORS = ("momentum", "volume", "event", "relative_strength", "price_action")


def _cfg():
    return load_config(project_root() / "config" / "default.yaml", root=project_root())


def _snapshot(rows: list[dict[str, object]]) -> dict[str, object]:
    return {"ranked": rows}


def _symbol(symbol: str, score: float, momentum: float) -> dict[str, object]:
    factors = {name: {"score": momentum if name == "momentum" else 1.0} for name in _FACTORS}
    return {"symbol": symbol, "total_score": score, "factors": factors}


def _forward(symbol: str, stock: float, excess: float) -> dict[str, object]:
    return {
        "symbol": symbol,
        "horizon": 1,
        "stock_return": stock,
        "excess_return": excess,
        "mae": -0.02,
        "mfe": 0.03,
    }


def _render(snapshot: dict[str, object], rows: list[dict[str, object]]) -> str:
    cfg = _cfg()
    return render_evaluation(
        [(snapshot, rows)],
        long_threshold=cfg.ranking.long_threshold,
        horizons=cfg.evaluation.horizons,
        buckets=cfg.evaluation.buckets,
        min_bucket_count=cfg.evaluation.min_bucket_count,
    )


def test_a_higher_score_that_earns_less_is_reported_as_failure() -> None:
    text = _render(
        _snapshot(
            [
                _symbol("HIGH", 90, 20),
                _symbol("LOW", 50, 5),
            ]
        ),
        [_forward("HIGH", -0.1, -0.1), _forward("LOW", 0.2, 0.2)],
    )
    assert "HIGH SCORE DOES NOT BEAT LOW SCORE" in text
    assert "hit_rate: 0.5000" in text
    assert "5D" in text
    assert "completed: 0" in text


def test_all_winners_leave_the_win_loss_ratio_undefined() -> None:
    text = _render(
        _snapshot([_symbol("AAA", 90, 1), _symbol("BBB", 91, 1)]),
        [_forward("AAA", 0.1, 0.1), _forward("BBB", 0.2, 0.2)],
    )
    assert "win_loss: UNDEFINED" in text
    assert "momentum: spearman=UNDEFINED" in text


def test_monotonic_buckets_and_a_broken_ladder() -> None:
    cfg = _cfg()
    rows = []
    forwards = []
    for bucket in (80, 85, 90, 95):
        for offset in range(5):
            symbol = f"S{bucket}{offset}"
            score = float(min(bucket + offset, 100))
            excess = score / 10000
            rows.append(_symbol(symbol, score, score))
            forwards.append(_forward(symbol, excess, excess))
    text = render_evaluation(
        [(_snapshot(rows), forwards)],
        long_threshold=cfg.ranking.long_threshold,
        horizons=cfg.evaluation.horizons,
        buckets=cfg.evaluation.buckets,
        min_bucket_count=5,
    )
    assert "1D monotonic: MONOTONIC" in text
    assert "NO MONOTONIC RELATIONSHIP" not in text
    assert "momentum: spearman=1.0000" in text

    broken = []
    for row, item in zip(rows, forwards, strict=True):
        excess = item["excess_return"]
        if float(row["total_score"]) >= 90:
            excess = 0.0
        broken.append({**item, "excess_return": excess, "stock_return": excess})
    failed = render_evaluation(
        [(_snapshot(rows), broken)],
        long_threshold=cfg.ranking.long_threshold,
        horizons=cfg.evaluation.horizons,
        buckets=cfg.evaluation.buckets,
        min_bucket_count=5,
    )
    assert "1D monotonic: NO MONOTONIC RELATIONSHIP" in failed


def test_fill_writes_only_completed_horizons_and_does_not_repeat(tmp_path, repo_root) -> None:
    cfg = _cfg()
    store = Store(tmp_path / "eval.db", repo_root / "migrations")
    session = date(2024, 6, 20)
    calendar = NYSECalendar()
    try:
        factors = tuple(
            FactorResult(
                name=name,
                raw_value=16,
                normalized_value=0.8,
                score=16,
                available=True,
                details=(("event_risk", "NONE"),) if name == "event" else (),
            )
            for name in _FACTORS
        )
        book = rank_symbols(
            [SymbolFactors("AAA", factors)],
            as_of=datetime(2024, 6, 20, 9, 0, tzinfo=NY),
            ranking=cfg.ranking,
            hard_filters=cfg.hard_filters,
            regime=MarketRegime.NORMAL,
            regime_cfg=cfg.regime,
            vix_available=False,
        )
        run_id = save_signal(
            store,
            book,
            signal_date=session,
            signal_time=datetime(2024, 6, 20, 9, 0, tzinfo=NY),
            timezone="America/New_York",
            weights=cfg.weights,
            config_hash="hash",
            code_version="0.1.0",
            created_at=datetime(2024, 6, 20, 13, 0, tzinfo=NY),
            universe_size=1,
            eligible_size=1,
            provider_errors=0,
            missing_data_count=0,
            universe_list_as_of=None,
            point_in_time_membership=False,
            notes="",
        )
        run = store.get_run(run_id)
        loaded = store.get_snapshot(run_id)
        assert run is not None and loaded is not None
        market = FixtureMarketDataProvider(
            [
                _bar("AAA", session, 100),
                _bar("AAA", date(2024, 6, 21), 110),
                _bar("SPY", session, 50),
                _bar("SPY", date(2024, 6, 21), 50),
            ]
        )
        document = __import__("json").loads(loaded[0])
        inserted = fill_forward_returns(
            store,
            run,
            document,
            market,
            calendar=calendar,
            as_of=datetime(2024, 6, 28, 16, 0, tzinfo=NY),
            spy_symbol="SPY",
            splits_for=lambda symbol, start, end, as_of: (),
        )
        assert inserted == 1
        assert (
            fill_forward_returns(
                store,
                run,
                document,
                market,
                calendar=calendar,
                as_of=datetime(2024, 6, 28, 16, 0, tzinfo=NY),
                spy_symbol="SPY",
                splits_for=lambda symbol, start, end, as_of: (),
            )
            == 0
        )
        rows = store.forward_for(run_id)
        assert [int(row["horizon"]) for row in rows] == [1]
        assert rows[0]["excess_return"] == pytest.approx(0.1)
    finally:
        store.close()


def test_evaluate_command_on_an_empty_database(tmp_path, repo_root, capsys) -> None:
    code = main(
        [
            "evaluate",
            "--as-of",
            "2024-06-28",
            "--config",
            str(repo_root / "config" / "default.yaml"),
            "--database",
            str(tmp_path / "empty.db"),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "runs: 0" in out
    assert "INSUFFICIENT SAMPLE" in out


def _bar(symbol: str, day: date, price: float) -> DailyBar:
    stamp = datetime.combine(day, time(16, 0), tzinfo=NY)
    return DailyBar(
        symbol=symbol,
        session_date=day,
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=1_000_000,
        event_time=stamp,
        published_at=stamp,
        available_at=stamp,
        source="fixture",
    )
