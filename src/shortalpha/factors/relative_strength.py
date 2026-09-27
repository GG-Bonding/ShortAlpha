"""Stock versus SPY and the configured sector ETF, on the same completed window."""

from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path

import yaml

from shortalpha.calendar import NYSECalendar
from shortalpha.config import RelativeStrengthConfig
from shortalpha.domain import DailyBar, FactorResult, Split
from shortalpha.errors import DataUnavailableError
from shortalpha.factors.adjust import adjusted_close
from shortalpha.factors.history import require_completed_bars
from shortalpha.factors.scale import clamp, scale_to_weight


def load_sector_map(path: Path) -> dict[str, str]:
    loaded = yaml.safe_load(path.read_text())
    symbols = loaded["symbols"]
    return {str(symbol): str(etf) for symbol, etf in symbols.items()}


def sector_etf_for(symbol: str, mapping: Mapping[str, str], as_of: datetime) -> str:
    try:
        return mapping[symbol]
    except KeyError as exc:
        raise DataUnavailableError(
            symbol=symbol,
            provider="sector_map",
            operation="relative_strength",
            timestamp=as_of,
            reason=f"no sector ETF mapped for {symbol}",
        ) from exc


def compute_relative_strength(
    symbol: str,
    stock_bars: list[DailyBar],
    market_bars: list[DailyBar],
    sector_bars: list[DailyBar],
    *,
    session: date,
    as_of: datetime,
    calendar: NYSECalendar,
    relative: RelativeStrengthConfig,
    weight: float,
    market_symbol: str,
    sector_etf: str,
    stock_splits: tuple[Split, ...] = (),
    market_splits: tuple[Split, ...] = (),
    sector_splits: tuple[Split, ...] = (),
) -> FactorResult:
    stock = _return(
        symbol,
        stock_bars,
        session,
        as_of,
        calendar,
        relative.lookback_sessions,
        stock_splits,
    )
    market = _return(
        market_symbol,
        market_bars,
        session,
        as_of,
        calendar,
        relative.lookback_sessions,
        market_splits,
    )
    sector = _return(
        sector_etf,
        sector_bars,
        session,
        as_of,
        calendar,
        relative.lookback_sessions,
        sector_splits,
    )
    rs_market = stock - market
    rs_sector = stock - sector
    raw = relative.market_weight * rs_market + relative.sector_weight * rs_sector
    score = clamp(
        scale_to_weight(raw, relative.raw_low, relative.raw_high, weight),
        0.0,
        weight,
    )
    reasons = []
    risks = []
    if rs_market > 0:
        reasons.append(f"Outperforming {market_symbol}")
    elif rs_market < 0:
        risks.append(f"Underperforming {market_symbol}")
    if rs_sector > 0:
        reasons.append(f"Outperforming {sector_etf}")
    elif rs_sector < 0:
        risks.append(f"Underperforming {sector_etf}")
    return FactorResult(
        name="relative_strength",
        raw_value=raw,
        normalized_value=score / weight,
        score=score,
        available=True,
        reasons=tuple(reasons),
        risks=tuple(risks),
        details=(
            ("rs_market", f"{rs_market:.10f}"),
            ("rs_sector", f"{rs_sector:.10f}"),
            ("sector_etf", sector_etf),
        ),
    )


def _return(
    symbol: str,
    bars: list[DailyBar],
    session: date,
    as_of: datetime,
    calendar: NYSECalendar,
    lookback: int,
    splits: tuple[Split, ...],
) -> float:
    completed = require_completed_bars(
        symbol,
        bars,
        session=session,
        as_of=as_of,
        calendar=calendar,
        count=lookback + 1,
        operation="relative_strength",
    )
    first = adjusted_close(completed[0].close, completed[0].session_date, session, splits, symbol)
    last = adjusted_close(completed[-1].close, completed[-1].session_date, session, splits, symbol)
    return last / first - 1
