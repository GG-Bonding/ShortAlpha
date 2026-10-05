"""One signal time, then the next trading session. No look-ahead."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from shortalpha import __version__
from shortalpha.calendar import NYSECalendar
from shortalpha.config import AppConfig, config_hash
from shortalpha.domain import Split
from shortalpha.errors import DataUnavailableError
from shortalpha.event_rules import EventRules, event_rules_content_hash, load_event_rules
from shortalpha.factors.events import compute_event
from shortalpha.factors.momentum import compute_momentum
from shortalpha.factors.price_action import compute_price_action
from shortalpha.factors.relative_strength import (
    compute_relative_strength,
    load_sector_map,
    sector_etf_for,
)
from shortalpha.factors.volume import compute_volume
from shortalpha.logging_utils import log_failure
from shortalpha.providers.base import (
    MarketDataProvider,
    NewsProvider,
    PreMarketDataProvider,
    UniverseProvider,
)
from shortalpha.scoring.rank import SymbolFactors, rank_symbols
from shortalpha.scoring.regime import classify_regime
from shortalpha.signal.snapshot import save_signal
from shortalpha.storage import Store
from shortalpha.universe import LiquidityStatus, evaluate_liquidity

SplitsFn = Callable[[str, date, date, datetime], tuple[Split, ...]]


@dataclass(frozen=True)
class SessionResult:
    signal_date: date
    run_id: str
    snapshot_hash: str
    no_trade: bool
    candidate_size: int
    provider_errors: int
    missing_data_count: int


def replay(
    cfg: AppConfig,
    *,
    root: Path,
    calendar: NYSECalendar,
    market: MarketDataProvider,
    premarket: PreMarketDataProvider,
    news: NewsProvider,
    universe: UniverseProvider,
    store: Store,
    start: date,
    end: date,
    splits_for: SplitsFn,
    notes: str,
    output_dir: Path | None = None,
) -> tuple[SessionResult, ...]:
    if start > end:
        raise ValueError("--from must be on or before --to")
    rules_path = _resolve(root, cfg.event.rules)
    rules = load_event_rules(rules_path)
    rules_hash = event_rules_content_hash(rules_path)
    sector_map = load_sector_map(_resolve(root, cfg.relative_strength.sector_map))
    results: list[SessionResult] = []
    day = start
    while day <= end:
        if calendar.is_trading_day(day):
            results.append(
                run_session(
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
                    notes=notes,
                    output_dir=output_dir,
                    run_mode="replay",
                    event_rules_hash=rules_hash,
                )
            )
        day = date.fromordinal(day.toordinal() + 1)
    return tuple(results)


def run_session(
    cfg: AppConfig,
    *,
    calendar: NYSECalendar,
    market: MarketDataProvider,
    premarket: PreMarketDataProvider,
    news: NewsProvider,
    universe: UniverseProvider,
    store: Store,
    session: date,
    rules: EventRules,
    sector_map: dict[str, str],
    splits_for: SplitsFn,
    notes: str,
    output_dir: Path | None,
    run_mode: str,
    event_rules_hash: str,
    strategy_version: str | None = None,
) -> SessionResult:
    started = time.perf_counter()
    as_of = calendar.signal_time(session, cfg.signal.time, _zone(cfg.signal.timezone))
    history_start = calendar.shift(session, -30)
    loaded = universe.load(as_of)
    spy = market.daily_bars(cfg.benchmarks.market, history_start, session, as_of)
    qqq = market.daily_bars(cfg.benchmarks.growth, history_start, session, as_of)
    spy_return = _trailing_return(cfg.benchmarks.market, spy, session, as_of, calendar)
    qqq_return = _trailing_return(cfg.benchmarks.growth, qqq, session, as_of, calendar)
    vix, vix_note = _vix(cfg, market, history_start, session, as_of)
    regime, vix_available = classify_regime(spy_return, qqq_return, vix, cfg.regime)
    rows: list[SymbolFactors] = []
    provider_errors = 0
    missing_data = 0
    eligible = 0
    for member in loaded.members:
        try:
            bars = market.daily_bars(member.symbol, history_start, session, as_of)
            window = premarket.window(member.symbol, session, as_of)
            liquidity = evaluate_liquidity(
                member.symbol,
                bars,
                session=session,
                as_of=as_of,
                calendar=calendar,
                min_price=cfg.universe.filters.min_price,
                min_avg_dollar_volume=cfg.universe.filters.min_avg_dollar_volume_20d,
                premarket_price=window.last_price,
            )
        except DataUnavailableError as exc:
            _count_failure(exc)
            provider_errors += 1
            continue
        if liquidity.status is LiquidityStatus.MISSING:
            missing_data += 1
            log_failure(
                symbol=member.symbol,
                provider="market",
                operation="liquidity",
                timestamp=as_of,
                reason=liquidity.reason,
            )
            continue
        if liquidity.status is not LiquidityStatus.ELIGIBLE:
            continue
        eligible += 1
        try:
            sector = sector_etf_for(member.symbol, sector_map, as_of)
            sector_bars = market.daily_bars(sector, history_start, session, as_of)
            items = news.news(
                member.symbol,
                as_of - _hours(cfg.event.max_age_hours),
                as_of,
                as_of,
            )
            stock_splits = splits_for(member.symbol, history_start, session, as_of)
            factors = (
                compute_momentum(
                    member.symbol,
                    bars,
                    session=session,
                    as_of=as_of,
                    calendar=calendar,
                    momentum=cfg.momentum,
                    weight=cfg.weights.momentum,
                    splits=stock_splits,
                ),
                compute_volume(
                    member.symbol,
                    bars,
                    premarket,
                    session=session,
                    as_of=as_of,
                    calendar=calendar,
                    volume=cfg.volume,
                    weight=cfg.weights.volume,
                ),
                compute_event(
                    member.symbol,
                    items,
                    as_of=as_of,
                    event=cfg.event,
                    rules=rules,
                    weight=cfg.weights.event,
                    rules_sha256=event_rules_hash,
                    bars=bars,
                    session=session,
                    splits=stock_splits,
                ),
                compute_relative_strength(
                    member.symbol,
                    bars,
                    spy,
                    sector_bars,
                    session=session,
                    as_of=as_of,
                    calendar=calendar,
                    relative=cfg.relative_strength,
                    weight=cfg.weights.relative_strength,
                    market_symbol=cfg.benchmarks.market,
                    sector_etf=sector,
                    stock_splits=stock_splits,
                ),
                compute_price_action(
                    member.symbol,
                    bars,
                    window,
                    session=session,
                    as_of=as_of,
                    calendar=calendar,
                    price_action=cfg.price_action,
                    weight=cfg.weights.price_action,
                    splits=stock_splits,
                ),
            )
        except DataUnavailableError as exc:
            _count_failure(exc)
            provider_errors += 1
            continue
        rows.append(SymbolFactors(member.symbol, factors))
    book = rank_symbols(
        rows,
        as_of=as_of,
        ranking=cfg.ranking,
        hard_filters=cfg.hard_filters,
        regime=regime,
        regime_cfg=cfg.regime,
        vix_available=vix_available,
    )
    run_notes = notes if not vix_note else f"{notes};{vix_note}".strip(";")
    run_id = save_signal(
        store,
        book,
        signal_date=session,
        signal_time=as_of,
        timezone=cfg.signal.timezone,
        weights=cfg.weights,
        config_hash=config_hash(cfg, event_rules_hash),
        code_version=__version__,
        created_at=datetime.now(UTC),
        universe_size=len(loaded.members),
        eligible_size=eligible,
        provider_errors=provider_errors,
        missing_data_count=missing_data,
        universe_list_as_of=loaded.list_as_of,
        point_in_time_membership=loaded.point_in_time_membership,
        notes=run_notes,
        strategy_version=strategy_version or cfg.strategy.version,
        run_mode=run_mode,
        event_rules_hash=event_rules_hash,
        output_dir=output_dir,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    loaded_snapshot = store.get_snapshot(run_id)
    if loaded_snapshot is None:
        raise RuntimeError(f"snapshot missing after save: {run_id}")
    return SessionResult(
        signal_date=session,
        run_id=run_id,
        snapshot_hash=loaded_snapshot[1],
        no_trade=book.no_trade,
        candidate_size=len(book.published),
        provider_errors=provider_errors,
        missing_data_count=missing_data,
    )


def _trailing_return(
    symbol: str,
    bars: list,
    session: date,
    as_of: datetime,
    calendar: NYSECalendar,
) -> float:
    from shortalpha.factors.history import require_completed_bars

    completed = require_completed_bars(
        symbol,
        bars,
        session=session,
        as_of=as_of,
        calendar=calendar,
        count=6,
        operation="regime",
    )
    return completed[-1].close / completed[-6].close - 1


def _vix(cfg: AppConfig, market: MarketDataProvider, start: date, session: date, as_of: datetime):
    try:
        bars = market.daily_bars(cfg.benchmarks.volatility, start, session, as_of)
    except DataUnavailableError as exc:
        log_failure(
            symbol=exc.symbol,
            provider=exc.provider,
            operation=exc.operation,
            timestamp=exc.timestamp,
            reason=exc.reason,
        )
        return None, "vix_available=false"
    visible = [bar for bar in bars if bar.session_date < session]
    if not visible:
        log_failure(
            symbol=cfg.benchmarks.volatility,
            provider=market.name,
            operation="regime",
            timestamp=as_of,
            reason="no VIX bar at or before signal_time",
        )
        return None, "vix_available=false"
    return visible[-1].close, ""


def _count_failure(exc: DataUnavailableError) -> None:
    log_failure(
        symbol=exc.symbol,
        provider=exc.provider,
        operation=exc.operation,
        timestamp=exc.timestamp,
        reason=exc.reason,
    )


def _hours(hours: float):
    from datetime import timedelta

    return timedelta(hours=hours)


def _zone(name: str):
    from zoneinfo import ZoneInfo

    return ZoneInfo(name)


def _resolve(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute():
        return path
    return root / path
