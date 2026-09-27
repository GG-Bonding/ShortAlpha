"""Project errors. A missing input is an error, never a silent zero."""

from datetime import datetime


class ShortAlphaError(Exception):
    """Base error for expected operational failures."""


class ConfigError(ShortAlphaError):
    """Configuration is missing, inconsistent, or points at a missing file."""


class FixtureError(ShortAlphaError):
    """A fixture file is malformed."""


class DataUnavailableError(ShortAlphaError):
    """A provider could not supply data. This is not a factor score."""

    def __init__(
        self,
        *,
        symbol: str,
        provider: str,
        operation: str,
        timestamp: datetime,
        reason: str,
    ) -> None:
        self.symbol = symbol
        self.provider = provider
        self.operation = operation
        self.timestamp = timestamp
        self.reason = reason
        super().__init__(
            f"{provider}.{operation} failed for {symbol} at {timestamp.isoformat()}: {reason}"
        )
