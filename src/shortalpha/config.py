"""Load and reject an inconsistent V0.1 configuration."""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from shortalpha.errors import ConfigError

_TOP_LEVEL = {
    "signal",
    "universe",
    "ranking",
    "weights",
    "momentum",
    "volume",
    "event",
    "relative_strength",
    "price_action",
    "hard_filters",
    "regime",
    "premarket",
    "benchmarks",
    "providers",
    "database",
    "alpaca",
    "calendar",
    "evaluation",
    "logging",
    "fixtures",
}
_FIXED_HORIZONS = (1, 2, 3, 5)
_WEIGHT_TOLERANCE = 1e-9


@dataclass(frozen=True)
class SignalConfig:
    timezone: str
    time: time


@dataclass(frozen=True)
class UniverseFilters:
    min_price: float
    min_avg_dollar_volume_20d: float


@dataclass(frozen=True)
class UniverseFiles:
    sp500: str
    nasdaq100: str


@dataclass(frozen=True)
class UniverseAsOf:
    sp500: date
    nasdaq100: date


@dataclass(frozen=True)
class UniverseConfig:
    sources: tuple[str, ...]
    filters: UniverseFilters
    files: UniverseFiles
    as_of: UniverseAsOf
    point_in_time_membership: bool


@dataclass(frozen=True)
class RankingConfig:
    top_n: int
    long_threshold: float
    watch_threshold: float


@dataclass(frozen=True)
class WeightsConfig:
    momentum: float
    volume: float
    event: float
    relative_strength: float
    price_action: float


@dataclass(frozen=True)
class MomentumConfig:
    w1: float
    w3: float
    w5: float
    overheat_r5: float
    overheat_multiplier: float
    raw_low: float
    raw_high: float


@dataclass(frozen=True)
class VolumeConfig:
    lookback: int
    ratio_low: float
    ratio_high: float


@dataclass(frozen=True)
class EventConfig:
    freshness_lambda: float
    duplicate_window_hours: float
    duplicate_jaccard: float
    max_age_hours: float
    severe_importance: float
    severe_freshness: float
    raw_low: float
    raw_high: float


@dataclass(frozen=True)
class RelativeStrengthConfig:
    market_weight: float
    sector_weight: float
    lookback_sessions: int
    raw_low: float
    raw_high: float
    sector_map: str


@dataclass(frozen=True)
class PriceActionConfig:
    gap_penalty_start: float
    gap_hard_penalty: float
    gap_penalty_floor: float
    atr_lookback: int
    atr_elevated: float
    gap_component_max: float
    break_bonus: float
    elevated_atr_scale: float


@dataclass(frozen=True)
class HardFilterConfig:
    block_severe_negative_event: bool


@dataclass(frozen=True)
class RegimeConfig:
    extreme_spy_5d: float
    risk_off_spy_5d: float
    risk_off_qqq_5d: float
    risk_off_vix: float
    extreme_vix: float
    no_trade_on: str


@dataclass(frozen=True)
class PremarketConfig:
    start: time


@dataclass(frozen=True)
class BenchmarkConfig:
    market: str
    growth: str
    volatility: str


@dataclass(frozen=True)
class ProviderConfig:
    market: str
    premarket: str
    news: str
    universe: str
    calendar: str


@dataclass(frozen=True)
class DatabaseConfig:
    path: str


@dataclass(frozen=True)
class AlpacaConfig:
    feed: str
    adjustment: str
    data_base_url: str


@dataclass(frozen=True)
class CalendarConfig:
    extra_holidays: tuple[date, ...]
    extra_early_closes: tuple[date, ...]


@dataclass(frozen=True)
class EvaluationConfig:
    horizons: tuple[int, ...]
    min_bucket_count: int
    buckets: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class LoggingConfig:
    level: str


@dataclass(frozen=True)
class FixtureConfig:
    market: str
    premarket: str
    news: str
    universe: str


@dataclass(frozen=True)
class AppConfig:
    signal: SignalConfig
    universe: UniverseConfig
    ranking: RankingConfig
    weights: WeightsConfig
    momentum: MomentumConfig
    volume: VolumeConfig
    event: EventConfig
    relative_strength: RelativeStrengthConfig
    price_action: PriceActionConfig
    hard_filters: HardFilterConfig
    regime: RegimeConfig
    premarket: PremarketConfig
    benchmarks: BenchmarkConfig
    providers: ProviderConfig
    database: DatabaseConfig
    alpaca: AlpacaConfig
    calendar: CalendarConfig
    evaluation: EvaluationConfig
    logging: LoggingConfig
    fixtures: FixtureConfig


def load_config(path: Path, root: Path | None = None) -> AppConfig:
    path = path.resolve()
    if root is None:
        root = path.parent
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    loaded = yaml.safe_load(path.read_text())
    if not isinstance(loaded, dict):
        raise ConfigError("config root must be a mapping")
    _exact_keys(loaded, _TOP_LEVEL, "config")
    cfg = _build(loaded, root)
    return cfg


def config_hash(cfg: AppConfig) -> str:
    payload = json.dumps(_jsonable(asdict(cfg)), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _build(data: dict[str, Any], root: Path) -> AppConfig:
    signal = _section(data, "signal", {"timezone", "time"})
    timezone_name = _as_str(signal, "timezone")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"unknown timezone: {timezone_name}") from exc

    universe = _universe_config(
        _section(
            data,
            "universe",
            {"sources", "filters", "files", "as_of", "point_in_time_membership"},
        ),
        root,
    )

    ranking = _section(data, "ranking", {"top_n", "long_threshold", "watch_threshold"})
    long_threshold = _as_number(ranking, "long_threshold")
    watch_threshold = _as_number(ranking, "watch_threshold")
    if not 0 <= watch_threshold < long_threshold <= 100:
        raise ConfigError("watch_threshold must be >= 0 and strictly below long_threshold")

    weights = _section(
        data,
        "weights",
        {"momentum", "volume", "event", "relative_strength", "price_action"},
    )
    weight_values = {name: _as_number(weights, name) for name in weights}
    weight_sum = sum(weight_values.values())
    if abs(weight_sum - 100) > _WEIGHT_TOLERANCE:
        raise ConfigError(f"sum(weights) must equal 100, got {weight_sum}")
    if any(value <= 0 for value in weight_values.values()):
        raise ConfigError("each weight must be positive")

    momentum = _momentum(
        _section(
            data,
            "momentum",
            {"w1", "w3", "w5", "overheat_r5", "overheat_multiplier", "raw_low", "raw_high"},
        )
    )
    volume = _volume(_section(data, "volume", {"lookback", "ratio_low", "ratio_high"}))
    event = _event(
        _section(
            data,
            "event",
            {
                "freshness_lambda",
                "duplicate_window_hours",
                "duplicate_jaccard",
                "max_age_hours",
                "severe_importance",
                "severe_freshness",
                "raw_low",
                "raw_high",
            },
        )
    )
    relative = _relative_strength(
        _section(
            data,
            "relative_strength",
            {
                "market_weight",
                "sector_weight",
                "lookback_sessions",
                "raw_low",
                "raw_high",
                "sector_map",
            },
        ),
        root,
    )
    price_action = _price_action(
        _section(
            data,
            "price_action",
            {
                "gap_penalty_start",
                "gap_hard_penalty",
                "gap_penalty_floor",
                "atr_lookback",
                "atr_elevated",
                "gap_component_max",
                "break_bonus",
                "elevated_atr_scale",
            },
        ),
        weight_values["price_action"],
    )
    hard_filters = _section(data, "hard_filters", {"block_severe_negative_event"})
    regime = _regime(
        _section(
            data,
            "regime",
            {
                "extreme_spy_5d",
                "risk_off_spy_5d",
                "risk_off_qqq_5d",
                "risk_off_vix",
                "extreme_vix",
                "no_trade_on",
            },
        )
    )
    premarket = _section(data, "premarket", {"start"})
    benchmarks = _benchmarks(_section(data, "benchmarks", {"market", "growth", "volatility"}))
    providers = _providers(
        _section(data, "providers", {"market", "premarket", "news", "universe", "calendar"})
    )
    database = _section(data, "database", {"path"})
    alpaca = _alpaca(_section(data, "alpaca", {"feed", "adjustment", "data_base_url"}))
    calendar = _calendar(_section(data, "calendar", {"extra_holidays", "extra_early_closes"}))
    evaluation = _evaluation(
        _section(data, "evaluation", {"horizons", "min_bucket_count", "buckets"})
    )
    logging_cfg = _section(data, "logging", {"level"})
    level = _as_str(logging_cfg, "level")
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ConfigError(f"unsupported logging level: {level}")
    fixtures = _fixtures(
        _section(data, "fixtures", {"market", "premarket", "news", "universe"}),
        root,
    )
    return AppConfig(
        signal=SignalConfig(timezone=timezone_name, time=_as_clock(signal, "time")),
        universe=universe,
        ranking=RankingConfig(
            top_n=_positive_int(ranking, "top_n"),
            long_threshold=long_threshold,
            watch_threshold=watch_threshold,
        ),
        weights=WeightsConfig(**weight_values),
        momentum=momentum,
        volume=volume,
        event=event,
        relative_strength=relative,
        price_action=price_action,
        hard_filters=HardFilterConfig(
            block_severe_negative_event=_as_bool(hard_filters, "block_severe_negative_event")
        ),
        regime=regime,
        premarket=PremarketConfig(start=_as_clock(premarket, "start")),
        benchmarks=benchmarks,
        providers=providers,
        database=DatabaseConfig(path=_as_str(database, "path")),
        alpaca=alpaca,
        calendar=calendar,
        evaluation=evaluation,
        logging=LoggingConfig(level=level),
        fixtures=fixtures,
    )


def _momentum(data: dict[str, Any]) -> MomentumConfig:
    cfg = MomentumConfig(
        w1=_as_number(data, "w1"),
        w3=_as_number(data, "w3"),
        w5=_as_number(data, "w5"),
        overheat_r5=_as_number(data, "overheat_r5"),
        overheat_multiplier=_as_number(data, "overheat_multiplier"),
        raw_low=_as_number(data, "raw_low"),
        raw_high=_as_number(data, "raw_high"),
    )
    if abs((cfg.w1 + cfg.w3 + cfg.w5) - 1) > _WEIGHT_TOLERANCE:
        raise ConfigError("momentum weights w1+w3+w5 must sum to 1")
    if min(cfg.w1, cfg.w3, cfg.w5) < 0:
        raise ConfigError("momentum weights must be >= 0")
    if cfg.overheat_r5 <= 0 or not 0 <= cfg.overheat_multiplier <= 1:
        raise ConfigError("momentum overheat settings are out of range")
    if cfg.raw_low >= cfg.raw_high:
        raise ConfigError("momentum raw_low must be below raw_high")
    return cfg


def _volume(data: dict[str, Any]) -> VolumeConfig:
    cfg = VolumeConfig(
        lookback=_positive_int(data, "lookback"),
        ratio_low=_as_number(data, "ratio_low"),
        ratio_high=_as_number(data, "ratio_high"),
    )
    if cfg.ratio_low >= cfg.ratio_high:
        raise ConfigError("volume ratio_low must be below ratio_high")
    return cfg


def _event(data: dict[str, Any]) -> EventConfig:
    cfg = EventConfig(
        freshness_lambda=_as_number(data, "freshness_lambda"),
        duplicate_window_hours=_as_number(data, "duplicate_window_hours"),
        duplicate_jaccard=_as_number(data, "duplicate_jaccard"),
        max_age_hours=_as_number(data, "max_age_hours"),
        severe_importance=_as_number(data, "severe_importance"),
        severe_freshness=_as_number(data, "severe_freshness"),
        raw_low=_as_number(data, "raw_low"),
        raw_high=_as_number(data, "raw_high"),
    )
    if cfg.freshness_lambda <= 0 or cfg.duplicate_window_hours <= 0 or cfg.max_age_hours <= 0:
        raise ConfigError("event windows and lambda must be positive")
    if not 0 <= cfg.duplicate_jaccard <= 1:
        raise ConfigError("event duplicate_jaccard must be between 0 and 1")
    if not 0 <= cfg.severe_importance <= 1 or not 0 <= cfg.severe_freshness <= 1:
        raise ConfigError("event severe thresholds must be between 0 and 1")
    if cfg.raw_low >= cfg.raw_high:
        raise ConfigError("event raw_low must be below raw_high")
    return cfg


def _relative_strength(data: dict[str, Any], root: Path) -> RelativeStrengthConfig:
    cfg = RelativeStrengthConfig(
        market_weight=_as_number(data, "market_weight"),
        sector_weight=_as_number(data, "sector_weight"),
        lookback_sessions=_positive_int(data, "lookback_sessions"),
        raw_low=_as_number(data, "raw_low"),
        raw_high=_as_number(data, "raw_high"),
        sector_map=_as_str(data, "sector_map"),
    )
    if abs((cfg.market_weight + cfg.sector_weight) - 1) > _WEIGHT_TOLERANCE:
        raise ConfigError("relative strength weights must sum to 1")
    if cfg.raw_low >= cfg.raw_high:
        raise ConfigError("relative strength raw_low must be below raw_high")
    _require_file(root / cfg.sector_map)
    _validate_sector_map(root / cfg.sector_map)
    return cfg


def _price_action(data: dict[str, Any], weight: float) -> PriceActionConfig:
    cfg = PriceActionConfig(
        gap_penalty_start=_as_number(data, "gap_penalty_start"),
        gap_hard_penalty=_as_number(data, "gap_hard_penalty"),
        gap_penalty_floor=_as_number(data, "gap_penalty_floor"),
        atr_lookback=_positive_int(data, "atr_lookback"),
        atr_elevated=_as_number(data, "atr_elevated"),
        gap_component_max=_as_number(data, "gap_component_max"),
        break_bonus=_as_number(data, "break_bonus"),
        elevated_atr_scale=_as_number(data, "elevated_atr_scale"),
    )
    if not 0 < cfg.gap_penalty_start < cfg.gap_hard_penalty:
        raise ConfigError("price_action gap thresholds must be increasing and positive")
    if not 0 <= cfg.gap_penalty_floor < 1:
        raise ConfigError("price_action gap_penalty_floor must be in [0, 1)")
    if cfg.atr_elevated <= 0 or not 0 < cfg.elevated_atr_scale <= 1:
        raise ConfigError("price_action ATR settings are out of range")
    parts = cfg.gap_component_max + 2 * cfg.break_bonus
    if abs(parts - weight) > _WEIGHT_TOLERANCE:
        raise ConfigError(
            f"price_action components sum to {parts}, which must equal the price_action weight"
        )
    return cfg


def _regime(data: dict[str, Any]) -> RegimeConfig:
    cfg = RegimeConfig(
        extreme_spy_5d=_as_number(data, "extreme_spy_5d"),
        risk_off_spy_5d=_as_number(data, "risk_off_spy_5d"),
        risk_off_qqq_5d=_as_number(data, "risk_off_qqq_5d"),
        risk_off_vix=_as_number(data, "risk_off_vix"),
        extreme_vix=_as_number(data, "extreme_vix"),
        no_trade_on=_as_str(data, "no_trade_on"),
    )
    if not cfg.extreme_spy_5d < cfg.risk_off_spy_5d < 0:
        raise ConfigError("regime SPY thresholds must satisfy extreme < risk_off < 0")
    if cfg.risk_off_qqq_5d >= 0:
        raise ConfigError("regime QQQ risk-off threshold must be negative")
    if not cfg.extreme_vix > cfg.risk_off_vix > 0:
        raise ConfigError("regime VIX thresholds must satisfy extreme > risk_off > 0")
    if cfg.no_trade_on not in {"EXTREME_RISK_OFF", "RISK_OFF"}:
        raise ConfigError("regime no_trade_on must be EXTREME_RISK_OFF or RISK_OFF")
    return cfg


def _benchmarks(data: dict[str, Any]) -> BenchmarkConfig:
    cfg = BenchmarkConfig(
        market=_as_str(data, "market"),
        growth=_as_str(data, "growth"),
        volatility=_as_str(data, "volatility"),
    )
    for name in (cfg.market, cfg.growth, cfg.volatility):
        if not name.isupper():
            raise ConfigError(f"benchmark symbol must be uppercase: {name}")
    return cfg


def _providers(data: dict[str, Any]) -> ProviderConfig:
    market = _choice(data, "market", {"fixture", "alpaca"})
    premarket = _choice(data, "premarket", {"fixture", "alpaca"})
    news = _choice(data, "news", {"fixture", "alpaca"})
    universe = _choice(data, "universe", {"fixture", "file"})
    calendar = _choice(data, "calendar", {"nyse"})
    return ProviderConfig(
        market=market,
        premarket=premarket,
        news=news,
        universe=universe,
        calendar=calendar,
    )


def _alpaca(data: dict[str, Any]) -> AlpacaConfig:
    feed = _choice(data, "feed", {"iex", "sip"})
    adjustment = _choice(data, "adjustment", {"raw"})
    url = _as_str(data, "data_base_url")
    if not url.startswith("https://"):
        raise ConfigError("alpaca data_base_url must start with https://")
    return AlpacaConfig(feed=feed, adjustment=adjustment, data_base_url=url)


def _calendar(data: dict[str, Any]) -> CalendarConfig:
    return CalendarConfig(
        extra_holidays=_dates(data, "extra_holidays"),
        extra_early_closes=_dates(data, "extra_early_closes"),
    )


def _evaluation(data: dict[str, Any]) -> EvaluationConfig:
    horizons_raw = data.get("horizons")
    if horizons_raw != [1, 2, 3, 5]:
        raise ConfigError("evaluation horizons are fixed at [1, 2, 3, 5]")
    buckets_raw = data.get("buckets")
    if not isinstance(buckets_raw, list) or not buckets_raw:
        raise ConfigError("evaluation buckets must be a non-empty list")
    buckets: list[tuple[float, float]] = []
    for pair in buckets_raw:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ConfigError("each evaluation bucket must be [low, high]")
        low = float(pair[0])
        high = float(pair[1])
        if low >= high:
            raise ConfigError("evaluation bucket low must be below high")
        buckets.append((low, high))
    return EvaluationConfig(
        horizons=_FIXED_HORIZONS,
        min_bucket_count=_positive_int(data, "min_bucket_count"),
        buckets=tuple(buckets),
    )


def _fixtures(data: dict[str, Any], root: Path) -> FixtureConfig:
    cfg = FixtureConfig(
        market=_as_str(data, "market"),
        premarket=_as_str(data, "premarket"),
        news=_as_str(data, "news"),
        universe=_as_str(data, "universe"),
    )
    for relative in (cfg.market, cfg.premarket, cfg.news, cfg.universe):
        _require_file(root / relative)
    return cfg


def _universe_config(data: dict[str, Any], root: Path) -> UniverseConfig:
    filters = _section(data, "filters", {"min_price", "min_avg_dollar_volume_20d"})
    min_price = _as_number(filters, "min_price")
    min_dollar = _as_number(filters, "min_avg_dollar_volume_20d")
    if min_price < 0 or min_dollar < 0:
        raise ConfigError("universe filters must be >= 0")
    if data.get("point_in_time_membership") is not False:
        raise ConfigError("point_in_time_membership must be false for the V0.1 static universe")
    files = _section(data, "files", {"sp500", "nasdaq100"})
    as_of = _section(data, "as_of", {"sp500", "nasdaq100"})
    file_cfg = UniverseFiles(
        sp500=_as_str(files, "sp500"),
        nasdaq100=_as_str(files, "nasdaq100"),
    )
    for relative in (file_cfg.sp500, file_cfg.nasdaq100):
        _require_file(root / relative)
    return UniverseConfig(
        sources=_sources(data),
        filters=UniverseFilters(min_price=min_price, min_avg_dollar_volume_20d=min_dollar),
        files=file_cfg,
        as_of=UniverseAsOf(
            sp500=_as_date(as_of, "sp500"),
            nasdaq100=_as_date(as_of, "nasdaq100"),
        ),
        point_in_time_membership=False,
    )


def _sources(data: dict[str, Any]) -> tuple[str, ...]:
    raw = data.get("sources")
    if not isinstance(raw, list) or not raw:
        raise ConfigError("universe sources must be a non-empty list")
    allowed = {"sp500", "nasdaq100"}
    if any(not isinstance(item, str) or item not in allowed for item in raw):
        raise ConfigError("universe sources must be sp500 and/or nasdaq100")
    if len(set(raw)) != len(raw):
        raise ConfigError("universe sources must be unique")
    return tuple(raw)


def _validate_sector_map(path: Path) -> None:
    loaded = yaml.safe_load(path.read_text())
    if not isinstance(loaded, dict) or set(loaded) != {"symbols"}:
        raise ConfigError(f"sector map must contain only symbols: {path}")
    symbols = loaded["symbols"]
    if not isinstance(symbols, dict) or not symbols:
        raise ConfigError(f"sector map symbols are missing: {path}")
    for symbol, etf in symbols.items():
        if not isinstance(symbol, str) or not isinstance(etf, str):
            raise ConfigError(f"sector map entries must be strings: {path}")
        if symbol != symbol.upper() or etf != etf.upper():
            raise ConfigError(f"sector map tickers must be uppercase: {symbol} {etf}")


def _dates(data: dict[str, Any], key: str) -> tuple[date, ...]:
    raw = data.get(key)
    if not isinstance(raw, list):
        raise ConfigError(f"{key} must be a list")
    days: list[date] = []
    for item in raw:
        if not isinstance(item, str):
            raise ConfigError(f"{key} entries must be YYYY-MM-DD strings")
        try:
            days.append(date.fromisoformat(item))
        except ValueError as exc:
            raise ConfigError(f"invalid date in {key}: {item}") from exc
    return tuple(days)


def _require_file(path: Path) -> None:
    if not path.is_file():
        raise ConfigError(f"missing file: {path}")


def _section(data: dict[str, Any], key: str, allowed: set[str]) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"{key} must be a mapping")
    _exact_keys(value, allowed, key)
    return value


def _exact_keys(data: dict[str, Any], allowed: set[str], label: str) -> None:
    missing = allowed - set(data)
    extra = set(data) - allowed
    if missing or extra:
        raise ConfigError(f"{label} keys mismatch missing={sorted(missing)} extra={sorted(extra)}")


def _as_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{key} must be a non-empty string")
    return value


def _as_bool(data: dict[str, Any], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be a boolean")
    return value


def _as_number(data: dict[str, Any], key: str) -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{key} must be a number")
    return float(value)


def _positive_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{key} must be a positive integer")
    return value


def _as_date(data: dict[str, Any], key: str) -> date:
    raw = _as_str(data, key)
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ConfigError(f"invalid date for {key}: {raw}") from exc


def _as_clock(data: dict[str, Any], key: str) -> time:
    raw = _as_str(data, key)
    parts = raw.split(":")
    if len(parts) != 2:
        raise ConfigError(f"{key} must be HH:MM")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        return time(hour, minute)
    except ValueError as exc:
        raise ConfigError(f"{key} must be HH:MM") from exc


def _choice(data: dict[str, Any], key: str, allowed: set[str]) -> str:
    value = _as_str(data, key)
    if value not in allowed:
        raise ConfigError(f"{key} must be one of {sorted(allowed)}")
    return value


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, date | time):
        return value.isoformat()
    return value
