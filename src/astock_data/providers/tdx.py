"""Optional mootdx-backed market-data provider."""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

from ..models import DataStatus, ProviderResult, SourceMetadata
from ..tickers import normalize_ticker

DEFAULT_TDX_SERVERS = (
    ("119.97.185.59", 7709),
    ("124.70.133.119", 7709),
    ("116.205.183.150", 7709),
    ("123.60.73.44", 7709),
    ("116.205.163.254", 7709),
    ("121.36.225.169", 7709),
    ("123.60.70.228", 7709),
    ("124.71.9.153", 7709),
    ("110.41.147.114", 7709),
    ("124.71.187.122", 7709),
)


class TdxProvider:
    provider_name = "mootdx"
    schema_version = "v1"

    def __init__(
        self,
        *,
        quotes_factory: Callable[..., Any] | None = None,
        socket_probe: Callable[[str, int], bool] | None = None,
        servers: Iterable[tuple[str, int]] = DEFAULT_TDX_SERVERS,
    ):
        self._quotes_factory = quotes_factory
        self._socket_probe = socket_probe or _probe
        self._servers = tuple(servers)
        self._clients: dict[str, Any] = {}

    def get_bars(
        self,
        *,
        symbol: str,
        frequency: int = 9,
        offset: int = 800,
        market: str = "std",
    ) -> ProviderResult[list[dict]]:
        safe_symbol = _validate_symbol(symbol, market)
        if isinstance(frequency, bool) or not isinstance(frequency, int):
            raise ValueError("frequency must be an integer")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 1:
            raise ValueError("offset must be a positive integer")
        return self._call(
            "tdx_bars",
            market,
            lambda client: client.bars(symbol=safe_symbol, frequency=frequency, offset=offset),
        )

    def get_quote(self, *, symbol: str, market: str = "std") -> ProviderResult[list[dict]]:
        safe_symbol = _validate_symbol(symbol, market)
        return self._call("tdx_quote", market, lambda client: client.quotes(symbol=safe_symbol))

    def get_transactions(
        self,
        *,
        symbol: str,
        date: str,
        market: str = "std",
    ) -> ProviderResult[list[dict]]:
        safe_symbol = _validate_symbol(symbol, market)
        date_text = str(date)
        if len(date_text) != 8 or not date_text.isdigit():
            raise ValueError("trade_date must use YYYYMMDD")
        try:
            datetime.strptime(date_text, "%Y%m%d")
        except ValueError as exc:
            raise ValueError("trade_date must use YYYYMMDD") from exc
        return self._call(
            "tdx_transactions",
            market,
            lambda client: client.transaction(symbol=safe_symbol, date=date_text),
            trade_date=f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:]}",
        )

    def _call(
        self,
        capability: str,
        market: str,
        operation: Callable[[Any], Any],
        *,
        trade_date: str | None = None,
    ) -> ProviderResult[list[dict]]:
        endpoint = f"tdx://{market}"
        try:
            rows = _records(operation(self._client(market)))
            return ProviderResult(
                data=rows,
                meta=SourceMetadata(
                    provider=self.provider_name,
                    capability=capability,
                    endpoint=endpoint,
                    status=DataStatus.OK if rows else DataStatus.EMPTY,
                    trade_date=trade_date,
                    schema_version=self.schema_version,
                ),
                coverage={"coverage_ratio": 1.0 if rows else 0.0, "returned_count": len(rows), "market": market},
            )
        except Exception as exc:
            warning = f"{type(exc).__name__}: {exc}"
            return ProviderResult(
                data=[],
                meta=SourceMetadata(
                    provider=self.provider_name,
                    capability=capability,
                    endpoint=endpoint,
                    status=DataStatus.UNAVAILABLE,
                    trade_date=trade_date,
                    warnings=[warning],
                    schema_version=self.schema_version,
                ),
                coverage={"coverage_ratio": 0.0, "returned_count": 0, "market": market, "warnings": [warning]},
            )

    def _client(self, market: str) -> Any:
        if market not in self._clients:
            self._clients[market] = self._create_client(market)
        return self._clients[market]

    def _create_client(self, market: str) -> Any:
        factory = self._factory()
        last_error: Exception | None = None
        for ip, port in self._servers:
            if not self._socket_probe(ip, port):
                continue
            try:
                client = factory(market=market, server=(ip, port))
                if _validate_client(client, market):
                    return client
            except Exception as exc:
                last_error = exc
        for kwargs in ({"bestip": True}, {}):
            try:
                client = factory(market=market, **kwargs)
                if _validate_client(client, market):
                    return client
            except Exception as exc:
                last_error = exc
        detail = f": {last_error}" if last_error else ""
        raise RuntimeError(f"所有 mootdx 服务器均无法取到数据{detail}")

    def _factory(self) -> Callable[..., Any]:
        if self._quotes_factory is not None:
            return self._quotes_factory
        from mootdx.quotes import Quotes

        return Quotes.factory


def _probe(ip: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _validate_client(client: Any, market: str) -> bool:
    if market != "std":
        return True
    try:
        return bool(_records(client.bars(symbol="000001", frequency=9, offset=1)))
    except Exception:
        return False


def _records(value: Any) -> list[dict]:
    if value is None:
        return []
    if hasattr(value, "to_dict"):
        converted = value.to_dict("records")
    elif isinstance(value, list):
        converted = value
    elif isinstance(value, dict):
        converted = [value]
    else:
        return []
    return [dict(row) for row in converted if isinstance(row, dict)]


def _validate_symbol(symbol: str, market: str) -> str:
    if not isinstance(market, str) or not market.strip():
        raise ValueError("market must be a non-empty string")
    if market == "std":
        return normalize_ticker(symbol, stock_only=True)
    text = str(symbol).strip()
    if not text:
        raise ValueError("symbol must not be empty")
    return text
