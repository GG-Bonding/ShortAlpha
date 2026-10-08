"""Later results for the names that would actually have been published.

Watch names and high scores rejected by a confirmation rule stay beside that
book. A horizon without an exit bar stays missing. Costs and thresholds are
the locked configuration, not a fitted result.
"""

from collections.abc import Sequence
from datetime import date

_HORIZONS = (1, 2, 3, 5)
_CONFIRMATION = {
    "five-day move already exceeds 25%",
    "premarket gap already exceeds 8%",
    "post-event baseline is approximate",
    "post-event move already exceeds 8%",
    "price has not confirmed the event",
}


def format_outcomes(
    days: Sequence[tuple[date, dict[str, object], Sequence[dict[str, object]]]],
    *,
    cost: float,
    long_threshold: float,
    watch_threshold: float,
) -> str:
    lines = [
        "实际会执行的名单",
        f"round_trip_cost: {cost}",
        "未到期的周期保持缺失",
        "",
        "实际发布",
    ]
    lines.extend(_group(days, cost, selector=lambda row, snap: _published(row, snap)))
    lines.append("")
    lines.append("观察70-80")
    lines.extend(
        _group(
            days,
            cost,
            selector=lambda row, _snap: _watch(row, watch_threshold, long_threshold),
        )
    )
    lines.append("")
    lines.append("80分被确认规则拒绝")
    lines.extend(_group(days, cost, selector=lambda row, _snap: _rejected(row, long_threshold)))
    return "\n".join(lines)


def _group(
    days: Sequence[tuple[date, dict[str, object], Sequence[dict[str, object]]]],
    cost: float,
    *,
    selector,
) -> list[str]:
    lines: list[str] = []
    found = False
    for session, snapshot, forwards in days:
        chosen = [row for row in _ranked(snapshot) if selector(row, snapshot)]
        if not chosen:
            continue
        found = True
        by_symbol = _forwards(forwards)
        for row in chosen:
            symbol = str(row["symbol"])
            score = row["total_score"]
            saved = by_symbol.get(symbol, {})
            for horizon in _HORIZONS:
                item = saved.get(horizon)
                if item is None:
                    lines.append(f"{session.isoformat()} {symbol} {score} {horizon}D 缺失")
                    continue
                stock = float(item["stock_return"])
                spy = float(item["spy_return"])
                net = stock - cost
                lines.append(
                    f"{session.isoformat()} {symbol} {score} {horizon}D "
                    f"扣成本={_fmt(net)} 超额={_fmt(net - spy)} 不利波动={_fmt(float(item['mae']))}"
                )
    if not found:
        lines.append("无")
    return lines


def _published(row: dict[str, object], snapshot: dict[str, object]) -> bool:
    if snapshot.get("no_trade") is True:
        return False
    symbols = snapshot.get("symbols")
    if not isinstance(symbols, list):
        return False
    symbol = row.get("symbol")
    return any(isinstance(item, dict) and item.get("symbol") == symbol for item in symbols)


def _watch(row: dict[str, object], watch_threshold: float, long_threshold: float) -> bool:
    if row.get("thesis_veto"):
        return False
    score = row.get("total_score")
    if not isinstance(score, (int, float)):
        return False
    return watch_threshold <= score < long_threshold


def _rejected(row: dict[str, object], long_threshold: float) -> bool:
    score = row.get("total_score")
    veto = row.get("thesis_veto")
    if not isinstance(score, (int, float)) or not isinstance(veto, str):
        return False
    if score < long_threshold:
        return False
    if veto == "post-event baseline is approximate":
        return bool(_detail(row, "pre_event_price"))
    return veto in _CONFIRMATION


def _ranked(snapshot: dict[str, object]) -> list[dict[str, object]]:
    rows = snapshot.get("ranked")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _forwards(rows: Sequence[dict[str, object]]) -> dict[str, dict[int, dict[str, object]]]:
    found: dict[str, dict[int, dict[str, object]]] = {}
    for row in rows:
        symbol = row.get("symbol")
        horizon = row.get("horizon")
        if not isinstance(symbol, str) or not isinstance(horizon, int):
            continue
        found.setdefault(symbol, {})[horizon] = row
    return found


def _detail(row: dict[str, object], key: str) -> str:
    factors = row.get("factors")
    if not isinstance(factors, dict):
        return ""
    event = factors.get("event")
    if not isinstance(event, dict):
        return ""
    details = event.get("details")
    if not isinstance(details, dict):
        return ""
    value = details.get(key)
    if value in {None, "", "missing"}:
        return ""
    return str(value)


def _fmt(value: float) -> str:
    return f"{value:.4f}"
