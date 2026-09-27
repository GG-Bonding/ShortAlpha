"""Failure logs. Every operational miss carries the same five fields."""

import json
import logging
from datetime import datetime
from typing import NoReturn

from shortalpha.errors import DataUnavailableError

LOGGER = logging.getLogger("shortalpha")


def log_failure(
    *,
    symbol: str,
    provider: str,
    operation: str,
    timestamp: datetime,
    reason: str,
) -> None:
    payload = {
        "symbol": symbol,
        "provider": provider,
        "operation": operation,
        "timestamp": timestamp.isoformat(),
        "reason": reason,
    }
    LOGGER.error("%s", json.dumps(payload, sort_keys=True))


def raise_unavailable(
    *,
    symbol: str,
    provider: str,
    operation: str,
    timestamp: datetime,
    reason: str,
) -> NoReturn:
    log_failure(
        symbol=symbol,
        provider=provider,
        operation=operation,
        timestamp=timestamp,
        reason=reason,
    )
    raise DataUnavailableError(
        symbol=symbol,
        provider=provider,
        operation=operation,
        timestamp=timestamp,
        reason=reason,
    )
