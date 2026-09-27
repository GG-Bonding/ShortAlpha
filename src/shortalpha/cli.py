"""Command line."""

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from shortalpha import __version__
from shortalpha.calendar import calendar_from_config
from shortalpha.config import AppConfig, load_config
from shortalpha.errors import ConfigError, DataUnavailableError, ShortAlphaError
from shortalpha.logging_utils import log_failure
from shortalpha.paths import project_root
from shortalpha.providers.factory import build_market_provider, build_universe_provider
from shortalpha.signal.explain import format_explanation
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
    explain = sub.add_parser("explain")
    _add_config_arg(explain)
    explain.add_argument("symbol")
    explain.add_argument("--date", required=True)
    explain.add_argument("--run-id", default=None)
    explain.add_argument("--database", default=None)
    try:
        args = parser.parse_args(argv)
        if args.command == "version":
            print(f"shortalpha {__version__}")
            return 0
        if args.command == "universe":
            return _universe(args)
        if args.command == "explain":
            return _explain(args)
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
