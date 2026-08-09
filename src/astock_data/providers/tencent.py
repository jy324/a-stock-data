"""Tencent Finance real-time quote provider."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from urllib.request import Request, urlopen

from ..models import DataStatus, ProviderResult, SourceMetadata
from ..tickers import get_market_prefix, normalize_ticker

TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


class TencentProvider:
    provider_name = "tencent"
    schema_version = "v1"

    def __init__(self, *, timeout: float = 10.0):
        self.timeout = timeout

    def get_realtime_quotes(self, *, codes: Iterable[str]) -> ProviderResult[list[dict]]:
        requested = [str(code).strip() for code in codes]
        if not requested:
            raise ValueError("codes must contain at least one ticker")

        prefixed = []
        requested_by_query: dict[str, str] = {}
        for raw in requested:
            digits = normalize_ticker(raw)
            query_code = f"{get_market_prefix(raw)}{digits}"
            prefixed.append(query_code)
            requested_by_query[query_code] = raw

        endpoint = TENCENT_QUOTE_URL + ",".join(prefixed)
        coverage: dict[str, Any] = {
            "requested_count": len(requested),
            "returned_count": 0,
            "missing_codes": [],
        }
        try:
            request = Request(endpoint, headers={"User-Agent": UA})
            with urlopen(request, timeout=self.timeout) as response:
                payload = response.read().decode("gbk", errors="replace")
            rows = []
            returned_requested = set()
            for line in payload.strip().split(";"):
                if not line.strip() or "=" not in line or '"' not in line:
                    continue
                query_code = line.split("=", 1)[0].rsplit("_", 1)[-1]
                values = line.split('"', 2)[1].split("~")
                if len(values) < 53:
                    continue
                requested_code = requested_by_query.get(query_code)
                if requested_code is None:
                    continue
                returned_requested.add(requested_code)
                rows.append(_quote_row(requested_code, query_code, values))

            missing = [code for code in requested if code not in returned_requested]
            coverage["returned_count"] = len(rows)
            coverage["missing_codes"] = missing
            warnings = [f"Tencent quote missing requested codes: {', '.join(missing)}"] if missing else []
            stale_count = sum(1 for row in rows if row["is_stale"])
            if rows and stale_count == len(rows) and not missing:
                status = DataStatus.STALE
            elif missing or stale_count:
                status = DataStatus.PARTIAL
            else:
                status = DataStatus.OK if rows else DataStatus.EMPTY
            return ProviderResult(
                data=rows,
                meta=SourceMetadata(
                    provider=self.provider_name,
                    capability="realtime_quotes",
                    endpoint=endpoint,
                    status=status,
                    unit_map={
                        "price": "CNY",
                        "turnover_amount": "CNY",
                        "float_market_cap": "CNY",
                        "market_cap": "CNY",
                    },
                    warnings=warnings,
                    schema_version=self.schema_version,
                ),
                coverage={"coverage_ratio": len(rows) / len(requested), **coverage, "warnings": warnings},
            )
        except Exception as exc:
            warning = f"{type(exc).__name__}: {exc}"
            return ProviderResult(
                data=[],
                meta=SourceMetadata(
                    provider=self.provider_name,
                    capability="realtime_quotes",
                    endpoint=endpoint,
                    status=DataStatus.UNAVAILABLE,
                    warnings=[warning],
                    schema_version=self.schema_version,
                ),
                coverage={"coverage_ratio": 0.0, **coverage, "warnings": [warning]},
            )


def _quote_row(requested_code: str, query_code: str, values: list[str]) -> dict[str, Any]:
    price = _float(values[3])
    previous_close = _float(values[4])
    turnover_wan = _float(values[37])
    is_stale = turnover_wan == 0 and price == previous_close and price > 0
    stale_reason = None
    digits = query_code[2:]
    if is_stale and digits[:2] in {"43", "83", "87"}:
        stale_reason = "北交所老号段，多数已迁至 920xxx，请按名称反查现行代码"
    elif is_stale:
        stale_reason = "成交额为 0（停牌 / 未开盘 / 废码），报价可能不是当日真实成交"
    return {
        "requested_code": requested_code,
        "code": digits,
        "market": query_code[:2].upper(),
        "name": values[1],
        "price": price,
        "previous_close": previous_close,
        "open": _float(values[5]),
        "change_amount": _float(values[31]),
        "change_pct": _float(values[32]),
        "high": _float(values[33]),
        "low": _float(values[34]),
        "turnover_amount": _cny(turnover_wan * 10_000),
        "turnover_pct": _float(values[38]),
        "pe_ttm": _float(values[39]),
        "amplitude_pct": _float(values[43]),
        "float_market_cap": _cny(_float(values[44]) * 100_000_000),
        "market_cap": _cny(_float(values[45]) * 100_000_000),
        "pb": _float(values[46]),
        "limit_up": _float(values[47]),
        "limit_down": _float(values[48]),
        "volume_ratio": _float(values[49]),
        "pe_static": _float(values[52]),
        "is_stale": is_stale,
        "stale_reason": stale_reason,
    }


def _float(value: Any) -> float:
    try:
        return float(value) if value not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _cny(amount: float) -> dict[str, float | str]:
    return {"amount": amount, "unit": "CNY"}
