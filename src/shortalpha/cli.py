"""Command line."""

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from shortalpha import __version__
from shortalpha.calendar import calendar_from_config
from shortalpha.capture import capture_from_json, git_revision
from shortalpha.config import AppConfig, config_hash, load_config
from shortalpha.domain import Split
from shortalpha.errors import ConfigError, DataUnavailableError, ShortAlphaError
from shortalpha.evaluation.fill import fill_forward_returns, runs_by_ids, select_runs
from shortalpha.evaluation.outcomes import format_outcomes
from shortalpha.evaluation.report import render_evaluation
from shortalpha.event_rules import event_rules_content_hash, load_event_rules
from shortalpha.evolution.active import (
    resolve_shadow,
    resolve_strategy,
    signal_is_in_forward_window,
)
from shortalpha.evolution.loop import decide_experiment, open_experiment, rollback_official
from shortalpha.factors.relative_strength import load_sector_map
from shortalpha.logging_utils import log_failure
from shortalpha.paths import project_root
from shortalpha.providers.alpaca_corporate import AlpacaCorporateActionsProvider
from shortalpha.providers.factory import (
    build_market_provider,
    build_news_provider,
    build_premarket_provider,
    build_universe_provider,
)
from shortalpha.replay.engine import replay, replay_saved, run_session
from shortalpha.signal.coverage import format_coverage
from shortalpha.signal.explain import format_explanation, format_scan
from shortalpha.storage import Store
from shortalpha.universe import LiquidityStatus, evaluate_liquidity, format_number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shortalpha")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version")
    check = sub.add_parser("check")
    _add_config_arg(check)
    check.add_argument("--date", default=None)
    check.add_argument("--database", default=None)
    universe = sub.add_parser("universe")
    _add_config_arg(universe)
    universe.add_argument("--date", default=None)
    scan = sub.add_parser("scan")
    _add_config_arg(scan)
    scan.add_argument("--date", default=None)
    scan.add_argument("--database", default=None)
    scan.add_argument("--run-mode", default="live", choices=("live", "replay", "shadow"))
    scan.add_argument("--strategy-version", default=None)
    scan.add_argument("--experiment", default=None)
    explain = sub.add_parser("explain")
    _add_config_arg(explain)
    explain.add_argument("symbol")
    explain.add_argument("--date", required=True)
    explain.add_argument("--run-id", default=None)
    explain.add_argument("--database", default=None)
    replay_cmd = sub.add_parser("replay")
    _add_config_arg(replay_cmd)
    replay_cmd.add_argument("--from", dest="start", required=True)
    replay_cmd.add_argument("--to", dest="end", required=True)
    replay_cmd.add_argument("--database", default=None)
    evaluate = sub.add_parser("evaluate")
    _add_config_arg(evaluate)
    evaluate.add_argument("--as-of", default=None)
    evaluate.add_argument("--database", default=None)
    evaluate.add_argument("--strategy-version", default=None)
    evaluate.add_argument("--run-mode", default="live", choices=("live", "replay", "shadow"))
    evaluate.add_argument("--experiment", default=None)
    coverage = sub.add_parser("coverage")
    _add_config_arg(coverage)
    coverage.add_argument("--date", required=True)
    coverage.add_argument("--run-id", default=None)
    coverage.add_argument("--database", default=None)
    outcomes = sub.add_parser("outcomes")
    _add_config_arg(outcomes)
    outcomes.add_argument("--as-of", default=None)
    outcomes.add_argument("--database", default=None)
    outcomes.add_argument("--run-mode", default="live", choices=("live", "replay", "shadow"))
    replay_inputs = sub.add_parser("replay-inputs")
    _add_config_arg(replay_inputs)
    replay_inputs.add_argument("--run-id", required=True)
    replay_inputs.add_argument("--database", default=None)
    evolve = sub.add_parser("evolve")
    evolve_sub = evolve.add_subparsers(dest="evolve_command", required=True)
    propose = evolve_sub.add_parser("propose")
    _add_config_arg(propose)
    propose.add_argument("--database", default=None)
    propose.add_argument("--as-of", required=True)
    propose.add_argument("--dev-end", required=True)
    propose.add_argument("--validation-start", required=True)
    propose.add_argument("--validation-end", required=True)
    propose.add_argument("--run-mode", default="replay", choices=("live", "replay", "shadow"))
    decide = evolve_sub.add_parser("decide")
    _add_config_arg(decide)
    decide.add_argument("--database", default=None)
    decide.add_argument("--as-of", required=True)
    decide.add_argument("--experiment", required=True)
    rollback = evolve_sub.add_parser("rollback")
    _add_config_arg(rollback)
    rollback.add_argument("--database", default=None)
    rollback.add_argument("--to-version", required=True)
    try:
        args = parser.parse_args(argv)
        if args.command == "version":
            print(f"shortalpha {__version__}")
            return 0
        if args.command == "universe":
            return _universe(args)
        if args.command == "explain":
            return _explain(args)
        if args.command == "scan":
            return _scan(args)
        if args.command == "replay":
            return _replay(args)
        if args.command == "evaluate":
            return _evaluate(args)
        if args.command == "coverage":
            return _coverage(args)
        if args.command == "outcomes":
            return _outcomes(args)
        if args.command == "replay-inputs":
            return _replay_inputs(args)
        if args.command == "evolve":
            return _evolve(args)
        return _check(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except ShortAlphaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=None)


def _load(args: argparse.Namespace) -> tuple[Path, AppConfig]:
    root = project_root()
    config_path = Path(args.config) if args.config else root / "config" / "default.yaml"
    return root, load_config(config_path, root=root)


def _database_path(root: Path, cfg: AppConfig, requested: str | None) -> Path:
    if requested:
        return Path(requested)
    database_path = Path(cfg.database.path)
    if database_path.is_absolute():
        return database_path
    return root / database_path


def _scan(args: argparse.Namespace) -> int:
    root, cfg = _load(args)
    calendar = calendar_from_config(cfg)
    zone = ZoneInfo(cfg.signal.timezone)
    day = date.fromisoformat(args.date) if args.date else datetime.now(zone).date()
    if not calendar.is_trading_day(day):
        print(f"error: {day.isoformat()} is not a trading session", file=sys.stderr)
        return 1
    store = Store(_database_path(root, cfg, args.database), root / "migrations")
    market = build_market_provider(cfg, root, calendar)
    premarket = build_premarket_provider(cfg, root)
    news = build_news_provider(cfg, root)
    universe = build_universe_provider(cfg, root)
    splits_for, split_note, split_source = _split_source(cfg)
    rules_path = root / cfg.event.rules
    file_hash = event_rules_content_hash(rules_path)
    file_rules = load_event_rules(rules_path)
    experiment_id = ""
    try:
        if args.run_mode == "shadow":
            if not args.experiment:
                print("error: shadow scan requires --experiment", file=sys.stderr)
                return 1
            experiment = store.experiment(args.experiment)
            if experiment is None:
                print(f"error: unknown experiment {args.experiment}", file=sys.stderr)
                return 1
            signal_at = calendar.signal_time(day, cfg.signal.time, zone)
            if not signal_is_in_forward_window(experiment["shadow_starts_at"], signal_at):
                print(
                    "error: signal is before this experiment's forward window",
                    file=sys.stderr,
                )
                return 1
            version, rules, rules_hash = resolve_shadow(
                store, cfg, file_rules, args.experiment, file_hash=file_hash
            )
            experiment_id = args.experiment
        else:
            version, rules, rules_hash = resolve_strategy(
                store, cfg, file_rules, requested=args.strategy_version, file_hash=file_hash
            )
        sector_map = load_sector_map(root / cfg.relative_strength.sector_map)
        result = run_session(
            cfg,
            calendar=calendar,
            market=market,
            premarket=premarket,
            news=news,
            universe=universe,
            store=store,
            session=day,
            rules=rules,
            sector_map=sector_map,
            splits_for=splits_for,
            notes=split_note,
            output_dir=None,
            run_mode=args.run_mode,
            event_rules_hash=rules_hash,
            strategy_version=version,
            experiment_id=experiment_id,
            code_revision=git_revision(root),
        )
        loaded = store.get_snapshot(result.run_id)
        if loaded is None:
            print(f"error: snapshot row missing for {result.run_id}", file=sys.stderr)
            return 1
        document = json.loads(loaded[0])
        print(format_scan(document))
        saved = store.get_scan_inputs(result.run_id)
        saved_run = store.get_run(result.run_id)
        if saved is not None and saved_run is not None:
            print()
            print(
                format_coverage(
                    saved_run,
                    document,
                    capture_from_json(saved),
                    long_threshold=cfg.ranking.long_threshold,
                )
            )
        return 0
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except DataUnavailableError as exc:
        log_failure(
            symbol=exc.symbol,
            provider=exc.provider,
            operation=exc.operation,
            timestamp=exc.timestamp,
            reason=exc.reason,
        )
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()
        for provider in (market, premarket, news, split_source):
            close = getattr(provider, "close", None)
            if close is not None:
                close()


def _evaluate(args: argparse.Namespace) -> int:
    root, cfg = _load(args)
    zone = ZoneInfo(cfg.signal.timezone)
    if args.as_of:
        as_of = datetime.combine(date.fromisoformat(args.as_of), datetime.max.time(), tzinfo=zone)
    else:
        as_of = datetime.now(zone)
    calendar = calendar_from_config(cfg)
    store = Store(_database_path(root, cfg, args.database), root / "migrations")
    market = build_market_provider(cfg, root, calendar)
    splits_for, _split_note, split_source = _split_source(cfg)
    file_rules = load_event_rules(root / cfg.event.rules)
    file_hash = event_rules_content_hash(root / cfg.event.rules)
    try:
        if args.experiment:
            experiment = store.experiment(args.experiment)
            if experiment is None:
                print(f"error: unknown experiment {args.experiment}", file=sys.stderr)
                return 1
            version = str(experiment["baseline_version"])
            mode = str(experiment["run_mode"])
            chosen = runs_by_ids(store, tuple(json.loads(experiment["baseline_run_ids"])))
        else:
            try:
                version, _rules, _rules_hash = resolve_strategy(
                    store, cfg, file_rules, requested=args.strategy_version, file_hash=file_hash
                )
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            mode = args.run_mode
            chosen = select_runs(store, strategy_version=version, run_mode=mode)
        payloads: list[tuple[dict[str, object], list[object]]] = []
        for run in chosen:
            if run.strategy_version != version or run.run_mode != mode:
                print(
                    f"error: pinned run {run.run_id} is {run.strategy_version} {run.run_mode}",
                    file=sys.stderr,
                )
                return 1
            loaded = store.get_snapshot(run.run_id)
            if loaded is None:
                print(f"error: snapshot row missing for {run.run_id}", file=sys.stderr)
                return 1
            document = json.loads(loaded[0])
            fill_forward_returns(
                store,
                run,
                document,
                market,
                calendar=calendar,
                as_of=as_of,
                spy_symbol=cfg.benchmarks.market,
                splits_for=splits_for,
            )
            payloads.append((document, list(store.forward_for(run.run_id, as_of=as_of))))
        print(
            render_evaluation(
                payloads,
                long_threshold=cfg.ranking.long_threshold,
                horizons=cfg.evaluation.horizons,
                buckets=cfg.evaluation.buckets,
                min_bucket_count=cfg.evaluation.min_bucket_count,
                as_of=as_of,
                top_n=cfg.ranking.top_n,
                round_trip_cost=cfg.evaluation.round_trip_cost,
                primary_horizon=cfg.evaluation.primary_horizon,
                block_sessions=cfg.evaluation.block_sessions,
                calendar=calendar,
                strategy_version=version,
                run_mode=mode,
                run_ids=tuple(run.run_id for run in chosen),
            )
        )
        return 0
    finally:
        store.close()
        for provider in (market, split_source):
            close = getattr(provider, "close", None)
            if close is not None:
                close()


def _replay(args: argparse.Namespace) -> int:
    try:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
    except ValueError:
        print("error: --from and --to must be YYYY-MM-DD", file=sys.stderr)
        return 2
    root, cfg = _load(args)
    calendar = calendar_from_config(cfg)
    store = Store(_database_path(root, cfg, args.database), root / "migrations")
    market = build_market_provider(cfg, root, calendar)
    premarket = build_premarket_provider(cfg, root)
    news = build_news_provider(cfg, root)
    universe = build_universe_provider(cfg, root)
    splits_for, split_note, split_source = _split_source(cfg)
    try:
        results = replay(
            cfg,
            root=root,
            calendar=calendar,
            market=market,
            premarket=premarket,
            news=news,
            universe=universe,
            store=store,
            start=start,
            end=end,
            splits_for=splits_for,
            notes=split_note,
        )
    finally:
        store.close()
        for provider in (market, premarket, news, split_source):
            close = getattr(provider, "close", None)
            if close is not None:
                close()
    lines = ["ShortAlpha replay", f"sessions: {len(results)}"]
    lines.extend(
        f"{item.signal_date.isoformat()} hash={item.snapshot_hash} "
        f"no_trade={str(item.no_trade).lower()} candidates={item.candidate_size}"
        for item in results
    )
    print("\n".join(lines))
    return 0


def _coverage(args: argparse.Namespace) -> int:
    root, cfg = _load(args)
    store = Store(_database_path(root, cfg, args.database), root / "migrations")
    try:
        runs = store.runs_on(date.fromisoformat(args.date))
        if args.run_id:
            runs = [run for run in runs if run.run_id == args.run_id]
        if not runs:
            print(f"error: no snapshot on {args.date}", file=sys.stderr)
            return 1
        run = runs[-1]
        loaded = store.get_snapshot(run.run_id)
        saved = store.get_scan_inputs(run.run_id)
        if loaded is None or saved is None:
            print(f"error: coverage inputs missing for {run.run_id}", file=sys.stderr)
            return 1
        print(
            format_coverage(
                run,
                json.loads(loaded[0]),
                capture_from_json(saved),
                long_threshold=cfg.ranking.long_threshold,
            )
        )
        return 0
    finally:
        store.close()


def _replay_inputs(args: argparse.Namespace) -> int:
    root, cfg = _load(args)
    calendar = calendar_from_config(cfg)
    store = Store(_database_path(root, cfg, args.database), root / "migrations")
    try:
        source = store.get_run(args.run_id)
        if source is None:
            print(f"error: unknown run {args.run_id}", file=sys.stderr)
            return 1
        result, matched = replay_saved(
            cfg,
            root=root,
            calendar=calendar,
            store=store,
            run_id=args.run_id,
            session=source.signal_date,
            notes=f"replay-inputs {args.run_id}",
        )
        print(f"source_run: {args.run_id}")
        print(f"replay_run: {result.run_id}")
        print(f"snapshot_match: {str(matched).lower()}")
        return 0 if matched else 1
    finally:
        store.close()


def _outcomes(args: argparse.Namespace) -> int:
    root, cfg = _load(args)
    zone = ZoneInfo(cfg.signal.timezone)
    if args.as_of:
        as_of = datetime.combine(date.fromisoformat(args.as_of), datetime.max.time(), tzinfo=zone)
    else:
        as_of = datetime.now(zone)
    calendar = calendar_from_config(cfg)
    store = Store(_database_path(root, cfg, args.database), root / "migrations")
    market = build_market_provider(cfg, root, calendar)
    splits_for, _split_note, split_source = _split_source(cfg)
    file_rules = load_event_rules(root / cfg.event.rules)
    file_hash = event_rules_content_hash(root / cfg.event.rules)
    try:
        version, _rules, _rules_hash = resolve_strategy(
            store, cfg, file_rules, requested=None, file_hash=file_hash
        )
        days: list[tuple[date, dict[str, object], list[dict[str, object]]]] = []
        for run in select_runs(store, strategy_version=version, run_mode=args.run_mode):
            loaded = store.get_snapshot(run.run_id)
            if loaded is None:
                print(f"error: snapshot row missing for {run.run_id}", file=sys.stderr)
                return 1
            document = json.loads(loaded[0])
            fill_forward_returns(
                store,
                run,
                document,
                market,
                calendar=calendar,
                as_of=as_of,
                spy_symbol=cfg.benchmarks.market,
                splits_for=splits_for,
            )
            days.append((run.signal_date, document, _forward_rows(store, run.run_id, as_of)))
        print(
            format_outcomes(
                days,
                cost=cfg.evaluation.round_trip_cost,
                long_threshold=cfg.ranking.long_threshold,
                watch_threshold=cfg.ranking.watch_threshold,
            )
        )
        return 0
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()
        for provider in (market, split_source):
            close = getattr(provider, "close", None)
            if close is not None:
                close()


def _forward_rows(store: Store, run_id: str, as_of: datetime) -> list[dict[str, object]]:
    return [
        {
            "horizon": int(row["horizon"]),
            "mae": row["mae"],
            "spy_return": row["spy_return"],
            "stock_return": row["stock_return"],
            "symbol": row["symbol"],
        }
        for row in store.forward_for(run_id, as_of=as_of)
    ]


def _no_splits(symbol: str, start: date, end: date, as_of: datetime) -> tuple[Split, ...]:
    return ()


def _split_source(
    cfg: AppConfig,
) -> tuple[object, str, AlpacaCorporateActionsProvider | None]:
    if cfg.providers.market != "alpaca":
        return _no_splits, "corporate_actions=not_loaded", None
    provider = AlpacaCorporateActionsProvider.from_env(base_url=cfg.alpaca.data_base_url)
    return (
        provider.splits,
        "corporate_actions=alpaca;announcement_time=unavailable",
        provider,
    )


def _evolve(args: argparse.Namespace) -> int:
    root, cfg = _load(args)
    zone = ZoneInfo(cfg.signal.timezone)
    store = Store(_database_path(root, cfg, args.database), root / "migrations")
    rules_path = root / cfg.event.rules
    rules_hash = event_rules_content_hash(rules_path)
    try:
        if args.evolve_command == "rollback":
            rollback_official(
                store,
                args.to_version,
                recorded_at=datetime.now(zone),
                config_digest=config_hash(cfg, rules_hash),
                event_rules_hash=rules_hash,
            )
            print(f"OFFICIAL {store.official_version(cfg.strategy.version)}")
            return 0
        as_of = datetime.combine(date.fromisoformat(args.as_of), datetime.max.time(), tzinfo=zone)
        if args.evolve_command == "decide":
            print(
                decide_experiment(
                    store,
                    cfg,
                    args.experiment,
                    as_of=as_of,
                    calendar=calendar_from_config(cfg),
                )
            )
            return 0
        file_rules = load_event_rules(rules_path)
        try:
            version, rules, active_hash = resolve_strategy(
                store, cfg, file_rules, requested=None, file_hash=rules_hash
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        calendar = calendar_from_config(cfg)
        market = build_market_provider(cfg, root, calendar)
        splits_for, _note, split_source = _split_source(cfg)
        try:
            runs = select_runs(store, strategy_version=version, run_mode=args.run_mode)
            documents: dict[str, dict[str, object]] = {}
            forwards: dict[str, list[object]] = {}
            for run in runs:
                loaded = store.get_snapshot(run.run_id)
                if loaded is None:
                    print(f"error: snapshot row missing for {run.run_id}", file=sys.stderr)
                    return 1
                document = json.loads(loaded[0])
                fill_forward_returns(
                    store,
                    run,
                    document,
                    market,
                    calendar=calendar,
                    as_of=as_of,
                    spy_symbol=cfg.benchmarks.market,
                    splits_for=splits_for,
                )
                documents[run.run_id] = document
                forwards[run.run_id] = list(store.forward_for(run.run_id, as_of=as_of))
            print(
                open_experiment(
                    store,
                    cfg,
                    rules,
                    runs,
                    documents,
                    forwards,
                    as_of=as_of,
                    dev_end=date.fromisoformat(args.dev_end),
                    validation_start=date.fromisoformat(args.validation_start),
                    validation_end=date.fromisoformat(args.validation_end),
                    run_mode=args.run_mode,
                    config_digest=config_hash(cfg, active_hash),
                    event_rules_hash=active_hash,
                    calendar=calendar,
                )
            )
            return 0
        finally:
            for provider in (market, split_source):
                close = getattr(provider, "close", None)
                if close is not None:
                    close()
    finally:
        store.close()


def _explain(args: argparse.Namespace) -> int:
    root, cfg = _load(args)
    symbol = args.symbol.upper()
    day = date.fromisoformat(args.date)
    store = Store(_database_path(root, cfg, args.database), root / "migrations")
    try:
        runs = store.runs_on(day)
        if args.run_id:
            runs = [run for run in runs if run.run_id == args.run_id]
        if not runs:
            print(f"error: no snapshot for {symbol} on {day.isoformat()}", file=sys.stderr)
            return 1
        run = runs[-1]
        loaded = store.get_snapshot(run.run_id)
        if loaded is None:
            print(f"error: snapshot row missing for {run.run_id}", file=sys.stderr)
            return 1
        document = json.loads(loaded[0])
        print(format_explanation(document, symbol, run_id=run.run_id))
        return 0
    finally:
        store.close()


def _check(args: argparse.Namespace) -> int:
    root, cfg = _load(args)
    database_path = _database_path(root, cfg, args.database)
    store = Store(database_path, root / "migrations")
    try:
        calendar = calendar_from_config(cfg)
        if args.date:
            day = date.fromisoformat(args.date)
        else:
            day = datetime.now(ZoneInfo(cfg.signal.timezone)).date()
        trading = calendar.is_trading_day(day)
        early = calendar.is_early_close(day) if trading else False
        lines = [
            f"shortalpha {__version__}",
            "config: OK",
            "weights: 100",
            f"database: {database_path}",
            f"date: {day.isoformat()}",
            f"trading_day: {str(trading).lower()}",
            f"early_close: {str(early).lower()}",
        ]
        if trading:
            signal = calendar.signal_time(day, cfg.signal.time, ZoneInfo(cfg.signal.timezone))
            lines.append(f"signal_time: {signal.isoformat()}")
        else:
            lines.append("signal_time: n/a")
        print("\n".join(lines))
        if args.date and not trading:
            print(f"error: {day.isoformat()} is not a trading session", file=sys.stderr)
            return 1
        return 0
    finally:
        store.close()


def _universe(args: argparse.Namespace) -> int:
    root, cfg = _load(args)
    calendar = calendar_from_config(cfg)
    loaded = build_universe_provider(cfg, root).load(datetime.now(ZoneInfo(cfg.signal.timezone)))
    lines = [
        "ShortAlpha universe",
        f"list_as_of: {loaded.list_as_of.isoformat()}",
        f"point_in_time_membership: {str(loaded.point_in_time_membership).lower()}",
        "source_as_of: "
        + " ".join(f"{name}={day.isoformat()}" for name, day in loaded.source_as_of),
        f"symbols: {len(loaded.members)}",
    ]
    if not args.date:
        lines.extend(f"{member.symbol}  {','.join(member.sources)}" for member in loaded.members)
        print("\n".join(lines))
        return 0

    day = date.fromisoformat(args.date)
    if not calendar.is_trading_day(day):
        print(f"error: {day.isoformat()} is not a trading session", file=sys.stderr)
        return 1
    as_of = calendar.signal_time(day, cfg.signal.time, ZoneInfo(cfg.signal.timezone))
    market = build_market_provider(cfg, root, calendar)
    start = calendar.shift(day, -20)
    counts = {status: 0 for status in LiquidityStatus}
    detail: list[str] = []
    try:
        for member in loaded.members:
            try:
                bars = market.daily_bars(member.symbol, start, day, as_of)
            except DataUnavailableError as exc:
                counts[LiquidityStatus.MISSING] += 1
                detail.append(f"{member.symbol}  missing  {exc.reason}")
                continue
            result = evaluate_liquidity(
                member.symbol,
                bars,
                session=day,
                as_of=as_of,
                calendar=calendar,
                min_price=cfg.universe.filters.min_price,
                min_avg_dollar_volume=cfg.universe.filters.min_avg_dollar_volume_20d,
            )
            counts[result.status] += 1
            if result.status is LiquidityStatus.MISSING:
                log_failure(
                    symbol=member.symbol,
                    provider=getattr(market, "name", "market"),
                    operation="liquidity",
                    timestamp=as_of,
                    reason=result.reason,
                )
                detail.append(f"{member.symbol}  missing  {result.reason}")
            elif result.status is LiquidityStatus.EXCLUDED:
                detail.append(f"{member.symbol}  excluded  {result.reason}")
            else:
                detail.append(
                    f"{member.symbol}  eligible  price={format_number(result.price or 0)} "
                    f"avg_dollar_volume={format_number(result.avg_dollar_volume or 0)}"
                )
    finally:
        closer = getattr(market, "close", None)
        if closer is not None:
            closer()
    lines.extend(
        [
            f"date: {day.isoformat()}",
            f"signal_time: {as_of.isoformat()}",
            f"eligible: {counts[LiquidityStatus.ELIGIBLE]}",
            f"excluded: {counts[LiquidityStatus.EXCLUDED]}",
            f"missing: {counts[LiquidityStatus.MISSING]}",
            *detail,
        ]
    )
    print("\n".join(lines))
    return 0
