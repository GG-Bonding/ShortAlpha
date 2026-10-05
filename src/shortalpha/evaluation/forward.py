"""Forward returns on trading sessions. Missing bars stay missing."""

from dataclasses import dataclass
from datetime import date, datetime

from shortalpha.calendar import NYSECalendar
from shortalpha.domain import DailyBar, Split
from shortalpha.factors.adjust import adjustment_factor

_HORIZONS = (1, 2, 3, 5)


@dataclass(frozen=True)
class HorizonReturn:
    symbol: str
    horizon: int
    entry_session: date
    entry_price: float
    exit_session: date
    exit_price: float
    stock_return: float
    spy_return: float
    excess_return: float
    mae: float
    mfe: float
    label_available_at: datetime


def compute_forward_returns(
    symbol: str,
    stock_bars: list[DailyBar],
    spy_bars: list[DailyBar],
    *,
    session: date,
    as_of: datetime,
    calendar: NYSECalendar,
    horizons: tuple[int, ...] = _HORIZONS,
    splits: tuple[Split, ...] = (),
    spy_splits: tuple[Split, ...] = (),
) -> tuple[tuple[HorizonReturn, ...], tuple[tuple[int, str], ...]]:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if tuple(horizons) != _HORIZONS:
        raise ValueError("horizons must be 1, 2, 3, 5")
    if not calendar.is_trading_day(session):
        raise ValueError(f"{session.isoformat()} is not a trading session")
    stock = _index(symbol, stock_bars, as_of)
    spy = _index("SPY", spy_bars, as_of)
    completed: list[HorizonReturn] = []
    missing: list[tuple[int, str]] = []
    entry = stock.get(session)
    spy_entry = spy.get(session)
    for horizon in horizons:
        exit_session = calendar.shift(session, horizon)
        path = [calendar.shift(session, offset) for offset in range(horizon)]
        reason = _missing_reason(entry, spy_entry, stock, spy, path, exit_session)
        if reason is not None or entry is None or spy_entry is None:
            missing.append((horizon, reason or "entry bar unavailable"))
            continue
        exit_bar = stock[exit_session]
        spy_exit = spy[exit_session]
        entry_price = _adjusted(entry.open, session, exit_session, splits, symbol)
        exit_price = _adjusted(exit_bar.open, exit_session, exit_session, splits, symbol)
        spy_entry_price = _adjusted(spy_entry.open, session, exit_session, spy_splits, "SPY")
        spy_exit_price = _adjusted(spy_exit.open, exit_session, exit_session, spy_splits, "SPY")
        stock_return = exit_price / entry_price - 1
        spy_return = spy_exit_price / spy_entry_price - 1
        lows = [
            _adjusted(stock[day].low, day, exit_session, splits, symbol) / entry_price - 1
            for day in path
        ]
        highs = [
            _adjusted(stock[day].high, day, exit_session, splits, symbol) / entry_price - 1
            for day in path
        ]
        label_at = max(exit_bar.available_at, spy_exit.available_at)
        completed.append(
            HorizonReturn(
                symbol=symbol,
                horizon=horizon,
                entry_session=session,
                entry_price=entry_price,
                exit_session=exit_session,
                exit_price=exit_price,
                stock_return=stock_return,
                spy_return=spy_return,
                excess_return=stock_return - spy_return,
                mae=min(lows),
                mfe=max(highs),
                label_available_at=label_at,
            )
        )
    return tuple(completed), tuple(missing)


def _missing_reason(
    entry: DailyBar | None,
    spy_entry: DailyBar | None,
    stock: dict[date, DailyBar],
    spy: dict[date, DailyBar],
    path: list[date],
    exit_session: date,
) -> str | None:
    if entry is None:
        return "entry bar unavailable"
    if spy_entry is None:
        return "SPY entry bar unavailable"
    if exit_session not in stock:
        return "exit bar unavailable"
    if exit_session not in spy:
        return "SPY exit bar unavailable"
    if any(day not in stock for day in path):
        return "path bar unavailable"
    return None


def _index(symbol: str, bars: list[DailyBar], as_of: datetime) -> dict[date, DailyBar]:
    by_day: dict[date, DailyBar] = {}
    for bar in bars:
        if bar.symbol != symbol or bar.available_at > as_of:
            continue
        previous = by_day.get(bar.session_date)
        if previous is not None and (previous.open != bar.open or previous.close != bar.close):
            raise ValueError(
                f"conflicting daily bars for {symbol} on {bar.session_date.isoformat()}"
            )
        by_day[bar.session_date] = bar
    return by_day


def _adjusted(
    price: float,
    session_date: date,
    known_through: date,
    splits: tuple[Split, ...],
    symbol: str,
) -> float:
    return price * adjustment_factor(session_date, known_through, splits, symbol)
