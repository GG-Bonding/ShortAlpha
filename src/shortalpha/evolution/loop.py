"""Open an experiment, then keep or promote after shadow results mature."""

import json
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from shortalpha.calendar import NYSECalendar
from shortalpha.config import AppConfig
from shortalpha.domain import SignalRun
from shortalpha.evaluation.fill import runs_by_ids, select_runs
from shortalpha.event_rules import EventRules, rules_to_json
from shortalpha.evolution.active import next_signal_at
from shortalpha.evolution.change import (
    EventScale,
    candidate_version,
    event_scale_from_json,
    scale_event_contribution,
)
from shortalpha.evolution.gates import (
    DayPoint,
    Leg,
    PromotionLimits,
    failure_reasons,
    sample_gaps,
    uncertainty,
    uncertainty_reason,
)
from shortalpha.evolution.inputs import input_fingerprint
from shortalpha.evolution.propose import Proposal, propose_event_scale
from shortalpha.evolution.rescore import rerank
from shortalpha.storage import Store

_KIND = "event_contribution"


def open_experiment(
    store: Store,
    cfg: AppConfig,
    rules: EventRules,
    runs: list[SignalRun],
    documents: dict[str, dict[str, object]],
    forwards: dict[str, list[object]],
    *,
    as_of: datetime,
    dev_end: date,
    validation_start: date,
    validation_end: date,
    run_mode: str,
    config_digest: str,
    event_rules_hash: str,
    calendar: NYSECalendar | None,
    opened_at: datetime | None = None,
) -> str:
    if calendar is None:
        raise ValueError("calendar is required")
    if dev_end >= validation_start or validation_end < validation_start:
        raise ValueError("validation window must start after development")
    _reject_consumed(store, validation_start, validation_end)
    opened = opened_at or datetime.now(UTC)
    if opened.tzinfo is None or opened.utcoffset() is None:
        raise ValueError("opened_at must be timezone-aware")
    zone = ZoneInfo(cfg.signal.timezone)
    cutoff = datetime.combine(validation_start, cfg.signal.time, tzinfo=zone)
    shadow_starts = next_signal_at(opened, calendar, cfg.signal.time, zone)
    baseline = store.official_version(cfg.strategy.version)
    usable = [run for run in runs if run.strategy_version == baseline and run.run_mode == run_mode]
    groups = _event_groups(
        usable,
        documents,
        forwards,
        horizon=cfg.evaluation.primary_horizon,
        start=None,
        end=dev_end,
        as_of=as_of,
        available_before=cutoff,
    )
    proposal = propose_event_scale(groups, rules, min_count=cfg.evaluation.min_proposal_count)
    if proposal is None:
        return "NO_PROPOSAL"
    change = EventScale(proposal.rule_id, proposal.scale)
    version = candidate_version(baseline, change)
    window = [run for run in usable if validation_start <= run.signal_date <= validation_end]
    points = _rescore_points(
        usable,
        documents,
        forwards,
        change,
        cfg,
        as_of=as_of,
        start=validation_start,
        end=validation_end,
    )
    if len(points) < len(window) or len(points) == 0:
        return "WAITING_LABELS"
    limits, top_n, horizon, cost, block_sessions = _limits(cfg)
    gaps = sample_gaps(points, limits=limits, block_sessions=block_sessions, calendar=calendar)
    report = _uncertainty(points, top_n, horizon, cost, block_sessions, calendar)
    waiting = _wait_reasons(gaps, uncertainty_reason(report))
    if waiting:
        return f"WAITING_SAMPLE {version} " + "; ".join(waiting)
    reasons = failure_reasons(
        points,
        top_n=top_n,
        horizon=horizon,
        cost=cost,
        block_sessions=block_sessions,
        calendar=calendar,
    )
    experiment_id = _experiment_id(baseline, version, dev_end, validation_end, run_mode)
    pinned = [
        run.run_id
        for run in sorted(usable, key=lambda item: item.signal_date)
        if dev_end >= run.signal_date or validation_start <= run.signal_date <= validation_end
    ]
    scaled = scale_event_contribution(rules, change)
    store.insert_experiment(
        experiment_id=experiment_id,
        created_at=opened,
        baseline_version=baseline,
        candidate_version=version,
        run_mode=run_mode,
        baseline_run_ids=json.dumps(pinned),
        change_json=_change_json(change),
        dev_end=dev_end,
        validation_start=validation_start,
        validation_end=validation_end,
        config_hash=config_digest,
        event_rules_hash=event_rules_hash,
        shadow_starts_at=shadow_starts,
        training_cutoff_at=cutoff,
        baseline_rules_json=rules_to_json(rules),
        promotion_json=_promotion_json(cfg),
    )
    store.insert_registry(
        strategy_version=version,
        recorded_at=opened,
        role="candidate",
        parent_version=baseline,
        config_hash=config_digest,
        event_rules_hash=event_rules_hash,
        change_json=_change_json(change),
        rules_json=rules_to_json(scaled),
    )
    if reasons:
        _record(
            store,
            experiment_id,
            as_of,
            action="KEEP_CURRENT",
            terminal=True,
            pairs=(),
            evidence={
                "stage": "validation",
                "reasons": list(reasons),
                "proposal": _proposal(proposal),
                "uncertainty": report,
                "training_cutoff_at": cutoff.isoformat(),
            },
        )
        return "KEEP_CURRENT " + "; ".join(reasons)
    return f"SHADOW {version} {experiment_id}"


def decide_experiment(
    store: Store,
    cfg: AppConfig,
    experiment_id: str,
    *,
    as_of: datetime,
    calendar: NYSECalendar | None,
) -> str:
    experiment = store.experiment(experiment_id)
    if experiment is None:
        raise ValueError(f"unknown experiment {experiment_id}")
    existing = store.decisions_for(experiment_id)
    terminal = [row for row in existing if int(row["terminal"]) == 1]
    if terminal:
        return f"{terminal[-1]['action']} already_recorded"
    change = event_scale_from_json(str(experiment["change_json"]))
    pinned = runs_by_ids(store, tuple(json.loads(experiment["baseline_run_ids"])))
    documents = _documents(store, pinned)
    forwards = {run.run_id: store.forward_for(run.run_id, as_of=as_of) for run in pinned}
    history = _rescore_points(
        pinned,
        documents,
        forwards,
        change,
        cfg,
        as_of=as_of,
        start=date.fromisoformat(experiment["validation_start"]),
        end=date.fromisoformat(experiment["validation_end"]),
    )
    limits, top_n, horizon, cost, block_sessions = _limits(cfg, experiment)
    history_report = _uncertainty(history, top_n, horizon, cost, block_sessions, calendar)
    history_wait = _wait_reasons(
        sample_gaps(history, limits=limits, block_sessions=block_sessions, calendar=calendar),
        uncertainty_reason(history_report),
    )
    if history_wait:
        _record(
            store,
            experiment_id,
            as_of,
            action="KEEP_CURRENT",
            terminal=False,
            pairs=(),
            evidence={
                "stage": "validation",
                "reasons": list(history_wait),
                "uncertainty": history_report,
            },
        )
        return "KEEP_CURRENT " + "; ".join(history_wait)
    history_reasons = failure_reasons(
        history,
        top_n=top_n,
        horizon=horizon,
        cost=cost,
        block_sessions=block_sessions,
        calendar=calendar,
    )
    if history_reasons:
        _record(
            store,
            experiment_id,
            as_of,
            action="KEEP_CURRENT",
            terminal=True,
            pairs=(),
            evidence={
                "stage": "validation",
                "reasons": list(history_reasons),
                "uncertainty": history_report,
            },
        )
        return "KEEP_CURRENT " + "; ".join(history_reasons)
    shadow_points, pairs = _shadow_points(
        store,
        experiment_id=experiment_id,
        baseline_version=str(experiment["baseline_version"]),
        candidate_version=str(experiment["candidate_version"]),
        opened_at=datetime.fromisoformat(str(experiment["created_at"])),
        shadow_starts_at=_optional_dt(experiment["shadow_starts_at"]),
        as_of=as_of,
        horizon=horizon,
    )
    shadow_report = _uncertainty(shadow_points, top_n, horizon, cost, block_sessions, calendar)
    shadow_wait = _wait_reasons(
        sample_gaps(shadow_points, limits=limits, block_sessions=block_sessions, calendar=calendar),
        uncertainty_reason(shadow_report),
    )
    if shadow_wait:
        _record(
            store,
            experiment_id,
            as_of,
            action="KEEP_CURRENT",
            terminal=False,
            pairs=pairs,
            evidence={
                "stage": "shadow",
                "reasons": list(shadow_wait),
                "uncertainty": shadow_report,
            },
        )
        return "KEEP_CURRENT " + "; ".join(shadow_wait)
    shadow_reasons = failure_reasons(
        shadow_points,
        top_n=top_n,
        horizon=horizon,
        cost=cost,
        block_sessions=block_sessions,
        calendar=calendar,
    )
    if shadow_reasons:
        _record(
            store,
            experiment_id,
            as_of,
            action="KEEP_CURRENT",
            terminal=True,
            pairs=pairs,
            evidence={
                "stage": "shadow",
                "reasons": list(shadow_reasons),
                "uncertainty": shadow_report,
            },
        )
        return "KEEP_CURRENT " + "; ".join(shadow_reasons)
    recorded = datetime.now(UTC)
    previous = store.official_version(str(experiment["baseline_version"]))
    if previous and previous != experiment["candidate_version"]:
        prior = store.latest_registry(previous)
        store.insert_registry(
            strategy_version=previous,
            recorded_at=recorded,
            role="retired",
            parent_version=None if prior is None else prior["parent_version"],
            config_hash=str(experiment["config_hash"]),
            event_rules_hash=str(experiment["event_rules_hash"]),
            change_json="{}" if prior is None else str(prior["change_json"]),
            rules_json=_frozen_rules(prior, str(experiment["baseline_rules_json"] or "")),
        )
    candidate_row = store.latest_registry(str(experiment["candidate_version"]))
    store.insert_registry(
        strategy_version=str(experiment["candidate_version"]),
        recorded_at=recorded,
        role="official",
        parent_version=str(experiment["baseline_version"]),
        config_hash=str(experiment["config_hash"]),
        event_rules_hash=str(experiment["event_rules_hash"]),
        change_json=str(experiment["change_json"]),
        rules_json="" if candidate_row is None else str(candidate_row["rules_json"] or ""),
    )
    _record(
        store,
        experiment_id,
        as_of,
        action="PROMOTE",
        terminal=True,
        pairs=pairs,
        evidence={
            "stage": "shadow",
            "reasons": [],
            "uncertainty": shadow_report,
        },
    )
    return f"PROMOTE {experiment['candidate_version']}"


def rollback_official(
    store: Store,
    version: str,
    *,
    recorded_at: datetime,
    config_digest: str,
    event_rules_hash: str,
) -> None:
    current = store.official_version("")
    if current and current != version:
        prior = store.latest_registry(current)
        store.insert_registry(
            strategy_version=current,
            recorded_at=recorded_at,
            role="retired",
            parent_version=None if prior is None else prior["parent_version"],
            config_hash=config_digest if prior is None else str(prior["config_hash"]),
            event_rules_hash=event_rules_hash if prior is None else str(prior["event_rules_hash"]),
            change_json="{}" if prior is None else str(prior["change_json"]),
            rules_json="" if prior is None else str(prior["rules_json"] or ""),
        )
    target = store.latest_registry(version)
    store.insert_registry(
        strategy_version=version,
        recorded_at=recorded_at,
        role="official",
        parent_version=None if target is None else target["parent_version"],
        config_hash=config_digest if target is None else str(target["config_hash"]),
        event_rules_hash=event_rules_hash if target is None else str(target["event_rules_hash"]),
        change_json="{}" if target is None else str(target["change_json"]),
        rules_json="" if target is None else str(target["rules_json"] or ""),
    )


def _reject_consumed(store: Store, start: date, end: date) -> None:
    for row in store.experiments():
        prior_start = date.fromisoformat(row["validation_start"])
        prior_end = date.fromisoformat(row["validation_end"])
        if prior_start <= end and start <= prior_end:
            raise ValueError(
                f"validation window overlaps consumed experiment {row['experiment_id']}"
            )


def _event_groups(
    runs: list[SignalRun],
    documents: dict[str, dict[str, object]],
    forwards: dict[str, list[object]],
    *,
    horizon: int,
    start: date | None,
    end: date,
    as_of: datetime,
    available_before: datetime | None = None,
) -> dict[str, list[float]]:
    groups: dict[str, list[float]] = {}
    for run in runs:
        if run.signal_date > end or (start is not None and run.signal_date < start):
            continue
        document = documents[run.run_id]
        by_symbol = _forward_map(
            forwards.get(run.run_id, []),
            horizon,
            as_of,
            available_before=available_before,
        )
        for row in document.get("ranked") or []:
            if not isinstance(row, dict):
                continue
            item = by_symbol.get(str(row["symbol"]))
            if item is None:
                continue
            for label in _labels(row):
                groups.setdefault(label, []).append(item[2])
    return groups


def _rescore_points(
    runs: list[SignalRun],
    documents: dict[str, dict[str, object]],
    forwards: dict[str, list[object]],
    change: EventScale,
    cfg: AppConfig,
    *,
    as_of: datetime,
    start: date,
    end: date,
) -> tuple[DayPoint, ...]:
    points: list[DayPoint] = []
    horizon = cfg.evaluation.primary_horizon
    for run in runs:
        if not start <= run.signal_date <= end:
            continue
        document = documents[run.run_id]
        by_symbol = _forward_map(forwards.get(run.run_id, []), horizon, as_of)
        baseline = _legs_from_snapshot(document, by_symbol)
        candidate_book = rerank(document, change, cfg, as_of)
        candidate = _legs_from_book(candidate_book, by_symbol)
        if baseline is None or candidate is None:
            continue
        ratio = _missing_ratio(run)
        points.append(
            DayPoint(
                signal_date=run.signal_date,
                baseline=baseline,
                candidate=candidate,
                baseline_missing_ratio=ratio,
                candidate_missing_ratio=ratio,
            )
        )
    return tuple(points)


def _shadow_points(
    store: Store,
    *,
    experiment_id: str,
    baseline_version: str,
    candidate_version: str,
    opened_at: datetime,
    shadow_starts_at: datetime | None,
    as_of: datetime,
    horizon: int,
) -> tuple[tuple[DayPoint, ...], tuple[dict[str, str], ...]]:
    if shadow_starts_at is None:
        return (), ()
    baseline_runs = {
        run.signal_date: run
        for run in select_runs(store, strategy_version=baseline_version, run_mode="live")
        if _is_forward_run(run, opened_at, shadow_starts_at) and run.experiment_id == ""
    }
    shadow_runs = [
        run
        for run in select_runs(store, strategy_version=candidate_version, run_mode="shadow")
        if run.experiment_id == experiment_id
        and run.signal_date in baseline_runs
        and _is_forward_run(run, opened_at, shadow_starts_at)
    ]
    shadow_runs.sort(key=lambda run: (run.signal_date, run.run_id))
    points: list[DayPoint] = []
    pairs: list[dict[str, str]] = []
    for shadow in shadow_runs:
        baseline = baseline_runs[shadow.signal_date]
        if baseline.signal_time != shadow.signal_time:
            continue
        if baseline.run_mode != "live" or shadow.run_mode != "shadow":
            continue
        base_doc = _one_document(store, baseline.run_id)
        shadow_doc = _one_document(store, shadow.run_id)
        fingerprint = input_fingerprint(base_doc)
        if fingerprint != input_fingerprint(shadow_doc):
            continue
        recorded_before = max(baseline.created_at, shadow.created_at)
        base_forward = _forward_map(
            store.forward_for(baseline.run_id, as_of=as_of),
            horizon,
            as_of,
            recorded_before=recorded_before,
        )
        shadow_forward = _forward_map(
            store.forward_for(shadow.run_id, as_of=as_of),
            horizon,
            as_of,
            recorded_before=recorded_before,
        )
        base_legs = _legs_from_snapshot(base_doc, base_forward)
        shadow_legs = _legs_from_snapshot(shadow_doc, shadow_forward)
        if base_legs is None or shadow_legs is None:
            continue
        points.append(
            DayPoint(
                signal_date=shadow.signal_date,
                baseline=base_legs,
                candidate=shadow_legs,
                baseline_missing_ratio=_missing_ratio(baseline),
                candidate_missing_ratio=_missing_ratio(shadow),
            )
        )
        pairs.append(
            {
                "baseline_run_id": baseline.run_id,
                "candidate_run_id": shadow.run_id,
                "input_hash": fingerprint,
            }
        )
    return tuple(points), tuple(pairs)


def _legs_from_snapshot(
    snapshot: dict[str, object], by_symbol: dict[str, tuple[float, float, float]]
) -> tuple[Leg, ...] | None:
    if snapshot.get("no_trade") is True:
        return ()
    rows = snapshot.get("symbols")
    if not isinstance(rows, list):
        return ()
    legs: list[Leg] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row["symbol"])
        prices = by_symbol.get(symbol)
        if prices is None:
            return None
        legs.append(Leg(symbol, prices[0], prices[1]))
    return tuple(legs)


def _legs_from_book(
    book, by_symbol: dict[str, tuple[float, float, float]]
) -> tuple[Leg, ...] | None:
    if book.no_trade:
        return ()
    legs: list[Leg] = []
    for row in book.published:
        prices = by_symbol.get(row.symbol)
        if prices is None:
            return None
        legs.append(Leg(row.symbol, prices[0], prices[1]))
    return tuple(legs)


def _forward_map(
    rows: list[object],
    horizon: int,
    as_of: datetime,
    *,
    available_before: datetime | None = None,
    recorded_before: datetime | None = None,
) -> dict[str, tuple[float, float, float]]:
    found: dict[str, tuple[float, float, float]] = {}
    for row in rows:
        if int(row["horizon"]) != horizon or not _visible(
            row,
            as_of,
            available_before=available_before,
            recorded_before=recorded_before,
        ):
            continue
        found[str(row["symbol"])] = (
            float(row["stock_return"]),
            float(row["spy_return"]),
            float(row["excess_return"]),
        )
    return found


def _visible(
    row: object,
    as_of: datetime,
    *,
    available_before: datetime | None = None,
    recorded_before: datetime | None = None,
) -> bool:
    keys = row.keys() if not isinstance(row, dict) else row
    if "label_available_at" not in keys:
        return False
    stamp = row["label_available_at"]
    if isinstance(stamp, str):
        stamp = datetime.fromisoformat(stamp)
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        return False
    if stamp > as_of:
        return False
    if available_before is not None and stamp >= available_before:
        return False
    if recorded_before is not None and stamp <= recorded_before:
        return False
    return True


def _labels(row: dict[str, object]) -> tuple[str, ...]:
    factors = row.get("factors")
    if not isinstance(factors, dict):
        return ()
    event = factors.get("event")
    if not isinstance(event, dict):
        return ()
    classified = event.get("classified_events")
    if not isinstance(classified, list):
        return ()
    labels: list[str] = []
    for item in classified:
        if not isinstance(item, dict):
            continue
        if float(item.get("signed") or 0) <= 0:
            continue
        label = str(item.get("label") or "")
        if label and label not in labels:
            labels.append(label)
    return tuple(labels)


def _missing_ratio(run: SignalRun) -> float:
    if not run.universe_size:
        return 1.0
    return run.missing_data_count / run.universe_size


def _documents(store: Store, runs: list[SignalRun]) -> dict[str, dict[str, object]]:
    return {run.run_id: _one_document(store, run.run_id) for run in runs}


def _one_document(store: Store, run_id: str) -> dict[str, object]:
    loaded = store.get_snapshot(run_id)
    if loaded is None:
        raise ValueError(f"snapshot missing for {run_id}")
    document = json.loads(loaded[0])
    if not isinstance(document, dict):
        raise ValueError(f"snapshot was not an object for {run_id}")
    return document


def _record(
    store: Store,
    experiment_id: str,
    as_of: datetime,
    *,
    action: str,
    terminal: bool,
    pairs: tuple[dict[str, str], ...],
    evidence: dict[str, object],
) -> None:
    recorded = datetime.now(UTC)
    store.insert_decision(
        decision_id=f"{experiment_id}:{recorded.timestamp()}:{action}",
        experiment_id=experiment_id,
        recorded_at=recorded,
        as_of=as_of,
        action=action,
        terminal=terminal,
        shadow_run_ids=json.dumps(list(pairs), sort_keys=True),
        evidence_json=json.dumps(evidence, sort_keys=True),
    )


def _change_json(change: EventScale) -> str:
    return json.dumps(
        {"kind": _KIND, "rule_id": change.rule_id, "scale": change.scale},
        sort_keys=True,
    )


def _uncertainty(
    points: tuple[DayPoint, ...],
    top_n: int,
    horizon: int,
    cost: float,
    block_sessions: int,
    calendar: NYSECalendar | None,
):
    return uncertainty(
        points,
        top_n=top_n,
        horizon=horizon,
        cost=cost,
        block_sessions=block_sessions,
        calendar=calendar,
    )


def _limits(
    cfg: AppConfig, experiment: object | None = None
) -> tuple[PromotionLimits, int, int, float, int]:
    raw = None if experiment is None else experiment["promotion_json"]
    if raw:
        body = json.loads(str(raw))
        return (
            PromotionLimits(
                int(body["min_mature_dates"]),
                int(body["min_effective_trades"]),
                int(body["min_complete_blocks"]),
            ),
            int(body["top_n"]),
            int(body["horizon"]),
            float(body["cost"]),
            int(body["block_sessions"]),
        )
    return (
        PromotionLimits(
            cfg.evaluation.min_mature_dates,
            cfg.evaluation.min_effective_trades,
            cfg.evaluation.min_complete_blocks,
        ),
        cfg.ranking.top_n,
        cfg.evaluation.primary_horizon,
        cfg.evaluation.round_trip_cost,
        cfg.evaluation.block_sessions,
    )


def _promotion_json(cfg: AppConfig) -> str:
    return json.dumps(
        {
            "block_sessions": cfg.evaluation.block_sessions,
            "cost": cfg.evaluation.round_trip_cost,
            "horizon": cfg.evaluation.primary_horizon,
            "min_complete_blocks": cfg.evaluation.min_complete_blocks,
            "min_effective_trades": cfg.evaluation.min_effective_trades,
            "min_mature_dates": cfg.evaluation.min_mature_dates,
            "top_n": cfg.ranking.top_n,
        },
        sort_keys=True,
    )


def _wait_reasons(gaps: tuple[str, ...], reason: str | None) -> tuple[str, ...]:
    found = list(gaps)
    if reason and reason not in found:
        found.append(reason)
    return tuple(found)


def _frozen_rules(row: object, fallback: str) -> str:
    if row is not None and row["rules_json"]:
        return str(row["rules_json"])
    return fallback


def _optional_dt(value: object) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value))


def _is_forward_run(run: SignalRun, opened_at: datetime, shadow_starts_at: datetime) -> bool:
    return run.created_at >= opened_at and run.signal_time >= shadow_starts_at


def _proposal(proposal: Proposal) -> dict[str, object]:
    return {
        "label": proposal.label,
        "rule_id": proposal.rule_id,
        "scale": proposal.scale,
        "strong_label": proposal.strong_label,
    }


def _experiment_id(
    baseline: str, version: str, dev_end: date, validation_end: date, run_mode: str
) -> str:
    return f"exp-{baseline}-{version}-{dev_end.isoformat()}-{validation_end.isoformat()}-{run_mode}"
