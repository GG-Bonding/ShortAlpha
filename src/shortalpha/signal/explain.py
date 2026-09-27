"""Explain one symbol from a stored snapshot."""

from shortalpha.errors import ShortAlphaError

_LABELS = {
    "momentum": "Momentum",
    "volume": "Volume",
    "event": "Event",
    "relative_strength": "Relative Strength",
    "price_action": "Price Action",
}


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
