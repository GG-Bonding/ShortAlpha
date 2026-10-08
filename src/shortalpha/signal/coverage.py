"""Coverage and rejection report. Missing data is not called a lack of opportunity."""

from shortalpha.capture import Capture
from shortalpha.domain import SignalRun

_CONFIRMATION = {
    "five-day move already exceeds 25%",
    "premarket gap already exceeds 8%",
    "post-event move already exceeds 8%",
    "price has not confirmed the event",
}
_INSUFFICIENT = {
    "same-day price confirmation is missing",
    "no price after the event",
}
_NO_EVENT = "no positive classified change"
_STALE = "post-event baseline is approximate"


def judgment(row: dict[str, object], *, long_threshold: float) -> str:
    """One reason for a scored symbol. Data gaps stay out of the no-opportunity bucket."""
    if row.get("excluded_reason"):
        return "no_opportunity"
    veto = row.get("thesis_veto")
    veto_text = veto if isinstance(veto, str) else ""
    if veto_text == _NO_EVENT:
        return "no_opportunity"
    if veto_text in _INSUFFICIENT:
        return "insufficient_data"
    if veto_text == _STALE:
        if _detail(row, "event", "pre_event_price"):
            return "confirmation_reject"
        return "insufficient_data"
    if veto_text in _CONFIRMATION or veto_text:
        return "confirmation_reject"
    score = row.get("total_score")
    if not isinstance(score, (int, float)):
        return "insufficient_data"
    if score >= long_threshold:
        return "qualified"
    return "no_opportunity"


def format_coverage(
    run: SignalRun,
    snapshot: dict[str, object],
    capture: Capture,
    *,
    long_threshold: float,
) -> str:
    rows = _scored(snapshot)
    counts = {
        name: 0
        for name in ("no_opportunity", "insufficient_data", "confirmation_reject", "qualified")
    }
    for row in rows:
        counts[judgment(row, long_threshold=long_threshold)] += 1
    published = _published(snapshot)
    failures = [item for item in capture.failures if item.operation != "minute_price"]
    minute_failures = [item for item in capture.failures if item.operation == "minute_price"]
    lines = [
        "覆盖",
        f"git: {capture.git_revision or 'unknown'}",
        f"code_version: {run.code_version}",
        f"股票池: {run.universe_size}",
        f"通过流动性: {run.eligible_size}",
        f"完成评分: {run.scored_size}",
        f"行情缺失: {len(capture.gaps) + _count(failures, 'data', 'daily_bars')}",
        f"新闻请求失败: {_count_operation(failures, 'news')}",
        f"事件前基准缺失: {sum(_baseline_missing(row) for row in rows)}",
        f"盘前价格缺失: {sum(not _detail(row, 'price_action', 'price_time') for row in rows)}",
        f"80分及以上: {sum(float(row['total_score']) >= long_threshold for row in rows)}",
        "最终前3名:",
    ]
    if snapshot.get("no_trade") is True:
        reasons = snapshot.get("no_trade_reasons") or []
        reason_text = " ".join(str(item) for item in reasons) if isinstance(reasons, list) else ""
        lines.append(f"无 ({reason_text or 'NO_TRADE'})")
    elif not published:
        lines.append("无")
    else:
        lines.extend(_name_block(row, capture, long_threshold=long_threshold) for row in published)
    lines.extend(
        [
            "",
            f"没有合格机会: {counts['no_opportunity']}",
            f"其中观察70-80: {sum(row.get('signal') == 'WATCH' for row in rows)}",
            f"数据不足以判断: {counts['insufficient_data']}",
            f"确认规则拒绝: {counts['confirmation_reject']}",
            f"达到80但未进前3: {_outside_top(rows, published, long_threshold)}",
            f"接口权限错误: {sum(item.kind == 'auth' for item in failures)}",
            f"接口限流: {sum(item.kind == 'rate_limit' for item in failures)}",
            f"请求失败: {sum(item.kind == 'request' for item in failures)}",
            f"分钟价请求失败: {len(minute_failures)}",
            "",
            "高分被拒绝:",
        ]
    )
    rejected = _rejected(rows, published, long_threshold)
    if not rejected:
        lines.append("无")
    else:
        lines.extend(_name_block(row, capture, long_threshold=long_threshold) for row in rejected)
    if failures or minute_failures or capture.gaps:
        lines.append("")
        lines.append("未进入评分或请求失败:")
        for symbol, reason in capture.gaps:
            lines.append(f"{symbol} 数据不足以判断 {reason}")
        for item in (*failures, *minute_failures):
            lines.append(f"{item.symbol} {item.kind} {item.operation} {item.reason}")
    return "\n".join(lines)


def _published(snapshot: dict[str, object]) -> list[dict[str, object]]:
    if snapshot.get("no_trade") is True:
        return []
    rows = snapshot.get("symbols")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _scored(snapshot: dict[str, object]) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for key in ("ranked", "excluded"):
        rows = snapshot.get(key)
        if isinstance(rows, list):
            found.extend(row for row in rows if isinstance(row, dict))
    return found


def _rejected(
    rows: list[dict[str, object]],
    published: list[dict[str, object]],
    long_threshold: float,
) -> list[dict[str, object]]:
    chosen = {str(row.get("symbol")) for row in published}
    high = [
        row
        for row in rows
        if str(row.get("symbol")) not in chosen and float(row["total_score"]) >= long_threshold
    ]
    return sorted(high, key=lambda row: (-float(row["total_score"]), str(row.get("symbol"))))


def _outside_top(
    rows: list[dict[str, object]],
    published: list[dict[str, object]],
    long_threshold: float,
) -> int:
    chosen = {str(row.get("symbol")) for row in published}
    return sum(
        1
        for row in rows
        if str(row.get("symbol")) not in chosen
        and judgment(row, long_threshold=long_threshold) == "qualified"
    )


def _baseline_missing(row: dict[str, object]) -> bool:
    event = _factor(row, "event")
    raw = event.get("raw_value")
    reasons = event.get("reasons") or []
    positive = isinstance(raw, (int, float)) and raw > 0 and "no qualifying events" not in reasons
    return positive and not _detail(row, "event", "pre_event_price")


def _name_block(row: dict[str, object], capture: Capture, *, long_threshold: float) -> str:
    symbol = str(row.get("symbol"))
    kind = judgment(row, long_threshold=long_threshold)
    veto = row.get("thesis_veto") or "none"
    lines = [
        f"{symbol} 分数={row.get('total_score')} 信号={row.get('signal')} 分类={kind} 原因={veto}"
    ]
    headlines = {item.id: item for item in capture.news}
    for event in _events(row):
        news_id = str(event.get("news_id"))
        saved = headlines.get(news_id)
        headline = saved.headline if saved is not None else ""
        published = event.get("published_at")
        lines.append(f"  新闻 {published} {headline}".rstrip())
    lines.append(
        "  基准 "
        f"{_detail(row, 'event', 'pre_event_price') or '缺失'} @ "
        f"{_detail(row, 'event', 'pre_event_price_time') or '缺失'} "
        f"间隔秒={_detail(row, 'event', 'pre_event_age_seconds') or '缺失'}"
    )
    lines.append(
        "  确认价 "
        f"{_detail(row, 'price_action', 'last_price') or '缺失'} @ "
        f"{_detail(row, 'price_action', 'price_time') or '缺失'} "
        f"合并行情={_detail(row, 'price_action', 'price_consolidated') or '缺失'}"
    )
    return "\n".join(lines)


def _events(row: dict[str, object]) -> list[dict[str, object]]:
    factor = _factor(row, "event")
    events = factor.get("classified_events")
    if not isinstance(events, list):
        return []
    return [item for item in events if isinstance(item, dict)]


def _factor(row: dict[str, object], name: str) -> dict[str, object]:
    factors = row.get("factors")
    if not isinstance(factors, dict):
        return {}
    factor = factors.get(name)
    return factor if isinstance(factor, dict) else {}


def _detail(row: dict[str, object], factor: str, key: str) -> str:
    details = _factor(row, factor).get("details")
    if not isinstance(details, dict):
        return ""
    value = details.get(key)
    if value in {None, "", "missing"}:
        return ""
    return str(value)


def _count(failures: list[object], kind: str, operation: str) -> int:
    return sum(
        getattr(item, "kind", None) == kind and getattr(item, "operation", None) == operation
        for item in failures
    )


def _count_operation(failures: list[object], operation: str) -> int:
    return sum(getattr(item, "operation", None) == operation for item in failures)
