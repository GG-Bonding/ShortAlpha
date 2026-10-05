import json
from datetime import date, datetime

import pytest

from shortalpha.calendar import NYSECalendar
from shortalpha.config import load_config
from shortalpha.domain import ClassifiedEvent, FactorResult, MarketRegime, SignalRun
from shortalpha.evaluation.book import day_contribution
from shortalpha.evaluation.fill import select_runs
from shortalpha.event_rules import load_event_rules
from shortalpha.evolution.change import EventScale, candidate_version, scale_event_contribution
from shortalpha.evolution.gates import DayPoint, Leg, failure_reasons
from shortalpha.evolution.loop import decide_experiment, open_experiment, rollback_official
from shortalpha.evolution.propose import propose_event_scale
from shortalpha.paths import project_root
from shortalpha.scoring.rank import SymbolFactors, rank_symbols
from shortalpha.signal.snapshot import save_signal
from shortalpha.storage import Store
from tests.factor_setup import NY

DEV = date(2024, 6, 3)
VAL = (date(2024, 6, 4), date(2024, 6, 5))
SHADOW = (date(2024, 6, 6), date(2024, 6, 7))
AS_OF = datetime(2024, 6, 20, 16, 0, tzinfo=NY)
LABEL_AT = datetime(2024, 6, 12, 16, 0, tzinfo=NY)
ROOT = project_root()


def _cfg():
    return load_config(ROOT / "config" / "default.yaml", root=ROOT)


def _run(run_id: str, day: date, *, version: str = "v0", mode: str = "replay") -> SignalRun:
    return SignalRun(
        run_id=run_id,
        signal_date=day,
        signal_time=datetime(day.year, day.month, day.day, 9, 0, tzinfo=NY),
        timezone="America/New_York",
        config_hash="hash",
        code_version="0.1.0",
        created_at=datetime(day.year, day.month, day.day, 13, 0, tzinfo=NY),
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
    )


def _event(rule_id: str, label: str, signed: float) -> dict[str, object]:
    return {
        "available_at": "2024-06-03T13:00:00-04:00",
        "content_sha256": "abc",
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
            else {"degraded": "true"}
            if name == "price_action"
            else {"event_risk": "NONE"}
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


def test_shadow_can_promote_and_rollback_keeps_the_old_version(tmp_path, repo_root) -> None:
    cfg = _cfg()
    rules = load_event_rules(ROOT / "config" / "event_rules.yaml")
    store = Store(tmp_path / "evolve.db", repo_root / "migrations")
    target = EventScale("price_target_raise", 0.5)
    version = candidate_version("v0", target)
    try:
        _store_case(store)
        documents, forwards = _loaded(store, ("dev", "val-1", "val-2"))
        message = open_experiment(
            store,
            cfg,
            rules,
            [store.get_run(run_id) for run_id in ("dev", "val-1", "val-2")],
            documents,
            forwards,
            as_of=AS_OF,
            dev_end=DEV,
            validation_start=VAL[0],
            validation_end=VAL[1],
            run_mode="replay",
            config_digest="hash",
            event_rules_hash="rules",
            calendar=NYSECalendar(),
        )
        assert message.startswith(f"SHADOW {version} ")
        experiment_id = message.split()[-1]
        with pytest.raises(ValueError, match="overlaps"):
            open_experiment(
                store,
                cfg,
                rules,
                [],
                {},
                {},
                as_of=AS_OF,
                dev_end=DEV,
                validation_start=VAL[0],
                validation_end=VAL[1],
                run_mode="replay",
                config_digest="hash",
                event_rules_hash="rules",
                calendar=NYSECalendar(),
            )
        early = decide_experiment(store, cfg, experiment_id, as_of=AS_OF, calendar=NYSECalendar())
        assert early == "KEEP_CURRENT shadow labels are not mature"
        _store_shadow(store, version)
        promoted = decide_experiment(
            store, cfg, experiment_id, as_of=AS_OF, calendar=NYSECalendar()
        )
        assert promoted == f"PROMOTE {version}"
        assert store.official_version("v0") == version
        again = decide_experiment(store, cfg, experiment_id, as_of=AS_OF, calendar=NYSECalendar())
        assert again.startswith("PROMOTE")
        assert again.endswith("already_recorded")
        rollback_official(
            store,
            "v0",
            recorded_at=datetime(2026, 10, 6, 12, 0, tzinfo=NY),
            config_digest="hash",
            event_rules_hash="rules",
        )
        assert store.official_version("v0") == "v0"
        scaled = scale_event_contribution(rules, target)
        assert scaled.types != rules.types
        assert cfg.ranking.long_threshold == 80
        assert cfg.ranking.top_n == 3
    finally:
        store.close()


def _store_case(store: Store) -> None:
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
    _insert(store, _run("dev", DEV), _book(dev_rows, []), dev_forward)
    validation = _validation_rows(price, guide)
    for run_id, day in zip(("val-1", "val-2"), VAL, strict=True):
        _insert(
            store,
            _run(run_id, day),
            _book(validation, ["PTA", "GDA", "PTB"]),
            _validation_forwards(),
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


def _store_shadow(store: Store, version: str) -> None:
    price = _event("price_target_raise", "Price target raise", 1.0)
    guide = _event("guidance_raise", "Guidance raised", 0.2)
    rows = _validation_rows(price, guide)
    forwards = _validation_forwards()
    for index, day in enumerate(SHADOW):
        _insert(
            store,
            _run(f"live-{index}", day, mode="live"),
            _book(rows, ["PTA", "GDA", "PTB"]),
            forwards,
        )
        _insert(
            store,
            _run(f"shadow-{index}", day, version=version, mode="shadow"),
            _book(rows, ["GDA", "GDB", "PTA"]),
            forwards,
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
            label_available_at=LABEL_AT,
        )


def _loaded(store: Store, run_ids: tuple[str, ...]):
    documents = {}
    forwards = {}
    for run_id in run_ids:
        loaded = store.get_snapshot(run_id)
        assert loaded is not None
        documents[run_id] = json.loads(loaded[0])
        forwards[run_id] = store.forward_for(run_id, as_of=AS_OF)
    return documents, forwards
