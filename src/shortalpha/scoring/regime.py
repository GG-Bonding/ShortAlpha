"""A small SPY / QQQ / VIX regime. Missing VIX is not treated as zero."""

from shortalpha.config import RegimeConfig
from shortalpha.domain import MarketRegime


def classify_regime(
    spy_5d: float,
    qqq_5d: float,
    vix: float | None,
    regime: RegimeConfig,
) -> tuple[MarketRegime, bool]:
    vix_available = vix is not None
    if spy_5d <= regime.extreme_spy_5d or (vix_available and vix >= regime.extreme_vix):
        return MarketRegime.EXTREME_RISK_OFF, vix_available
    if (
        spy_5d <= regime.risk_off_spy_5d
        or qqq_5d <= regime.risk_off_qqq_5d
        or (vix_available and vix >= regime.risk_off_vix)
    ):
        return MarketRegime.RISK_OFF, vix_available
    return MarketRegime.NORMAL, vix_available


def regime_blocks_trading(regime: MarketRegime, no_trade_on: str) -> bool:
    if no_trade_on == "EXTREME_RISK_OFF":
        return regime is MarketRegime.EXTREME_RISK_OFF
    return regime is not MarketRegime.NORMAL
