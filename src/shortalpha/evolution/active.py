"""Resolve the strategy a scan, evaluation, or proposal actually runs."""

import hashlib
from datetime import datetime, time
from zoneinfo import ZoneInfo

from shortalpha.calendar import NYSECalendar
from shortalpha.config import AppConfig
from shortalpha.event_rules import EventRules, rules_from_json
from shortalpha.storage import Store


def next_signal_at(
    opened: datetime, calendar: NYSECalendar, clock: time, zone: ZoneInfo
) -> datetime:
    """First signal strictly after the experiment was opened."""
    if opened.tzinfo is None or opened.utcoffset() is None:
        raise ValueError("opened_at must be timezone-aware")
    local = opened.astimezone(zone)
    day = local.date()
    if calendar.is_trading_day(day):
        signal = calendar.signal_time(day, clock, zone)
        if signal > local:
            return signal
        day = calendar.shift(day, 1)
    else:
        day = calendar.shift(day, 1)
    return calendar.signal_time(day, clock, zone)


def signal_is_in_forward_window(shadow_starts_at: str | None, signal_at: datetime) -> bool:
    if not shadow_starts_at:
        return False
    return signal_at >= datetime.fromisoformat(shadow_starts_at)


def resolve_strategy(
    store: Store,
    cfg: AppConfig,
    file_rules: EventRules,
    *,
    requested: str | None,
    file_hash: str,
) -> tuple[str, EventRules, str]:
    version = requested or store.official_version(cfg.strategy.version)
    rules = rules_for_version(store, file_rules, version, cfg.strategy.version)
    return version, rules, _rules_hash(store, version, file_hash)


def resolve_shadow(
    store: Store,
    cfg: AppConfig,
    file_rules: EventRules,
    experiment_id: str,
    *,
    file_hash: str,
) -> tuple[str, EventRules, str]:
    row = store.experiment(experiment_id)
    if row is None:
        raise ValueError(f"unknown experiment {experiment_id}")
    version = str(row["candidate_version"])
    rules = rules_for_version(store, file_rules, version, cfg.strategy.version)
    return version, rules, _rules_hash(store, version, file_hash)


def rules_for_version(
    store: Store,
    file_rules: EventRules,
    version: str,
    config_version: str,
) -> EventRules:
    row = store.latest_registry(version)
    frozen = ""
    if row is not None and row["rules_json"]:
        frozen = str(row["rules_json"])
    if frozen:
        return rules_from_json(frozen)
    if version == config_version:
        return file_rules
    raise ValueError(f"unknown strategy version {version}")


def _rules_hash(store: Store, version: str, file_hash: str) -> str:
    row = store.latest_registry(version)
    if row is not None and row["rules_json"]:
        return hashlib.sha256(str(row["rules_json"]).encode()).hexdigest()
    return file_hash
