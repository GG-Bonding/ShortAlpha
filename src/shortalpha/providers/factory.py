"""Build the providers named in config. Callers do not import a vendor SDK."""

from pathlib import Path

from shortalpha.calendar import NYSECalendar
from shortalpha.config import AppConfig
from shortalpha.errors import ConfigError
from shortalpha.providers.alpaca_market import AlpacaMarketDataProvider
from shortalpha.providers.alpaca_premarket import AlpacaPremarketProvider
from shortalpha.providers.fixture_market import FixtureMarketDataProvider
from shortalpha.providers.fixture_premarket import FixturePreMarketProvider
from shortalpha.providers.fixture_universe import FixtureUniverseProvider
from shortalpha.universe import FileUniverseProvider, load_membership


def build_market_provider(cfg: AppConfig, root: Path, calendar: NYSECalendar):
    if cfg.providers.market == "fixture":
        return FixtureMarketDataProvider.from_json(_resolve(root, cfg.fixtures.market))
    if cfg.providers.market == "alpaca":
        return AlpacaMarketDataProvider.from_env(
            feed=cfg.alpaca.feed,
            base_url=cfg.alpaca.data_base_url,
            calendar=calendar,
        )
    raise ConfigError(f"unsupported market provider: {cfg.providers.market}")


def build_premarket_provider(cfg: AppConfig, root: Path):
    if cfg.providers.premarket == "fixture":
        return FixturePreMarketProvider.from_json(_resolve(root, cfg.fixtures.premarket))
    if cfg.providers.premarket == "alpaca":
        return AlpacaPremarketProvider.from_env(
            feed=cfg.alpaca.feed,
            base_url=cfg.alpaca.data_base_url,
            signal_clock=cfg.signal.time,
            premarket_start=cfg.premarket.start,
            timezone=cfg.signal.timezone,
        )
    raise ConfigError(f"unsupported premarket provider: {cfg.providers.premarket}")


def build_universe_provider(cfg: AppConfig, root: Path):
    if cfg.providers.universe == "fixture":
        return FixtureUniverseProvider.from_json(_resolve(root, cfg.fixtures.universe))
    if cfg.providers.universe == "file":
        files = {
            "sp500": _resolve(root, cfg.universe.files.sp500),
            "nasdaq100": _resolve(root, cfg.universe.files.nasdaq100),
        }
        as_of = {
            "sp500": cfg.universe.as_of.sp500,
            "nasdaq100": cfg.universe.as_of.nasdaq100,
        }
        return FileUniverseProvider(
            load_membership(sources=cfg.universe.sources, files=files, as_of=as_of)
        )
    raise ConfigError(f"unsupported universe provider: {cfg.providers.universe}")


def _resolve(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute():
        return path
    return root / path
