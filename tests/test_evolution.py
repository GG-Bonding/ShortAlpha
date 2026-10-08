import json
from dataclasses import replace
from datetime import date, datetime

import pytest

from shortalpha.calendar import NYSECalendar
from shortalpha.cli import main
from shortalpha.config import StrategyConfig, load_config
from shortalpha.domain import ClassifiedEvent, FactorResult, MarketRegime, SignalRun
from shortalpha.evaluation.book import day_contribution
from shortalpha.evaluation.fill import select_runs
from shortalpha.event_rules import load_event_rules, rules_to_json
from shortalpha.evolution.active import resolve_strategy
from shortalpha.evolution.change import EventScale, candidate_version, scale_event_contribution
from shortalpha.evolution.gates import (
    DayPoint,
    Leg,
    PromotionLimits,
    failure_reasons,
    sample_gaps,
    uncertainty,
)
from shortalpha.evolution.inputs import input_fingerprint
from shortalpha.evolution.loop import decide_experiment, open_experiment, rollback_official
from shortalpha.evolution.propose import propose_event_scale
from shortalpha.paths import project_root
from shortalpha.replay.engine import SessionResult
from shortalpha.scoring.rank import SymbolFactors, rank_symbols
from shortalpha.signal.snapshot import save_signal
from shortalpha.storage import Store
from tests.factor_setup import NY

DEV = date(2024, 6, 3)
AS_OF = datetime(2024, 6, 20, 16, 0, tzinfo=NY)
LABEL_AT = datetime(2024, 6, 12, 16, 0, tzinfo=NY)
DEV_DAY = date(2024, 5, 1)
LATE_DAY = date(2024, 5, 31)
DEV_END = date(2024, 5, 31)
VAL_START = date(2024, 6, 3)
VAL_DAYS = (
    date(2024, 6, 3),
    date(2024, 6, 4),
    date(2024, 6, 5),
    date(2024, 6, 6),
    date(2024, 6, 7),
    date(2024, 6, 10),
)
SHADOW_DAYS = (
    date(2024, 6, 17),
    date(2024, 6, 18),
    date(2024, 6, 20),
    date(2024, 6, 21),
    date(2024, 6, 24),
    date(2024, 6, 25),
)
OPENED_AT = datetime(2024, 6, 14, 16, 0, tzinfo=NY)
EARLY_LABEL = datetime(2024, 5, 10, 16, 0, tzinfo=NY)
LATE_LABEL = datetime(2024, 6, 5, 16, 0, tzinfo=NY)
VAL_LABEL = OPENED_AT
SHADOW_LABEL = datetime(2024, 7, 2, 16, 0, tzinfo=NY)
DECIDE_AT = datetime(2024, 7, 3, 16, 0, tzinfo=NY)
ROOT = project_root()


def _cfg():
    return load_config(ROOT / "config" / "default.yaml", root=ROOT)


def _run(
    run_id: str,
    day: date,
    *,
    version: str = "v0",
    mode: str = "replay",
    created_at: datetime | None = None,
    experiment_id: str = "",
) -> SignalRun:
    return SignalRun(
        run_id=run_id,
        signal_date=day,
        signal_time=datetime(day.year, day.month, day.day, 9, 0, tzinfo=NY),
        timezone="America/New_York",
        config_hash="hash",
        code_version="0.1.0",
        created_at=created_at or datetime(day.year, day.month, day.day, 13, 0, tzinfo=NY),
        universe_size=10,
        eligible_size=10,
        scored_size=4,
        candidate_size=3,
        duration_ms=1,
        provider_errors=0,
        missing_data_count=0,
        market_regime=None,
        no_trade=False,
        status="ok",
        universe_list_as_of=None,
        point_in_time_membership=False,
        notes="",
        strategy_version=version,
        run_mode=mode,
        event_rules_hash="rules",
        experiment_id=experiment_id,
    )


def _event(rule_id: str, label: str, signed: float, *, content: str = "abc") -> dict[str, object]:
    return {
        "available_at": "2024-06-03T13:00:00-04:00",
        "content_sha256": content,
        "direction": 1,
        "family": rule_id,
        "freshness": 1,
        "importance": 0.4,
        "label": label,
        "news_id": f"{rule_id}-{label}",
        "published_at": "2024-06-03T12:00:00-04:00",
        "reaction": 0.01,
        "rule_id": rule_id,
        "rules_sha256": "rules",
        "signed": signed,
        "source": "fixture",
    }


def _factors(scores: dict[str, float], event: dict[str, object]) -> dict[str, object]:
    factors = {}
    for name, score in scores.items():
        factors[name] = {
            "classified_events": [event] if name == "event" else [],
            "details": {"overheated": "false"}
            if name == "momentum"
            else {
                "degraded": "false",
                "gap": "0.0200000000",
                "price_time": "2024-06-20T09:00:00-04:00",
                "prior_close_time": "2024-06-18T20:00:00-04:00",
                "price_consolidated": "true",
            }
            if name == "price_action"
            else {
                "event_risk": "NONE",
                "news_available_at": "2024-06-18T21:00:00-04:00",
                "post_event_reaction": "0.0100000000",
                "reaction_baseline_time": "2024-06-18T20:00:00-04:00",
            }
            if name == "event"
            else {},
            "normalized_value": None,
            "raw_value": 0.02 if name == "relative_strength" else score,
            "reasons": [event["label"]] if name == "event" else [],
            "risks": [],
            "score": score,
            "weight": 25,
        }
    return factors


def _name(symbol: str, scores: dict[str, float], event: dict[str, object]) -> dict[str, object]:
    return {
        "factors": _factors(scores, event),
        "rank": 1,
        "signal": "LONG_CANDIDATE",
        "symbol": symbol,
        "thesis": "",
        "thesis_veto": None,
        "total_score": sum(scores.values()),
    }


def _book(rows: list[dict[str, object]], published: list[str]) -> dict[str, object]:
    by_symbol = {row["symbol"]: row for row in rows}
    return {
        "market_regime": "NORMAL",
        "no_trade": False,
        "no_trade_reasons": [],
        "ranked": rows,
        "signal_date": "2024-06-04",
        "signal_time": "09:00:00",
        "symbols": [by_symbol[symbol] for symbol in published],
        "timezone": "America/New_York",
        "vix_available": False,
    }


def _forward(symbol: str, stock: float, excess: float) -> dict[str, object]:
    return {
        "symbol": symbol,
        "horizon": 3,
        "stock_return": stock,
        "spy_return": stock - excess,
        "excess_return": excess,
        "mae": -0.01,
        "mfe": 0.02,
        "label_available_at": LABEL_AT.isoformat(),
    }


def test_a_classified_event_is_stored_with_the_run(tmp_path, repo_root) -> None:
    cfg = _cfg()
    published = datetime(2024, 6, 18, 15, 0, tzinfo=NY)
    observed = ClassifiedEvent(
        news_id="n1",
        rule_id="earnings_beat",
        label="Earnings beat",
        family="earnings",
        source="fixture",
        published_at=published,
        available_at=published,
        content_sha256="abc",
        rules_sha256="rules",
        signed=0.2,
        direction=1,
        importance=0.9,
        freshness=1,
        reaction=0.1,
    )
    factors = []
    for name, score in (
        ("momentum", 20),
        ("volume", 15),
        ("event", 25),
        ("relative_strength", 15),
        ("price_action", 15),
    ):
        factors.append(
            FactorResult(
                name=name,
                raw_value=0.02 if name == "relative_strength" else score,
                normalized_value=None,
                score=score,
                available=True,
                reasons=("Earnings beat",) if name == "event" else (),
                details=(("event_risk", "NONE"),) if name == "event" else (),
                events=(observed,) if name == "event" else (),
            )
        )
    book = rank_symbols(
        [SymbolFactors("AAA", tuple(factors))],
        as_of=datetime(2024, 6, 20, 9, 0, tzinfo=NY),
        ranking=cfg.ranking,
        hard_filters=cfg.hard_filters,
        regime=MarketRegime.NORMAL,
        regime_cfg=cfg.regime,
        vix_available=False,
    )
    store = Store(tmp_path / "events.db", repo_root / "migrations")
    try:
        save_signal(
            store,
            book,
            signal_date=DEV,
            signal_time=datetime(2024, 6, 3, 9, 0, tzinfo=NY),
            timezone="America/New_York",
            weights=cfg.weights,
            config_hash="hash",
            code_version="0.1.0",
            created_at=datetime(2024, 6, 3, 13, 0, tzinfo=NY),
            universe_size=1,
            eligible_size=1,
            provider_errors=0,
            missing_data_count=0,
            universe_list_as_of=None,
            point_in_time_membership=False,
            notes="",
            strategy_version="v0",
            run_mode="live",
            event_rules_hash="rules",
            run_id="run-a",
        )
        row = store.conn.execute(
            """
            SELECT news_id, rule_id, content_sha256, rules_sha256, reaction
            FROM event_observations
            """
        ).fetchone()
        assert row["news_id"] == "n1"
        assert row["rule_id"] == "earnings_beat"
        assert row["content_sha256"] == "abc"
        assert row["rules_sha256"] == "rules"
        assert row["reaction"] == pytest.approx(0.1)
    finally:
        store.close()


def test_a_new_name_uses_one_slot_not_the_whole_account() -> None:
    net, excess = day_contribution([(0.09, 0.0)], top_n=3, horizon=3, cost=0.0)
    assert net == pytest.approx(0.01)
    assert excess == pytest.approx(0.01)


def test_versions_and_modes_do_not_share_a_date(tmp_path, repo_root) -> None:
    store = Store(tmp_path / "runs.db", repo_root / "migrations")
    try:
        store.insert_run(_run("old", DEV, version="v0", mode="replay"))
        store.insert_run(_run("new", DEV, version="v1", mode="replay"))
        store.insert_run(_run("live", DEV, version="v0", mode="live"))
        replay = select_runs(store, strategy_version="v0", run_mode="replay")
        assert [run.run_id for run in replay] == ["old"]
    finally:
        store.close()


def test_a_future_label_is_hidden_when_reading(tmp_path, repo_root) -> None:
    store = Store(tmp_path / "labels.db", repo_root / "migrations")
    try:
        store.insert_run(_run("run-a", DEV))
        store.insert_forward_return(
            run_id="run-a",
            symbol="AAA",
            horizon=3,
            entry_session=DEV,
            entry_price=10,
            exit_session=date(2024, 6, 6),
            exit_price=11,
            stock_return=0.1,
            spy_return=0,
            excess_return=0.1,
            mae=-0.01,
            mfe=0.02,
            code_version="0.1.0",
            label_available_at=LABEL_AT,
        )
        assert store.forward_for("run-a", as_of=datetime(2024, 6, 11, 16, 0, tzinfo=NY)) == []
        assert len(store.forward_for("run-a", as_of=LABEL_AT)) == 1
    finally:
        store.close()


def test_proposal_uses_only_the_supplied_groups() -> None:
    rules = load_event_rules(ROOT / "config" / "event_rules.yaml")
    groups = {
        "Price target raise": [0.0] * 20,
        "Guidance raised": [0.02] * 20,
    }
    proposal = propose_event_scale(groups, rules, min_count=20)
    assert proposal is not None
    assert proposal.rule_id == "price_target_raise"
    assert proposal.scale == 0.5
    assert propose_event_scale({"Price target raise": [0.0] * 20}, rules, min_count=20) is None


def test_one_symbol_cannot_clear_the_promotion_gates() -> None:
    day = DayPoint(
        date(2024, 6, 4),
        baseline=(Leg("AAA", 0.2, 0.0),),
        candidate=(Leg("AAA", 0.2, 0.0), Leg("BBB", 0.05, 0.0)),
        baseline_missing_ratio=0,
        candidate_missing_ratio=0,
    )
    other = DayPoint(
        date(2024, 6, 5),
        baseline=(Leg("AAA", 0.2, 0.0),),
        candidate=(Leg("AAA", 0.2, 0.0), Leg("BBB", 0.05, 0.0)),
        baseline_missing_ratio=0,
        candidate_missing_ratio=0,
    )
    reasons = failure_reasons(
        (day, other),
        top_n=3,
        horizon=3,
        cost=0.001,
        block_sessions=3,
        calendar=NYSECalendar(),
    )
    assert "one symbol accounts for the improvement" in reasons


def test_two_dates_do_not_estimate_an_improvement() -> None:
    baseline = (Leg("PTA", 0.0, 0.0), Leg("GDA", 0.0, 0.0), Leg("PTB", -0.02, 0.0))
    candidate = (Leg("GDA", 0.0, 0.0), Leg("GDB", 0.022, 0.0), Leg("PTA", 0.0, 0.0))
    points = (
        DayPoint(date(2024, 6, 4), baseline, candidate, 0.0, 0.0),
        DayPoint(date(2024, 6, 5), baseline, candidate, 0.0, 0.0),
    )
    reasons = failure_reasons(
        points, top_n=3, horizon=3, cost=0.001, block_sessions=3, calendar=NYSECalendar()
    )
    report = uncertainty(
        points, top_n=3, horizon=3, cost=0.001, block_sessions=3, calendar=NYSECalendar()
    )
    gaps = sample_gaps(
        points,
        limits=PromotionLimits(6, 6, 2),
        block_sessions=3,
        calendar=NYSECalendar(),
    )
    assert reasons == ()
    assert report["blocks"] == "INSUFFICIENT SAMPLE"
    assert "improvement_low_excess" not in report
    assert "fewer than 6 mature dates" in gaps
    assert "fewer than 2 complete blocks" in gaps


def test_late_development_labels_do_not_open_a_short_window(tmp_path, repo_root) -> None:
    cfg = _cfg()
    rules = load_event_rules(ROOT / "config" / "event_rules.yaml")
    store = Store(tmp_path / "short.db", repo_root / "migrations")
    try:
        message = _open_window(store, cfg, rules, VAL_DAYS[:2])
        assert message.startswith("WAITING_SAMPLE ")
        assert "price_target_raise" in message
        assert "guidance_raise" not in message
        assert "fewer than 6 mature dates" in message
        assert store.experiments() == []
    finally:
        store.close()


def test_two_forward_days_stay_waiting(tmp_path, repo_root) -> None:
    cfg = _cfg()
    rules = load_event_rules(ROOT / "config" / "event_rules.yaml")
    store = Store(tmp_path / "two.db", repo_root / "migrations")
    try:
        version, experiment_id = _ready(store, cfg, rules)
        _store_pairs(store, version, experiment_id, SHADOW_DAYS[:2], prefix="two")
        decision = decide_experiment(
            store, cfg, experiment_id, as_of=DECIDE_AT, calendar=NYSECalendar()
        )
        assert decision.startswith("KEEP_CURRENT ")
        assert "PROMOTE" not in decision
        assert "fewer than 6 mature dates" in decision
        assert store.official_version("v0") == "v0"
        recorded = store.decisions_for(experiment_id)[-1]
        assert int(recorded["terminal"]) == 0
    finally:
        store.close()


def test_backdated_shadow_runs_cannot_promote(tmp_path, repo_root) -> None:
    cfg = _cfg()
    rules = load_event_rules(ROOT / "config" / "event_rules.yaml")
    store = Store(tmp_path / "old.db", repo_root / "migrations")
    try:
        version, experiment_id = _ready(store, cfg, rules)
        _store_pairs(
            store,
            version,
            experiment_id,
            SHADOW_DAYS,
            prefix="old",
            created_at=datetime(2024, 5, 1, 13, 0, tzinfo=NY),
        )
        decision = decide_experiment(
            store, cfg, experiment_id, as_of=DECIDE_AT, calendar=NYSECalendar()
        )
        assert not decision.startswith("PROMOTE")
        assert store.official_version("v0") == "v0"
    finally:
        store.close()


def test_replay_mode_cannot_fill_the_forward_window(tmp_path, repo_root) -> None:
    cfg = _cfg()
    rules = load_event_rules(ROOT / "config" / "event_rules.yaml")
    store = Store(tmp_path / "replay.db", repo_root / "migrations")
    try:
        version, experiment_id = _ready(store, cfg, rules)
        _store_pairs(store, version, experiment_id, SHADOW_DAYS, prefix="replay", mode="replay")
        decision = decide_experiment(
            store, cfg, experiment_id, as_of=DECIDE_AT, calendar=NYSECalendar()
        )
        assert not decision.startswith("PROMOTE")
        assert store.official_version("v0") == "v0"
    finally:
        store.close()


def test_runs_recorded_after_the_label_cannot_promote(tmp_path, repo_root) -> None:
    cfg = _cfg()
    rules = load_event_rules(ROOT / "config" / "event_rules.yaml")
    store = Store(tmp_path / "late.db", repo_root / "migrations")
    try:
        version, experiment_id = _ready(store, cfg, rules)
        _store_pairs(
            store,
            version,
            experiment_id,
            SHADOW_DAYS,
            prefix="after",
            created_at=datetime(2024, 7, 3, 16, 0, tzinfo=NY),
        )
        decision = decide_experiment(
            store, cfg, experiment_id, as_of=DECIDE_AT, calendar=NYSECalendar()
        )
        assert not decision.startswith("PROMOTE")
        assert store.official_version("v0") == "v0"
    finally:
        store.close()


def test_shadow_pairs_must_share_inputs(tmp_path, repo_root) -> None:
    cfg = _cfg()
    rules = load_event_rules(ROOT / "config" / "event_rules.yaml")
    store = Store(tmp_path / "inputs.db", repo_root / "migrations")
    try:
        version, experiment_id = _ready(store, cfg, rules)
        _store_pairs(store, version, experiment_id, SHADOW_DAYS, prefix="mis", content="different")
        live = store.get_snapshot("mis-live-0")
        shadow = store.get_snapshot("mis-shadow-0")
        assert live is not None and shadow is not None
        assert input_fingerprint(json.loads(live[0])) != input_fingerprint(json.loads(shadow[0]))
        decision = decide_experiment(
            store, cfg, experiment_id, as_of=DECIDE_AT, calendar=NYSECalendar()
        )
        assert not decision.startswith("PROMOTE")
        assert store.official_version("v0") == "v0"
    finally:
        store.close()


def test_forward_shadow_promotes_and_rollback_restores_rules(tmp_path, repo_root) -> None:
    cfg = _cfg()
    rules = load_event_rules(ROOT / "config" / "event_rules.yaml")
    store = Store(tmp_path / "forward.db", repo_root / "migrations")
    try:
        version, experiment_id = _ready(store, cfg, rules)
        stored = store.experiment(experiment_id)
        assert stored is not None
        assert datetime.fromisoformat(stored["training_cutoff_at"]) == datetime(
            2024, 6, 3, 9, 0, tzinfo=NY
        )
        assert datetime.fromisoformat(stored["shadow_starts_at"]) == datetime(
            2024, 6, 17, 9, 0, tzinfo=NY
        )
        with pytest.raises(ValueError, match="overlaps"):
            open_experiment(
                store,
                cfg,
                rules,
                [],
                {},
                {},
                as_of=OPENED_AT,
                dev_end=DEV_END,
                validation_start=VAL_START,
                validation_end=VAL_DAYS[-1],
                run_mode="replay",
                config_digest="hash",
                event_rules_hash="rules",
                calendar=NYSECalendar(),
                opened_at=OPENED_AT,
            )
        early = decide_experiment(
            store, cfg, experiment_id, as_of=DECIDE_AT, calendar=NYSECalendar()
        )
        assert early.startswith("KEEP_CURRENT ")
        assert int(store.decisions_for(experiment_id)[-1]["terminal"]) == 0
        _store_pairs(store, version, experiment_id, SHADOW_DAYS, prefix="good")
        sabotaged = replace(
            cfg,
            evaluation=replace(
                cfg.evaluation,
                round_trip_cost=1.0,
                min_mature_dates=100,
                min_complete_blocks=9,
            ),
        )
        promoted = decide_experiment(
            store, sabotaged, experiment_id, as_of=DECIDE_AT, calendar=NYSECalendar()
        )
        assert promoted == f"PROMOTE {version}"
        assert store.official_version("v0") == version
        evidence = json.loads(store.decisions_for(experiment_id)[-1]["evidence_json"])
        assert evidence["uncertainty"]["improvement_low_excess"] > 0
        pairs = json.loads(store.decisions_for(experiment_id)[-1]["shadow_run_ids"])
        assert len(pairs) == 6
        assert pairs[0]["baseline_run_id"] == "good-live-0"
        assert pairs[0]["candidate_run_id"] == "good-shadow-0"
        resolved, active, _digest = resolve_strategy(
            store, cfg, rules, requested=None, file_hash="rules"
        )
        assert resolved == version
        assert _importance(active, "price_target_raise") == pytest.approx(0.2)
        renamed = replace(cfg, strategy=StrategyConfig(version))
        _same, renamed_rules, _digest = resolve_strategy(
            store, renamed, rules, requested=version, file_hash="rules"
        )
        assert _importance(renamed_rules, "price_target_raise") == pytest.approx(0.2)
        again = decide_experiment(
            store, cfg, experiment_id, as_of=DECIDE_AT, calendar=NYSECalendar()
        )
        assert again.endswith("already_recorded")
        rollback_official(
            store,
            "v0",
            recorded_at=datetime(2099, 1, 1, tzinfo=NY),
            config_digest="hash",
            event_rules_hash="rules",
        )
        restored_version, restored, _digest = resolve_strategy(
            store,
            cfg,
            scale_event_contribution(rules, EventScale("price_target_raise", 0.5)),
            requested=None,
            file_hash="rules",
        )
        assert restored_version == "v0"
        assert store.official_version("v0") == "v0"
        assert _importance(restored, "price_target_raise") == pytest.approx(0.4)
        assert (
            _rule(restored, "price_target_raise").patterns
            == _rule(rules, "price_target_raise").patterns
        )
        assert cfg.ranking.long_threshold == 80
        assert cfg.ranking.top_n == 3
    finally:
        store.close()


def _ready(store: Store, cfg, rules) -> tuple[str, str]:
    message = _open_window(store, cfg, rules, VAL_DAYS)
    assert message.startswith("SHADOW ")
    version, experiment_id = message.split()[1:]
    return version, experiment_id


def _open_window(store: Store, cfg, rules, days: tuple[date, ...]) -> str:
    _store_history(store, days)
    ids = ("dev", "late", *[f"val-{index}" for index in range(len(days))])
    documents, forwards = _loaded(store, ids, as_of=OPENED_AT)
    runs = []
    for run_id in ids:
        run = store.get_run(run_id)
        assert run is not None
        runs.append(run)
    return open_experiment(
        store,
        cfg,
        rules,
        runs,
        documents,
        forwards,
        as_of=OPENED_AT,
        dev_end=DEV_END,
        validation_start=VAL_START,
        validation_end=days[-1],
        run_mode="replay",
        config_digest="hash",
        event_rules_hash="rules",
        calendar=NYSECalendar(),
        opened_at=OPENED_AT,
    )


def _store_history(store: Store, days: tuple[date, ...]) -> None:
    price = _event("price_target_raise", "Price target raise", 1.0)
    guide = _event("guidance_raise", "Guidance raised", 0.2)
    dev_rows = []
    dev_forward = []
    for index in range(20):
        symbol = f"P{index:02d}"
        dev_rows.append(_name(symbol, _flat(), price))
        dev_forward.append(_forward(symbol, 0.0, 0.0))
        guide_symbol = f"G{index:02d}"
        dev_rows.append(_name(guide_symbol, _flat(), guide))
        dev_forward.append(_forward(guide_symbol, 0.02, 0.02))
    _insert(store, _run("dev", DEV_DAY), _book(dev_rows, []), dev_forward, EARLY_LABEL)
    late_rows = []
    late_forward = []
    for index in range(20):
        symbol = f"L{index:02d}"
        late_rows.append(_name(symbol, _flat(), price))
        late_forward.append(_forward(symbol, 0.08, 0.08))
    _insert(store, _run("late", LATE_DAY), _book(late_rows, []), late_forward, LATE_LABEL)
    validation = _validation_rows(price, guide)
    forwards = _validation_forwards()
    for index, day in enumerate(days):
        _insert(
            store,
            _run(f"val-{index}", day),
            _book(validation, ["PTA", "GDA", "PTB"]),
            forwards,
            VAL_LABEL,
        )


def _validation_rows(price: dict[str, object], guide: dict[str, object]) -> list[dict[str, object]]:
    return [
        _name("PTA", _scores(25, 20, 15, 15, 15), price),
        _name("GDA", _scores(15, 25, 20, 15, 13), guide),
        _name("PTB", _scores(25, 16.5, 15, 15, 15), price),
        _name("GDB", _scores(15, 25, 20, 15, 9), guide),
    ]


def _scores(event: float, momentum: float, volume: float, relative: float, price: float):
    return {
        "momentum": momentum,
        "volume": volume,
        "event": event,
        "relative_strength": relative,
        "price_action": price,
    }


def _validation_forwards() -> list[dict[str, object]]:
    return [
        _forward("PTA", 0.0, 0.0),
        _forward("GDA", 0.0, 0.0),
        _forward("PTB", -0.02, -0.02),
        _forward("GDB", 0.022, 0.022),
    ]


def _store_pairs(
    store: Store,
    version: str,
    experiment_id: str,
    days: tuple[date, ...],
    *,
    prefix: str,
    created_at: datetime | None = None,
    mode: str = "shadow",
    content: str = "abc",
) -> None:
    live_rows = _validation_rows(
        _event("price_target_raise", "Price target raise", 1.0),
        _event("guidance_raise", "Guidance raised", 0.2),
    )
    shadow_rows = _validation_rows(
        _event("price_target_raise", "Price target raise", 1.0, content=content),
        _event("guidance_raise", "Guidance raised", 0.2, content=content),
    )
    forwards = _validation_forwards()
    for index, day in enumerate(days):
        _insert(
            store,
            _run(f"{prefix}-live-{index}", day, mode="live", created_at=created_at),
            _book(live_rows, ["PTA", "GDA", "PTB"]),
            forwards,
            SHADOW_LABEL,
        )
        _insert(
            store,
            _run(
                f"{prefix}-shadow-{index}",
                day,
                version=version,
                mode=mode,
                created_at=created_at,
                experiment_id=experiment_id,
            ),
            _book(shadow_rows, ["GDA", "GDB", "PTA"]),
            forwards,
            SHADOW_LABEL,
        )


def _flat() -> dict[str, float]:
    return {
        "momentum": 10,
        "volume": 10,
        "event": 10,
        "relative_strength": 10,
        "price_action": 10,
    }


def _insert(
    store: Store,
    run: SignalRun,
    document: dict[str, object],
    forwards: list[dict[str, object]],
    label_at: datetime,
) -> None:
    text = json.dumps(document, sort_keys=True)
    store.insert_run(run)
    store.insert_snapshot(run.run_id, text, "hash", run.created_at)
    for row in forwards:
        store.insert_forward_return(
            run_id=run.run_id,
            symbol=str(row["symbol"]),
            horizon=3,
            entry_session=run.signal_date,
            entry_price=10,
            exit_session=date(2024, 6, 10),
            exit_price=10,
            stock_return=float(row["stock_return"]),
            spy_return=float(row["spy_return"]),
            excess_return=float(row["excess_return"]),
            mae=-0.01,
            mfe=0.02,
            code_version="0.1.0",
            label_available_at=label_at,
        )


def _loaded(store: Store, run_ids: tuple[str, ...], *, as_of: datetime):
    documents = {}
    forwards = {}
    for run_id in run_ids:
        loaded = store.get_snapshot(run_id)
        assert loaded is not None
        documents[run_id] = json.loads(loaded[0])
        forwards[run_id] = store.forward_for(run_id, as_of=as_of)
    return documents, forwards


def _rule(rules, rule_id: str):
    return next(rule for rule in rules.types if rule.id == rule_id)


def _importance(rules, rule_id: str) -> float:
    return _rule(rules, rule_id).importance


def test_default_scan_uses_the_frozen_official_rules(tmp_path, repo_root, monkeypatch) -> None:
    rules = load_event_rules(ROOT / "config" / "event_rules.yaml")
    version = candidate_version("v0", EventScale("price_target_raise", 0.5))
    scaled = scale_event_contribution(rules, EventScale("price_target_raise", 0.5))
    database = tmp_path / "scan.db"
    store = Store(database, repo_root / "migrations")
    store.insert_registry(
        strategy_version="v0",
        recorded_at=datetime(2024, 1, 1, tzinfo=NY),
        role="retired",
        parent_version=None,
        config_hash="hash",
        event_rules_hash="rules",
        change_json="{}",
        rules_json=rules_to_json(rules),
    )
    store.insert_registry(
        strategy_version=version,
        recorded_at=datetime(2024, 6, 1, tzinfo=NY),
        role="official",
        parent_version="v0",
        config_hash="hash",
        event_rules_hash="rules",
        change_json=json.dumps(
            {"kind": "event_contribution", "rule_id": "price_target_raise", "scale": 0.5},
            sort_keys=True,
        ),
        rules_json=rules_to_json(scaled),
    )
    store.close()
    seen: list[tuple[str, float]] = []

    def fake_run_session(*_args, **kwargs):
        importance = _importance(kwargs["rules"], "price_target_raise")
        seen.append((kwargs["strategy_version"], importance))
        used = kwargs["store"]
        now = datetime(2024, 6, 20, 13, 0, tzinfo=NY)
        run = _run(
            f"scan-{len(seen)}",
            date(2024, 6, 20),
            version=kwargs["strategy_version"],
            mode="live",
            created_at=now,
        )
        used.insert_run(run)
        used.insert_snapshot(
            run.run_id,
            json.dumps(
                {
                    "market_regime": "NORMAL",
                    "no_trade": True,
                    "signal_date": "2024-06-20",
                    "signal_time": "09:00:00",
                    "symbols": [],
                }
            ),
            "hash",
            now,
        )
        return SessionResult(
            signal_date=date(2024, 6, 20),
            run_id=run.run_id,
            snapshot_hash="hash",
            no_trade=True,
            candidate_size=0,
            provider_errors=0,
            missing_data_count=0,
        )

    monkeypatch.setattr("shortalpha.cli.run_session", fake_run_session)
    argv = ["scan", "--date", "2024-06-20", "--database", str(database)]
    assert main(argv) == 0
    renamed = tmp_path / "renamed.yaml"
    text = (repo_root / "config" / "default.yaml").read_text()
    renamed.write_text(text.replace("version: v0", f"version: {version}", 1))
    assert main([*argv, "--config", str(renamed)]) == 0
    assert [item[0] for item in seen[:2]] == [version, version]
    assert seen[0][1] == pytest.approx(0.2)
    assert seen[1][1] == pytest.approx(0.2)
    reopened = Store(database, repo_root / "migrations")
    rollback_official(
        reopened,
        "v0",
        recorded_at=datetime(2099, 1, 1, tzinfo=NY),
        config_digest="hash",
        event_rules_hash="rules",
    )
    reopened.close()
    assert main(argv) == 0
    assert seen[2][0] == "v0"
    assert seen[2][1] == pytest.approx(0.4)


def test_shadow_scan_requires_a_forward_window(tmp_path, repo_root, capsys) -> None:
    database = tmp_path / "shadow.db"
    store = Store(database, repo_root / "migrations")
    rules = load_event_rules(ROOT / "config" / "event_rules.yaml")
    store.insert_experiment(
        experiment_id="exp-1",
        created_at=OPENED_AT,
        baseline_version="v0",
        candidate_version="v0.event.price_target_raise.x0.5",
        run_mode="replay",
        baseline_run_ids="[]",
        change_json="{}",
        dev_end=DEV_END,
        validation_start=VAL_START,
        validation_end=VAL_DAYS[-1],
        config_hash="hash",
        event_rules_hash="rules",
        shadow_starts_at=datetime(2024, 6, 17, 9, 0, tzinfo=NY),
        training_cutoff_at=datetime(2024, 6, 3, 9, 0, tzinfo=NY),
        baseline_rules_json=rules_to_json(rules),
        promotion_json="{}",
    )
    store.close()
    missing = main(
        ["scan", "--run-mode", "shadow", "--date", "2024-06-20", "--database", str(database)]
    )
    assert missing == 1
    assert "requires --experiment" in capsys.readouterr().err
    early = main(
        [
            "scan",
            "--run-mode",
            "shadow",
            "--experiment",
            "exp-1",
            "--date",
            "2024-06-06",
            "--database",
            str(database),
        ]
    )
    assert early == 1
    assert "forward window" in capsys.readouterr().err
