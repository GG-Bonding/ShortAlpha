"""Split adjustment that ignores corporate actions after the signal session."""

from datetime import date

from shortalpha.domain import Split


def adjustment_factor(
    session_date: date,
    signal_session: date,
    splits: tuple[Split, ...],
    symbol: str,
) -> float:
    factor = 1.0
    for split in splits:
        if split.symbol != symbol or split.ex_date > signal_session:
            continue
        if session_date < split.ex_date:
            factor *= split.old_rate / split.new_rate
    return factor


def adjusted_close(
    raw_close: float,
    session_date: date,
    signal_session: date,
    splits: tuple[Split, ...],
    symbol: str,
) -> float:
    return raw_close * adjustment_factor(session_date, signal_session, splits, symbol)
