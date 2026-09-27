"""Sum the five factor scores and rank without lowering the long threshold."""

from dataclasses import dataclass
from datetime import datetime

from shortalpha.config import HardFilterConfig, RankingConfig, RegimeConfig
from shortalpha.domain import EventRisk, FactorResult, MarketRegime, SignalLabel, validate_symbol
from shortalpha.errors import DataUnavailableError
from shortalpha.scoring.regime import regime_blocks_trading

_FACTORS = ("momentum", "volume", "event", "relative_strength", "price_action")


@dataclass(frozen=True)
class SymbolFactors:
    symbol: str
    factors: tuple[FactorResult, ...]
    excluded_reason: str | None = None

    def __post_init__(self) -> None:
        validate_symbol(self.symbol)


@dataclass(frozen=True)
class ScoredSymbol:
    symbol: str
    total_score: float
    label: SignalLabel
    factors: tuple[FactorResult, ...]
    event_risk: EventRisk
    excluded_reason: str | None


@dataclass(frozen=True)
class RankedBook:
    regime: MarketRegime
    vix_available: bool
    no_trade: bool
    no_trade_reasons: tuple[str, ...]
    ranked: tuple[ScoredSymbol, ...]
    excluded: tuple[ScoredSymbol, ...]
    published: tuple[ScoredSymbol, ...]


def rank_symbols(
    rows: list[SymbolFactors],
    *,
    as_of: datetime,
    ranking: RankingConfig,
    hard_filters: HardFilterConfig,
    regime: MarketRegime,
    regime_cfg: RegimeConfig,
    vix_available: bool,
) -> RankedBook:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    scored = [_score(row, as_of=as_of, ranking=ranking, hard_filters=hard_filters) for row in rows]
    ranked = tuple(
        sorted(
            (row for row in scored if row.excluded_reason is None),
            key=lambda row: (-row.total_score, row.symbol),
        )
    )
    excluded = tuple(row for row in scored if row.excluded_reason is not None)
    published = tuple(row for row in ranked if row.label is SignalLabel.LONG_CANDIDATE)[
        : ranking.top_n
    ]
    reasons: list[str] = []
    if regime_blocks_trading(regime, regime_cfg.no_trade_on):
        reasons.append(regime.value)
    if not published:
        reasons.append("no long candidate")
    return RankedBook(
        regime=regime,
        vix_available=vix_available,
        no_trade=bool(reasons),
        no_trade_reasons=tuple(reasons),
        ranked=ranked,
        excluded=excluded,
        published=published,
    )


def _score(
    row: SymbolFactors,
    *,
    as_of: datetime,
    ranking: RankingConfig,
    hard_filters: HardFilterConfig,
) -> ScoredSymbol:
    by_name = {factor.name: factor for factor in row.factors}
    if set(by_name) != set(_FACTORS) or any(
        factor.score is None or not factor.available for factor in by_name.values()
    ):
        raise DataUnavailableError(
            symbol=row.symbol,
            provider="factors",
            operation="rank",
            timestamp=as_of,
            reason="incomplete factor scores",
        )
    ordered = tuple(by_name[name] for name in _FACTORS)
    total = sum(float(factor.score) for factor in ordered)
    event_risk = _event_risk(by_name["event"], row.symbol, as_of)
    excluded = row.excluded_reason
    if hard_filters.block_severe_negative_event and event_risk is EventRisk.SEVERE_NEGATIVE:
        excluded = excluded or "severe negative event"
    return ScoredSymbol(
        symbol=row.symbol,
        total_score=total,
        label=_label(total, ranking),
        factors=ordered,
        event_risk=event_risk,
        excluded_reason=excluded,
    )


def _label(total: float, ranking: RankingConfig) -> SignalLabel:
    if total >= ranking.long_threshold:
        return SignalLabel.LONG_CANDIDATE
    if total >= ranking.watch_threshold:
        return SignalLabel.WATCH
    return SignalLabel.PASS


def _event_risk(factor: FactorResult, symbol: str, as_of: datetime) -> EventRisk:
    raw = dict(factor.details).get("event_risk")
    try:
        return EventRisk(raw)
    except ValueError as exc:
        raise DataUnavailableError(
            symbol=symbol,
            provider="factors",
            operation="rank",
            timestamp=as_of,
            reason="event risk missing",
        ) from exc
