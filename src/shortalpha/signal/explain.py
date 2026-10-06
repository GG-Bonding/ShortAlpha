"""Explain one symbol from a stored snapshot."""

from shortalpha.errors import ShortAlphaError

_LABELS = {
    "momentum": "Momentum",
    "volume": "Volume",
    "event": "Event",
    "relative_strength": "Relative Strength",
    "price_action": "Price Action",
}


def format_scan(snapshot: dict[str, object]) -> str:
    clock = str(snapshot["signal_time"])[:5]
    lines = [
        "ShortAlpha",
        f"{snapshot['signal_date']} {clock} ET",
        "",
        "Market:",
        str(snapshot["market_regime"]),
        "",
    ]
    symbols = snapshot.get("symbols")
    if not isinstance(symbols, list):
        symbols = []
    for row in symbols:
        if not isinstance(row, dict):
            continue
        lines.extend(_symbol_lines(row))
        lines.append("----------------------------")
        lines.append("")
    missing = _missing_same_day(snapshot)
    if missing:
        lines.append("Same-day price missing:")
        lines.append(str(missing))
        lines.append("")
    lines.append("NO_TRADE:")
    lines.append(str(bool(snapshot["no_trade"])).lower())
    return "\n".join(lines)


def _symbol_lines(row: dict[str, object]) -> list[str]:
    factors = row["factors"]
    if not isinstance(factors, dict):
        raise ShortAlphaError(f"{row.get('symbol')} snapshot is missing factor detail")
    lines = [
        f"#{row['rank']} {row['symbol']}",
        "",
        "Score:",
        _num(float(row["total_score"])),
        "",
        "Signal:",
        str(row["signal"]),
        "",
        "Same-day price:",
        _same_day(factors),
        "",
        "Thesis:",
        str(row.get("thesis") or "none"),
        "",
    ]
    reasons: list[str] = []
    risks: list[str] = []
    for name, label in _LABELS.items():
        factor = factors[name]
        if not isinstance(factor, dict):
            raise ShortAlphaError(f"{row.get('symbol')} is missing {name}")
        lines.append(f"{label:<16}{_num(float(factor['score']))} / {_num(float(factor['weight']))}")
        reasons.extend(str(item) for item in factor["reasons"])
        risks.extend(str(item) for item in factor["risks"])
    lines.append("")
    lines.append("Reasons:")
    lines.extend(f"+ {reason}" for reason in reasons)
    if not reasons:
        lines.append("none")
    lines.append("")
    lines.append("Risks:")
    lines.extend(f"- {risk}" for risk in risks)
    if not risks:
        lines.append("none")
    return lines


def format_explanation(snapshot: dict[str, object], symbol: str, *, run_id: str) -> str:
    row = _find(snapshot, symbol)
    if row is None:
        raise ShortAlphaError(f"{symbol} is not in this snapshot")
    factors = row["factors"]
    if not isinstance(factors, dict):
        raise ShortAlphaError(f"{symbol} snapshot is missing factor detail")
    lines = [
        f"run_id: {run_id}",
        f"signal_date: {snapshot['signal_date']}",
        symbol,
        f"Score: {_num(float(row['total_score']))}",
        f"Signal: {row['signal']}",
        f"Rank: {row['rank'] if row['rank'] is not None else 'excluded'}",
        f"Same-day price: {_same_day(factors)}",
        "",
        "Thesis:",
        str(row.get("thesis") or "none"),
        "",
    ]
    reasons: list[str] = []
    risks: list[str] = []
    for name in _LABELS:
        factor = factors[name]
        if not isinstance(factor, dict):
            raise ShortAlphaError(f"{symbol} is missing {name}")
        lines.append(
            f"{_LABELS[name]:<16}{_num(float(factor['score']))} / {_num(float(factor['weight']))}"
        )
        reasons.extend(str(item) for item in factor["reasons"])
        risks.extend(str(item) for item in factor["risks"])
    lines.append("")
    lines.append("Reasons:")
    lines.extend(f"+ {reason}" for reason in reasons)
    if not reasons:
        lines.append("none")
    lines.append("")
    lines.append("Risks:")
    lines.extend(f"- {risk}" for risk in risks)
    if not risks:
        lines.append("none")
    if row.get("excluded_reason"):
        lines.append("")
        lines.append(f"Excluded: {row['excluded_reason']}")
    return "\n".join(lines)


def _same_day(factors: dict[str, object]) -> str:
    price = factors.get("price_action")
    if not isinstance(price, dict):
        return "not recorded"
    details = price.get("details")
    if not isinstance(details, dict) or details.get("degraded") == "true" or "gap" not in details:
        return "missing"
    return f"premarket gap {float(str(details['gap'])) * 100:+.1f}%"


def _missing_same_day(snapshot: dict[str, object]) -> int:
    rows = snapshot.get("ranked")
    if not isinstance(rows, list):
        return 0
    count = 0
    for row in rows:
        if isinstance(row, dict) and row.get("thesis_veto") == (
            "same-day price confirmation is missing"
        ):
            count += 1
    return count


def _find(snapshot: dict[str, object], symbol: str) -> dict[str, object] | None:
    for key in ("symbols", "ranked", "excluded"):
        rows = snapshot.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and row.get("symbol") == symbol:
                return row
    return None


def _num(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")
