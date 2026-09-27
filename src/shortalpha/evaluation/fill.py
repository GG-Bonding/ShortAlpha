"""Fill forward returns after the exit bar exists. Missing horizons are not inserted."""

from collections.abc import Callable
from datetime import date, datetime

from shortalpha import __version__
from shortalpha.calendar import NYSECalendar
from shortalpha.domain import SignalRun, Split
from shortalpha.errors import DataUnavailableError
from shortalpha.evaluation.forward import compute_forward_returns
from shortalpha.logging_utils import log_failure
from shortalpha.providers.base import MarketDataProvider
from shortalpha.storage import Store

SplitsFn = Callable[[str, date, date, datetime], tuple[Split, ...]]


def fill_forward_returns(
    store: Store,
    run: SignalRun,
    snapshot: dict[str, object],
    market: MarketDataProvider,
    *,
    calendar: NYSECalendar,
    as_of: datetime,
    spy_symbol: str,
    splits_for: SplitsFn,
) -> int:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    ranked = snapshot.get("ranked")
    if not isinstance(ranked, list):
        return 0
    existing = {(row["symbol"], int(row["horizon"])) for row in store.forward_for(run.run_id)}
    end = calendar.shift(run.signal_date, 5)
    try:
        spy = market.daily_bars(spy_symbol, run.signal_date, end, as_of)
    except DataUnavailableError as exc:
        log_failure(
            symbol=exc.symbol,
            provider=exc.provider,
            operation=exc.operation,
            timestamp=exc.timestamp,
            reason=exc.reason,
        )
        return 0
    inserted = 0
    for item in ranked:
        if not isinstance(item, dict):
            continue
        symbol = str(item["symbol"])
        try:
            stock = market.daily_bars(symbol, run.signal_date, end, as_of)
            stock_splits = splits_for(symbol, run.signal_date, end, as_of)
            spy_splits = splits_for(spy_symbol, run.signal_date, end, as_of)
            completed, _missing = compute_forward_returns(
                symbol,
                stock,
                spy,
                session=run.signal_date,
                as_of=as_of,
                calendar=calendar,
                splits=stock_splits,
                spy_splits=spy_splits,
            )
        except DataUnavailableError as exc:
            log_failure(
                symbol=exc.symbol,
                provider=exc.provider,
                operation=exc.operation,
                timestamp=exc.timestamp,
                reason=exc.reason,
            )
            continue
        for result in completed:
            key = (result.symbol, result.horizon)
            if key in existing:
                continue
            store.insert_forward_return(
                run_id=run.run_id,
                symbol=result.symbol,
                horizon=result.horizon,
                entry_session=result.entry_session,
                entry_price=result.entry_price,
                exit_session=result.exit_session,
                exit_price=result.exit_price,
                stock_return=result.stock_return,
                spy_return=result.spy_return,
                excess_return=result.excess_return,
                mae=result.mae,
                mfe=result.mfe,
                code_version=__version__,
            )
            existing.add(key)
            inserted += 1
    return inserted


def latest_runs(store: Store) -> list[SignalRun]:
    chosen: dict[date, SignalRun] = {}
    for run in store.all_runs():
        chosen[run.signal_date] = run
    return [chosen[day] for day in sorted(chosen)]
