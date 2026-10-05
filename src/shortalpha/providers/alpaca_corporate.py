"""Alpaca split ratios.

process_date is not a public announcement clock. A split is applied only once
its ex_date is on or before the signal session, because that is when the ratio
is already in the traded price. The run note records that announcement time is
unavailable.
"""

import os
from datetime import date, datetime

import httpx

from shortalpha.domain import Split
from shortalpha.errors import ConfigError
from shortalpha.logging_utils import raise_unavailable

_TYPES = "forward_split,reverse_split,unit_split,stock_dividend"
_GROUPS = (
    "forward_splits",
    "reverse_splits",
    "unit_splits",
    "stock_dividends",
    "forward_split",
    "reverse_split",
    "unit_split",
    "stock_dividend",
)


class AlpacaCorporateActionsProvider:
    name = "alpaca"

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str,
        client: httpx.Client,
        owns_client: bool,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._owns_client = owns_client

    @classmethod
    def from_env(
        cls,
        *,
        base_url: str,
        client: httpx.Client | None = None,
    ) -> "AlpacaCorporateActionsProvider":
        api_key = os.environ.get("APCA_API_KEY_ID", "")
        api_secret = os.environ.get("APCA_API_SECRET_KEY", "")
        if not api_key or not api_secret:
            raise ConfigError("APCA_API_KEY_ID and APCA_API_SECRET_KEY are required")
        owns_client = client is None
        if client is None:
            client = httpx.Client(timeout=30.0)
        return cls(
            api_key=api_key,
            api_secret=api_secret,
            base_url=base_url,
            client=client,
            owns_client=owns_client,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def splits(
        self,
        symbol: str,
        start: date,
        end: date,
        as_of: datetime,
    ) -> tuple[Split, ...]:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        known_through = min(end, as_of.date())
        if start > known_through:
            return ()
        try:
            payload = self._fetch(symbol, start, known_through, as_of)
            return splits_from_payload(
                payload, symbol=symbol, start=start, end=known_through, as_of=as_of
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise_unavailable(
                symbol=symbol,
                provider=self.name,
                operation="corporate_actions",
                timestamp=as_of,
                reason=str(exc),
            )

    def _fetch(self, symbol: str, start: date, end: date, as_of: datetime) -> dict[str, object]:
        rows: list[dict[str, object]] = []
        page_token: str | None = None
        while True:
            params = {
                "symbols": symbol,
                "types": _TYPES,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "limit": "1000",
            }
            if page_token:
                params["page_token"] = page_token
            try:
                response = self._client.get(
                    f"{self._base_url}/v1/corporate-actions",
                    params=params,
                    headers={
                        "APCA-API-KEY-ID": self._api_key,
                        "APCA-API-SECRET-KEY": self._api_secret,
                    },
                )
            except httpx.HTTPError as exc:
                raise_unavailable(
                    symbol=symbol,
                    provider=self.name,
                    operation="corporate_actions",
                    timestamp=as_of,
                    reason=exc.__class__.__name__,
                )
            if response.status_code >= 400:
                raise_unavailable(
                    symbol=symbol,
                    provider=self.name,
                    operation="corporate_actions",
                    timestamp=as_of,
                    reason=f"HTTP {response.status_code}",
                )
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("corporate actions response was not an object")
            page = body.get("corporate_actions") or {}
            if not isinstance(page, dict):
                raise ValueError("corporate actions payload was not an object")
            merged = _merge(rows, page)
            rows = merged
            token = body.get("next_page_token")
            if not token:
                return {"corporate_actions": _grouped(rows)}
            page_token = str(token)


def splits_from_payload(
    payload: dict[str, object],
    *,
    symbol: str,
    start: date,
    end: date,
    as_of: datetime,
) -> tuple[Split, ...]:
    actions = payload.get("corporate_actions") or {}
    if not isinstance(actions, dict):
        raise ValueError("corporate actions payload was not an object")
    found: list[Split] = []
    for name in _GROUPS:
        rows = actions.get(name) or []
        if not isinstance(rows, list):
            raise ValueError(f"{name} was not a list")
        for raw in rows:
            if not isinstance(raw, dict):
                raise ValueError("corporate action row was not an object")
            row_symbol = str(raw.get("symbol") or symbol)
            if row_symbol != symbol:
                continue
            ex_date = date.fromisoformat(str(raw["ex_date"]))
            if ex_date < start or ex_date > end or ex_date > as_of.date():
                continue
            old_rate, new_rate = _rates(raw)
            found.append(Split(row_symbol, ex_date, old_rate, new_rate))
    found.sort(key=lambda split: (split.ex_date, split.new_rate))
    return tuple(found)


def _rates(raw: dict[str, object]) -> tuple[float, float]:
    if "old_rate" in raw and "new_rate" in raw:
        old_rate = float(raw["old_rate"])
        new_rate = float(raw["new_rate"])
    elif "rate" in raw:
        rate = float(raw["rate"])
        if rate <= 0:
            raise ValueError("stock dividend rate must be positive")
        # Alpaca's stock-dividend rate is additional shares per share held.
        old_rate, new_rate = 1.0, 1.0 + rate
    else:
        raise ValueError("corporate action is missing a ratio")
    if old_rate <= 0 or new_rate <= 0:
        raise ValueError("split rates must be positive")
    return old_rate, new_rate


def _merge(rows: list[dict[str, object]], page: dict[str, object]) -> list[dict[str, object]]:
    merged = list(rows)
    for name in _GROUPS:
        group = page.get(name) or []
        if not isinstance(group, list):
            raise ValueError(f"{name} was not a list")
        for raw in group:
            if isinstance(raw, dict):
                merged.append(raw)
    return merged


def _grouped(rows: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    return {"forward_splits": rows}
