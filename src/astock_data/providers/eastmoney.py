"""Eastmoney HTTP provider for the public AStockDataClient facade."""

from __future__ import annotations

import json
import random
import threading
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..models import DataStatus, ProviderResult, SourceMetadata

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
BKZJ_URL = "https://data.eastmoney.com/dataapi/bkzj/getbkzj"


class EastmoneyProvider:
    """Minimal built-in provider for DSA's A-share intelligence capabilities."""

    provider_name = "eastmoney"
    schema_version = "v1"

    def __init__(self, *, timeout: float = 15.0, min_interval: float = 1.0):
        self.timeout = timeout
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last_call = 0.0

    def get_stock_intraday_flow(self, *, code: str, trade_date: str | None = None) -> ProviderResult[list[dict]]:
        endpoint = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
        try:
            payload = self._get_json(
                endpoint,
                {
                    "secid": _secid(code),
                    "klt": "1",
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57",
                },
                referer="https://quote.eastmoney.com/",
                timeout=10,
            )
            rows = [
                _flow_row(parts, date_key="time")
                for parts in (_split_kline(line) for line in _klines(payload))
                if len(parts) >= 6
            ]
            return self._result(
                "stock_intraday_flow",
                endpoint,
                rows,
                trade_date=trade_date,
                unit_map=_flow_unit_map(),
            )
        except Exception as exc:
            return self._unavailable("stock_intraday_flow", endpoint, trade_date, exc)

    def get_stock_flow_history(
        self,
        *,
        code: str,
        trade_date: str | None = None,
        lookback: int = 120,
    ) -> ProviderResult[list[dict]]:
        endpoint = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
        safe_lookback = max(1, min(int(lookback or 120), 120))
        try:
            payload = self._get_json(
                endpoint,
                {
                    "secid": _secid(code),
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
                    "lmt": str(safe_lookback),
                },
                referer="https://quote.eastmoney.com/",
            )
            normalized_code = _normalize_code(code)
            rows = []
            for parts in (_split_kline(line) for line in _klines(payload)):
                if len(parts) < 6:
                    continue
                row = _flow_row(parts, date_key="trade_date")
                row["code"] = normalized_code
                rows.append(row)
            return self._result(
                "stock_flow_history",
                endpoint,
                rows,
                trade_date=trade_date,
                unit_map=_flow_unit_map(),
                coverage={"requested_lookback": safe_lookback, "returned_count": len(rows)},
            )
        except Exception as exc:
            return self._unavailable("stock_flow_history", endpoint, trade_date, exc)

    def get_sector_flow_ranking(
        self,
        *,
        trade_date: str | None = None,
        limit: int = 10,
    ) -> ProviderResult[list[dict]]:
        endpoint = BKZJ_URL
        safe_limit = max(1, min(int(limit or 10), 50))
        try:
            payload = self._get_json(
                endpoint,
                {
                    "key": "f62",
                    "code": "m:90 s:4",
                },
                referer="https://data.eastmoney.com/bkzj/hy.html",
            )
            items = _diff(payload)
            rows = []
            for index, item in enumerate(items[:safe_limit], start=1):
                rows.append(_sector_flow_row(index, item))
            return self._result(
                "sector_flow_ranking",
                endpoint,
                rows,
                trade_date=trade_date,
                coverage={"requested_limit": safe_limit, "returned_count": len(rows)},
            )
        except Exception as exc:
            return self._unavailable("sector_flow_ranking", endpoint, trade_date, exc)

    def get_market_dragon_tiger(
        self,
        *,
        trade_date: str | None = None,
        limit: int | None = None,
    ) -> ProviderResult[list[dict]]:
        endpoint = DATACENTER_URL
        query_date = trade_date or datetime.now().strftime("%Y-%m-%d")
        try:
            rows = self._datacenter(
                "RPT_DAILYBILLBOARD_DETAILSNEW",
                filter_str=f"(TRADE_DATE>='{query_date}')(TRADE_DATE<='{query_date}')",
                page_size=max(1, min(int(limit or 500), 500)),
                sort_columns="BILLBOARD_NET_AMT",
                sort_types="-1",
            )
            data = [_dragon_tiger_row(row) for row in rows]
            return self._result(
                "market_dragon_tiger",
                endpoint,
                data,
                trade_date=query_date,
                coverage={"returned_count": len(data)},
            )
        except Exception as exc:
            return self._unavailable("market_dragon_tiger", endpoint, query_date, exc)

    def get_stock_dragon_tiger(self, *, code: str, trade_date: str | None = None) -> ProviderResult[list[dict]]:
        endpoint = DATACENTER_URL
        query_date = trade_date or datetime.now().strftime("%Y-%m-%d")
        start = (datetime.strptime(query_date, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
        normalized_code = _normalize_code(code)
        try:
            rows = self._datacenter(
                "RPT_DAILYBILLBOARD_DETAILSNEW",
                filter_str=(
                    f"(TRADE_DATE>='{start}')(TRADE_DATE<='{query_date}')"
                    f"(SECURITY_CODE=\"{normalized_code}\")"
                ),
                page_size=50,
                sort_columns="TRADE_DATE",
                sort_types="-1",
            )
            data = [_dragon_tiger_row(row) for row in rows]
            return self._result(
                "stock_dragon_tiger",
                endpoint,
                data,
                trade_date=query_date,
                coverage={"returned_count": len(data), "filtered_code": normalized_code},
            )
        except Exception as exc:
            return self._unavailable("stock_dragon_tiger", endpoint, query_date, exc)

    def get_lockup_events(
        self,
        *,
        code: str,
        trade_date: str | None = None,
        forward_days: int = 90,
        limit: int | None = None,
    ) -> ProviderResult[list[dict]]:
        endpoint = DATACENTER_URL
        query_date = trade_date or datetime.now().strftime("%Y-%m-%d")
        normalized_code = _normalize_code(code)
        safe_limit = max(1, min(int(limit or 100), 500))
        end_date = (datetime.strptime(query_date, "%Y-%m-%d") + timedelta(days=max(1, int(forward_days or 90)))).strftime(
            "%Y-%m-%d"
        )
        try:
            rows = self._datacenter(
                "RPT_LIFT_STAGE",
                filter_str=(
                    f"(SECURITY_CODE=\"{normalized_code}\")"
                    f"(FREE_DATE>='{query_date}')(FREE_DATE<='{end_date}')"
                ),
                page_size=safe_limit,
                sort_columns="FREE_DATE",
                sort_types="1",
            )
            data = [_lockup_row(row) for row in rows]
            return self._result(
                "lockup_events",
                endpoint,
                data,
                trade_date=query_date,
                coverage={"returned_count": len(data), "filtered_code": normalized_code},
            )
        except Exception as exc:
            return self._unavailable("lockup_events", endpoint, query_date, exc)

    def _datacenter(
        self,
        report_name: str,
        *,
        filter_str: str = "",
        page_size: int = 50,
        sort_columns: str = "",
        sort_types: str = "-1",
    ) -> list[dict]:
        payload = self._get_json(
            DATACENTER_URL,
            {
                "reportName": report_name,
                "columns": "ALL",
                "filter": filter_str,
                "pageNumber": "1",
                "pageSize": str(page_size),
                "sortColumns": sort_columns,
                "sortTypes": sort_types,
                "source": "WEB",
                "client": "WEB",
            },
            referer="https://data.eastmoney.com/",
        )
        result = payload.get("result") if isinstance(payload, dict) else None
        data = result.get("data") if isinstance(result, dict) else None
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []

    def _get_json(
        self,
        url: str,
        params: dict[str, Any],
        *,
        referer: str,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self._throttle()
        full_url = f"{url}?{urlencode(params)}"
        request = Request(
            full_url,
            headers={
                "User-Agent": UA,
                "Referer": referer,
                "Origin": referer.rstrip("/"),
                "Accept": "application/json,text/plain,*/*",
            },
        )
        with urlopen(request, timeout=timeout or self.timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
        return _loads_json_or_jsonp(text)

    def _throttle(self) -> None:
        with self._lock:
            wait = self.min_interval - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait + random.uniform(0.1, 0.5))
            self._last_call = time.time()

    def _result(
        self,
        capability: str,
        endpoint: str,
        data: list[dict],
        *,
        trade_date: str | None,
        unit_map: dict[str, str] | None = None,
        coverage: dict[str, Any] | None = None,
    ) -> ProviderResult[list[dict]]:
        status = DataStatus.OK if data else DataStatus.EMPTY
        result_coverage = {"coverage_ratio": 1.0 if data else 0.0}
        if coverage:
            result_coverage.update(coverage)
        return ProviderResult(
            data=data,
            meta=SourceMetadata(
                provider=self.provider_name,
                capability=capability,
                endpoint=endpoint,
                status=status,
                trade_date=trade_date,
                unit_map=unit_map or {},
                schema_version=self.schema_version,
            ),
            coverage=result_coverage,
        )

    def _unavailable(
        self,
        capability: str,
        endpoint: str,
        trade_date: str | None,
        exc: Exception,
    ) -> ProviderResult[list[dict]]:
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
            coverage={"coverage_ratio": 0.0, "warnings": [warning]},
        )


def _loads_json_or_jsonp(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        return {}
    if not stripped.startswith(("{", "[")):
        start = stripped.find("(")
        end = stripped.rfind(")")
        if start >= 0 and end > start:
            stripped = stripped[start + 1:end]
    payload = json.loads(stripped)
    return payload if isinstance(payload, dict) else {"data": payload}


def _klines(payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") if isinstance(payload, dict) else None
    klines = data.get("klines") if isinstance(data, dict) else None
    return [line for line in klines if isinstance(line, str)] if isinstance(klines, list) else []


def _diff(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    diff = data.get("diff") if isinstance(data, dict) else None
    return [row for row in diff if isinstance(row, dict)] if isinstance(diff, list) else []


def _split_kline(line: str) -> list[str]:
    return [part.strip() for part in line.split(",")]


def _flow_row(parts: list[str], *, date_key: str) -> dict[str, Any]:
    return {
        date_key: parts[0],
        "main_net": _to_float(parts[1]),
        "small_net": _to_float(parts[2]),
        "mid_net": _to_float(parts[3]),
        "large_net": _to_float(parts[4]),
        "super_net": _to_float(parts[5]),
    }


def _flow_unit_map() -> dict[str, str]:
    return {
        "main_net": "CNY",
        "small_net": "CNY",
        "mid_net": "CNY",
        "large_net": "CNY",
        "super_net": "CNY",
    }


def _sector_flow_row(index: int, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": index,
        "sector_name": item.get("f14") or "",
        "sector_type": "industry",
        "provider_sector_code": item.get("f12") or "",
        "taxonomy": "eastmoney",
        "main_net_inflow": {"amount": _to_float(item.get("f62")), "unit": "CNY"},
        "change_pct": _to_float(item.get("f3")),
        "up_count": _to_int(item.get("f104")),
        "down_count": _to_int(item.get("f105")),
        "leader": item.get("f140") or item.get("f128") or "",
        "leader_change": _to_float(item.get("f136")),
    }


def _dragon_tiger_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": row.get("SECURITY_CODE") or "",
        "name": row.get("SECURITY_NAME_ABBR") or "",
        "trade_date": str(row.get("TRADE_DATE") or "")[:10],
        "reason": row.get("EXPLANATION") or "",
        "close": _to_float(row.get("CLOSE_PRICE")),
        "change_pct": _to_float(row.get("CHANGE_RATE")),
        "net_buy_wan": _to_float(row.get("BILLBOARD_NET_AMT")) / 10000.0,
        "buy_wan": _to_float(row.get("BILLBOARD_BUY_AMT")) / 10000.0,
        "sell_wan": _to_float(row.get("BILLBOARD_SELL_AMT")) / 10000.0,
        "turnover_pct": _to_float(row.get("TURNOVERRATE")),
    }


def _lockup_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": row.get("SECURITY_CODE") or "",
        "name": row.get("SECURITY_NAME_ABBR") or "",
        "unlock_date": str(row.get("FREE_DATE") or "")[:10],
        "date": str(row.get("FREE_DATE") or "")[:10],
        "type": row.get("LIMITED_STOCK_TYPE") or "",
        "shares": _to_float(row.get("FREE_SHARES_NUM")),
        "ratio": _to_float(row.get("FREE_RATIO")),
    }


def _normalize_code(code: str) -> str:
    text = str(code or "").strip().upper()
    if text.startswith(("SH", "SZ", "BJ")) and len(text) >= 8:
        return text[2:]
    if "." in text:
        return text.split(".", 1)[0]
    return text


def _secid(code: str) -> str:
    normalized = _normalize_code(code)
    market = "1" if normalized.startswith("6") else "0"
    return f"{market}.{normalized}"


def _to_float(value: Any) -> float:
    if value in (None, "", "-"):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    return int(_to_float(value))
