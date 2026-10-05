"""Identity of the inputs behind a snapshot. Scores are not part of it."""

import hashlib
import json


def input_fingerprint(document: dict[str, object]) -> str:
    by_symbol: dict[str, dict[str, object]] = {}
    for key in ("excluded", "ranked"):
        raw = document.get(key)
        if not isinstance(raw, list):
            continue
        for row in raw:
            if not isinstance(row, dict) or "symbol" not in row:
                continue
            by_symbol[str(row["symbol"])] = _symbol_inputs(row)
    body = {
        "market_regime": document.get("market_regime"),
        "signal_date": document.get("signal_date"),
        "signal_time": document.get("signal_time"),
        "symbols": [by_symbol[symbol] for symbol in sorted(by_symbol)],
        "timezone": document.get("timezone"),
        "vix_available": document.get("vix_available"),
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _symbol_inputs(row: dict[str, object]) -> dict[str, object]:
    factors = row.get("factors")
    packed: dict[str, object] = {}
    if isinstance(factors, dict):
        for name in sorted(factors):
            factor = factors[name]
            if not isinstance(factor, dict):
                continue
            if name == "event":
                packed[name] = {"events": _event_inputs(factor.get("classified_events"))}
            else:
                packed[name] = {
                    "details": factor.get("details"),
                    "raw_value": factor.get("raw_value"),
                }
    return {"factors": packed, "symbol": row["symbol"]}


def _event_inputs(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        return []
    events: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        events.append(
            {
                "available_at": item.get("available_at"),
                "content_sha256": item.get("content_sha256"),
                "news_id": item.get("news_id"),
                "published_at": item.get("published_at"),
                "reaction": item.get("reaction"),
                "rule_id": item.get("rule_id"),
            }
        )
    events.sort(key=lambda item: (str(item["news_id"]), str(item["rule_id"])))
    return events
