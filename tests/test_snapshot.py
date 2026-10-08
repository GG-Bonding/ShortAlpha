import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from shortalpha.cli import main
from shortalpha.config import load_config
from shortalpha.domain import FactorResult, MarketRegime, SignalLabel
from shortalpha.errors import ShortAlphaError
from shortalpha.paths import project_root
from shortalpha.scoring.rank import SymbolFactors, rank_symbols
from shortalpha.signal.explain import format_explanation, format_scan
from shortalpha.signal.snapshot import save_signal, snapshot_hash, write_snapshot_file
from shortalpha.storage import Store
from tests.factor_setup import signal_time


def _cfg():
    return load_config(project_root() / "config" / "default.yaml", root=project_root())


def _factor(name: str, score: float, *, reasons=(), risks=()) -> FactorResult:
    if name == "event":
        details = (
            ("event_risk", "NONE"),
            ("news_available_at", "2024-06-18T21:00:00-04:00"),
            ("post_event_reaction", "0.0100000000"),
            ("reaction_baseline_time", "2024-06-18T20:00:00-04:00"),
        )
    elif name == "price_action":
        details = (
            ("degraded", "false"),
            ("gap", "0.0680000000"),
            ("price_time", "2024-06-20T09:00:00-04:00"),
            ("prior_close_time", "2024-06-18T20:00:00-04:00"),
            ("price_consolidated", "true"),
        )
    else:
        details = ()
    return FactorResult(
        name=name,
        raw_value=score,
        normalized_value=score / 25,
        score=score,
        available=True,
        reasons=reasons,
        risks=risks,
        details=details,
    )


def _book():
    cfg = _cfg()
    scores = {
        "momentum": 21.23456,
        "volume": 17.8,
        "event": 23.4,
        "relative_strength": 13.9,
        "price_action": 11.9,
    }
    factors = []
    for name, score in scores.items():
        reasons = ("Earnings beat",) if name == "event" else ()
        risks = ("Premarket gap +6.8%",) if name == "price_action" else ()
        factors.append(_factor(name, score, reasons=reasons, risks=risks))
    row = SymbolFactors("NVDA", tuple(factors))
    return rank_symbols(
        [row],
        as_of=signal_time(),
        ranking=cfg.ranking,
        hard_filters=cfg.hard_filters,
        regime=MarketRegime.NORMAL,
        regime_cfg=cfg.regime,
        vix_available=True,
    )


def _save(store: Store, output_dir, *, created_at: datetime, run_id: str) -> str:
    cfg = _cfg()
    return save_signal(
        store,
        _book(),
        signal_date=signal_time().date(),
        signal_time=signal_time(),
        timezone="America/New_York",
        weights=cfg.weights,
        config_hash="hash",
        code_version="0.1.0",
        created_at=created_at,
        universe_size=1,
        eligible_size=1,
        provider_errors=0,
        missing_data_count=0,
        universe_list_as_of=None,
        point_in_time_membership=False,
        notes="",
        strategy_version="v0",
        run_mode="replay",
        event_rules_hash="rules",
        output_dir=output_dir,
        run_id=run_id,
    )


def test_a_second_run_keeps_the_first_snapshot_and_the_same_hash(tmp_path, repo_root) -> None:
    store = Store(tmp_path / "signals.db", repo_root / "migrations")
    output = tmp_path / "snapshots"
    first_at = datetime(2024, 6, 20, 13, 0, tzinfo=signal_time().tzinfo)
    second_at = first_at + timedelta(minutes=5)
    try:
        first = _save(store, output, created_at=first_at, run_id="run-a")
        second = _save(store, output, created_at=second_at, run_id="run-b")
        assert first != second
        loaded_a = store.get_snapshot("run-a")
        loaded_b = store.get_snapshot("run-b")
        assert loaded_a is not None and loaded_b is not None
        assert loaded_a[1] == loaded_b[1] == snapshot_hash(loaded_a[0])
        assert json.loads(loaded_a[0])["symbols"][0]["factors"]["momentum"][
            "score"
        ] == pytest.approx(21.2346)
        assert (output / "2024-06-20-run-a.json").read_text() == loaded_a[0]
        assert (output / "2024-06-20-run-b.json").read_text() == loaded_b[0]
        count = store.conn.execute(
            "SELECT COUNT(*) FROM factor_values WHERE run_id = 'run-a'"
        ).fetchone()[0]
        assert count == 5
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store.conn.execute("UPDATE signal_snapshots SET snapshot_hash = 'x'")
    finally:
        store.close()


def test_snapshot_file_is_not_overwritten(tmp_path) -> None:
    path = tmp_path / "snap.json"
    write_snapshot_file(path, '{"ok":true}')
    with pytest.raises(FileExistsError):
        write_snapshot_file(path, '{"ok":false}')
    assert path.read_text() == '{"ok":true}'


def test_explanation_includes_factor_scores_reasons_and_risks() -> None:
    text = format_explanation(
        json.loads(
            save_document(),
        ),
        "NVDA",
        run_id="run-a",
    )
    assert "Score: 88.2346" in text
    assert "Signal: LONG_CANDIDATE" in text
    assert "Momentum        21.2346 / 25" in text
    assert "+ Earnings beat" in text
    assert "- Premarket gap +6.8%" in text


def test_scan_text_lists_reasons_risks_and_the_no_trade_flag() -> None:
    document = json.loads(save_document())
    text = format_scan(document)
    assert "2024-06-20 09:00 ET" in text
    assert "Market:" in text
    assert "NORMAL" in text
    assert "#1 NVDA" in text
    assert "+ Earnings beat" in text
    assert "- Premarket gap +6.8%" in text
    assert "Prior expectation is unknown" in text
    assert "Positioning is unknown" in text
    assert "Same-day price:" in text
    assert "premarket gap +6.8%" in text
    assert "Setup: continuation." in text
    assert text.endswith("NO_TRADE:\nfalse")


def test_scan_counts_names_that_lack_a_same_day_price() -> None:
    text = format_scan(
        {
            "signal_date": "2024-06-20",
            "signal_time": "09:00:00",
            "market_regime": "NORMAL",
            "no_trade": True,
            "symbols": [],
            "ranked": [
                {"thesis_veto": "same-day price confirmation is missing"},
                {"thesis_veto": "same-day price confirmation is missing"},
                {"thesis_veto": "no positive classified change"},
            ],
        }
    )
    assert "Same-day price missing:\n2" in text
    assert text.endswith("NO_TRADE:\ntrue")


def test_missing_symbol_is_an_error() -> None:
    with pytest.raises(ShortAlphaError, match="not in this snapshot"):
        format_explanation(json.loads(save_document()), "AAPL", run_id="run-a")


def test_explain_command_uses_the_later_run(tmp_path, repo_root, capsys) -> None:
    store = Store(tmp_path / "signals.db", repo_root / "migrations")
    first_at = datetime(2024, 6, 20, 13, 0, tzinfo=signal_time().tzinfo)
    try:
        _save(store, None, created_at=first_at, run_id="run-a")
        _save(store, None, created_at=first_at + timedelta(minutes=1), run_id="run-b")
    finally:
        store.close()
    code = main(
        [
            "explain",
            "nvda",
            "--date",
            "2024-06-20",
            "--config",
            str(repo_root / "config" / "default.yaml"),
            "--database",
            str(tmp_path / "signals.db"),
        ]
    )
    assert code == 0
    assert "run_id: run-b" in capsys.readouterr().out


def save_document() -> str:
    """Build one canonical snapshot without touching SQLite."""
    from shortalpha.signal.snapshot import build_snapshot, canonical_snapshot

    cfg = _cfg()
    document = build_snapshot(
        _book(),
        signal_date=signal_time().date(),
        signal_time=signal_time(),
        timezone="America/New_York",
        weights=cfg.weights,
    )
    text = canonical_snapshot(document)
    assert json.loads(text)["symbols"][0]["signal"] == SignalLabel.LONG_CANDIDATE.value
    return text
