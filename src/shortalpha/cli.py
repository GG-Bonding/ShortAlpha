"""Command line. Phase 1 exposes version and check."""

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from shortalpha import __version__
from shortalpha.calendar import calendar_from_config
from shortalpha.config import load_config
from shortalpha.errors import ConfigError, ShortAlphaError
from shortalpha.paths import project_root
from shortalpha.storage import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shortalpha")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version")
    check = sub.add_parser("check")
    check.add_argument("--config", default=None)
    check.add_argument("--date", default=None)
    check.add_argument("--database", default=None)
    try:
        args = parser.parse_args(argv)
        if args.command == "version":
            print(f"shortalpha {__version__}")
            return 0
        return _check(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except ShortAlphaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _check(args: argparse.Namespace) -> int:
    root = project_root()
    config_path = Path(args.config) if args.config else root / "config" / "default.yaml"
    cfg = load_config(config_path, root=root)
    if args.database:
        database_path = Path(args.database)
    else:
        database_path = Path(cfg.database.path)
        if not database_path.is_absolute():
            database_path = root / database_path
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
