"""Re-rank one saved session after scaling a single event contribution."""

from datetime import datetime

from shortalpha.config import AppConfig
from shortalpha.domain import FactorResult, MarketRegime
from shortalpha.evolution.change import EventScale
from shortalpha.factors.scale import clamp, scale_to_weight
from shortalpha.scoring.rank import RankedBook, SymbolFactors, rank_symbols

_EMPTY = "no qualifying events"


def rerank(
    snapshot: dict[str, object],
    change: EventScale,
    cfg: AppConfig,
    as_of: datetime,
) -> RankedBook:
    rows = snapshot.get("ranked")
    if not isinstance(rows, list):
        raise ValueError("snapshot is missing ranked rows")
    built = [_symbol(row, change, cfg) for row in rows if isinstance(row, dict)]
    regime = MarketRegime(str(snapshot.get("market_regime") or MarketRegime.NORMAL.value))
    return rank_symbols(
        built,
        as_of=as_of,
        ranking=cfg.ranking,
        hard_filters=cfg.hard_filters,
        regime=regime,
        regime_cfg=cfg.regime,
        vix_available=bool(snapshot.get("vix_available")),
    )


def _symbol(row: dict[str, object], change: EventScale, cfg: AppConfig) -> SymbolFactors:
    factors = row.get("factors")
    if not isinstance(factors, dict):
        raise ValueError(f"{row.get('symbol')} is missing factors")
    event = factors.get("event")
    if not isinstance(event, dict) or "classified_events" not in event:
        raise ValueError(f"{row.get('symbol')} has no classified events to rescore")
    rebuilt = []
    for name in ("momentum", "volume", "event", "relative_strength", "price_action"):
        payload = factors.get(name)
        if not isinstance(payload, dict):
            raise ValueError(f"{row.get('symbol')} is missing {name}")
        if name == "event":
            raw, score, reasons = _scaled_event(payload, change, cfg)
            rebuilt.append(_factor(name, payload, raw=raw, score=score, reasons=reasons))
        else:
            rebuilt.append(_factor(name, payload))
    return SymbolFactors(str(row["symbol"]), tuple(rebuilt))


def _scaled_event(
    payload: dict[str, object], change: EventScale, cfg: AppConfig
) -> tuple[float, float, tuple[str, ...]]:
    classified = payload.get("classified_events")
    if not isinstance(classified, list):
        raise ValueError("classified events were not a list")
    signed_sum = 0.0
    labels: list[str] = []
    for item in classified:
        if not isinstance(item, dict):
            continue
        signed = float(item["signed"])
        if str(item.get("rule_id")) == change.rule_id:
            signed *= change.scale
        signed_sum += signed
        label = str(item.get("label") or "")
        if signed > 0 and label and label not in labels:
            labels.append(label)
    raw = clamp(signed_sum, cfg.event.raw_low, cfg.event.raw_high)
    score = scale_to_weight(raw, cfg.event.raw_low, cfg.event.raw_high, cfg.weights.event)
    if not classified:
        stored = payload.get("reasons")
        if isinstance(stored, list) and stored:
            return raw, score, tuple(str(item) for item in stored)
        return raw, score, (_EMPTY,)
    return raw, score, tuple(labels)


def _factor(
    name: str,
    payload: dict[str, object],
    *,
    raw: float | None = None,
    score: float | None = None,
    reasons: tuple[str, ...] | None = None,
) -> FactorResult:
    details = payload.get("details")
    pairs: list[tuple[str, str]] = []
    if isinstance(details, dict):
        pairs = [(str(key), str(value)) for key, value in details.items()]
    stored_reasons = payload.get("reasons")
    reason_values = (
        reasons
        if reasons is not None
        else tuple(str(item) for item in stored_reasons)
        if isinstance(stored_reasons, list)
        else ()
    )
    risks = payload.get("risks")
    raw_value = payload.get("raw_value") if raw is None else raw
    score_value = payload.get("score") if score is None else score
    return FactorResult(
        name=name,
        raw_value=None if raw_value is None else float(raw_value),
        normalized_value=None,
        score=None if score_value is None else float(score_value),
        available=True,
        reasons=reason_values,
        risks=tuple(str(item) for item in risks) if isinstance(risks, list) else (),
        details=tuple(pairs),
    )
