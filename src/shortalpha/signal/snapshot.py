"""Build and store a signal snapshot. A second run gets a new id."""

import hashlib
import json
import uuid
from datetime import date, datetime
from pathlib import Path

from shortalpha.config import WeightsConfig
from shortalpha.domain import ClassifiedEvent, FactorResult, SignalRun
from shortalpha.scoring.rank import RankedBook, ScoredSymbol
from shortalpha.storage import Store

_WEIGHTS = {
    "momentum": "momentum",
    "volume": "volume",
    "event": "event",
    "relative_strength": "relative_strength",
    "price_action": "price_action",
}


def build_snapshot(
    book: RankedBook,
    *,
    signal_date: date,
    signal_time: datetime,
    timezone: str,
    weights: WeightsConfig,
) -> dict[str, object]:
    if signal_time.tzinfo is None or signal_time.utcoffset() is None:
        raise ValueError("signal_time must be timezone-aware")
    clock = signal_time.strftime("%H:%M:%S")
    return {
        "excluded": [
            _symbol_payload(row, None, weights)
            for row in sorted(book.excluded, key=lambda item: item.symbol)
        ],
        "market_regime": book.regime.value,
        "no_trade": book.no_trade,
        "no_trade_reasons": list(book.no_trade_reasons),
        "ranked": [
            _symbol_payload(row, rank, weights) for rank, row in enumerate(book.ranked, start=1)
        ],
        "signal_date": signal_date.isoformat(),
        "signal_time": clock,
        "symbols": [
            _symbol_payload(row, rank, weights) for rank, row in enumerate(book.published, start=1)
        ],
        "timezone": timezone,
        "vix_available": book.vix_available,
    }


def canonical_snapshot(document: dict[str, object]) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def snapshot_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def write_snapshot_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)


def save_signal(
    store: Store,
    book: RankedBook,
    *,
    signal_date: date,
    signal_time: datetime,
    timezone: str,
    weights: WeightsConfig,
    config_hash: str,
    code_version: str,
    created_at: datetime,
    universe_size: int | None,
    eligible_size: int | None,
    provider_errors: int,
    missing_data_count: int,
    universe_list_as_of: date | None,
    point_in_time_membership: bool,
    notes: str,
    strategy_version: str,
    run_mode: str,
    event_rules_hash: str,
    experiment_id: str = "",
    output_dir: Path | None = None,
    run_id: str | None = None,
    duration_ms: int | None = None,
) -> str:
    run_id = run_id or str(uuid.uuid4())
    document = build_snapshot(
        book,
        signal_date=signal_date,
        signal_time=signal_time,
        timezone=timezone,
        weights=weights,
    )
    text = canonical_snapshot(document)
    digest = snapshot_hash(text)
    run = SignalRun(
        run_id=run_id,
        signal_date=signal_date,
        signal_time=signal_time,
        timezone=timezone,
        config_hash=config_hash,
        code_version=code_version,
        created_at=created_at,
        universe_size=universe_size,
        eligible_size=eligible_size,
        scored_size=len(book.ranked) + len(book.excluded),
        candidate_size=len(book.published),
        duration_ms=duration_ms,
        provider_errors=provider_errors,
        missing_data_count=missing_data_count,
        market_regime=book.regime,
        no_trade=book.no_trade,
        status="ok",
        universe_list_as_of=universe_list_as_of,
        point_in_time_membership=point_in_time_membership,
        notes=notes,
        strategy_version=strategy_version,
        run_mode=run_mode,
        event_rules_hash=event_rules_hash,
        experiment_id=experiment_id,
    )
    store.insert_signal(run, text, digest, _factor_rows(book))
    if output_dir is not None:
        write_snapshot_file(output_dir / f"{signal_date.isoformat()}-{run_id}.json", text)
    return run_id


def _symbol_payload(
    row: ScoredSymbol, rank: int | None, weights: WeightsConfig
) -> dict[str, object]:
    factors: dict[str, object] = {}
    for factor in row.factors:
        factors[factor.name] = {
            "classified_events": [_event_payload(item) for item in factor.events],
            "details": dict(factor.details),
            "normalized_value": _round(factor.normalized_value),
            "raw_value": _round(factor.raw_value),
            "reasons": list(factor.reasons),
            "risks": list(factor.risks),
            "score": _round(factor.score),
            "weight": getattr(weights, _WEIGHTS[factor.name]),
        }
    return {
        "event_risk": row.event_risk.value,
        "excluded_reason": row.excluded_reason,
        "factors": factors,
        "rank": rank,
        "signal": row.label.value,
        "symbol": row.symbol,
        "thesis": row.thesis,
        "thesis_veto": row.thesis_veto,
        "total_score": _round(row.total_score),
    }


def _event_payload(event: ClassifiedEvent) -> dict[str, object]:
    return {
        "available_at": event.available_at.isoformat(),
        "content_sha256": event.content_sha256,
        "direction": event.direction,
        "family": event.family,
        "freshness": event.freshness,
        "importance": event.importance,
        "label": event.label,
        "news_id": event.news_id,
        "published_at": event.published_at.isoformat(),
        "reaction": event.reaction,
        "rule_id": event.rule_id,
        "rules_sha256": event.rules_sha256,
        "signed": event.signed,
        "source": event.source,
    }


def _factor_rows(book: RankedBook) -> list[tuple[str, FactorResult]]:
    rows: list[tuple[str, FactorResult]] = []
    for scored in (*book.ranked, *sorted(book.excluded, key=lambda item: item.symbol)):
        for factor in scored.factors:
            rows.append((scored.symbol, factor))
    return rows


def _round(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 4)
