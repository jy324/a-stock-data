"""Domain-level A-share data facade.

The facade intentionally exposes stable capability names. Individual upstream
providers, such as Eastmoney or Cninfo, remain implementation details behind
the injected provider objects.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from .models import DataStatus, ProviderResult, SourceMetadata


class AStockDataClient:
    """Facade consumed by DSA and other applications."""

    def __init__(self, *, eastmoney_provider: Any = None, cninfo_provider: Any = None):
        self._eastmoney = eastmoney_provider
        self._cninfo = cninfo_provider

    def get_stock_intraday_flow(self, code: str, *, trade_date: str | None = None) -> ProviderResult[list[dict]]:
        return self._call(
            self._eastmoney,
            "get_stock_intraday_flow",
            capability="stock_intraday_flow",
            trade_date=trade_date,
            code=_normalize_code(code),
        )

    def get_stock_flow_history(
        self,
        code: str,
        *,
        trade_date: str | None = None,
        lookback: int = 120,
    ) -> ProviderResult[list[dict]]:
        normalized = _normalize_code(code)
        result = self._call(
            self._eastmoney,
            "get_stock_flow_history",
            capability="stock_flow_history",
            trade_date=trade_date,
            code=normalized,
            lookback=_safe_lookback(lookback),
        )
        rows = _rows(result.data)
        filtered = _filter_rows_by_code(rows, normalized)
        clipped = _clip_rows_by_lookback(filtered, _safe_lookback(lookback))
        return _replace_result_data(
            result,
            clipped,
            {
                "requested_lookback": _safe_lookback(lookback),
                "returned_count": len(clipped),
                "filtered_count": len(filtered),
            },
        )

    def get_sector_flow_ranking(
        self,
        *,
        trade_date: str | None = None,
        limit: int = 10,
    ) -> ProviderResult[list[dict]]:
        safe_limit = max(1, int(limit or 10))
        result = self._call(
            self._eastmoney,
            "get_sector_flow_ranking",
            capability="sector_flow_ranking",
            trade_date=trade_date,
            limit=safe_limit,
        )
        return _replace_result_data(result, _rows(result.data)[:safe_limit], {"requested_limit": safe_limit})

    def get_market_dragon_tiger(
        self,
        *,
        trade_date: str | None = None,
        limit: int | None = None,
    ) -> ProviderResult[list[dict]]:
        result = self._call(
            self._eastmoney,
            "get_market_dragon_tiger",
            capability="market_dragon_tiger",
            trade_date=trade_date,
            limit=limit,
        )
        rows = _rows(result.data)
        if limit is not None:
            rows = rows[:max(1, int(limit))]
        return _replace_result_data(result, rows)

    def get_stock_dragon_tiger(self, code: str, *, trade_date: str | None = None) -> ProviderResult[list[dict]]:
        normalized = _normalize_code(code)
        result = self._call(
            self._eastmoney,
            "get_stock_dragon_tiger",
            capability="stock_dragon_tiger",
            trade_date=trade_date,
            code=normalized,
        )
        return _replace_result_data(result, _filter_rows_by_code(_rows(result.data), normalized))

    def get_announcements(
        self,
        code: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 30,
    ) -> ProviderResult[list[dict]]:
        normalized = _normalize_code(code)
        result = self._call(
            self._cninfo,
            "get_announcements",
            capability="announcements",
            trade_date=end_date,
            code=normalized,
            start_date=start_date,
            end_date=end_date,
            limit=max(1, int(limit or 30)),
        )
        return _replace_result_data(result, _filter_rows_by_code(_rows(result.data), normalized))

    def get_lockup_events(
        self,
        code: str,
        *,
        trade_date: str | None = None,
        forward_days: int = 90,
        limit: int | None = None,
    ) -> ProviderResult[list[dict]]:
        normalized = _normalize_code(code)
        result = self._call(
            self._eastmoney,
            "get_lockup_events",
            capability="lockup_events",
            trade_date=trade_date,
            code=normalized,
            forward_days=max(1, int(forward_days or 90)),
            limit=limit,
        )
        rows = _filter_rows_by_code(_rows(result.data), normalized, require_explicit_code=True)
        if limit is not None:
            rows = rows[:max(1, int(limit))]
        return _replace_result_data(result, rows, {"filtered_code": normalized})

    def _call(self, provider: Any, method_name: str, *, capability: str, trade_date: str | None, **kwargs: Any):
        if provider is None:
            return _unavailable_result(capability, trade_date, "provider is not configured")
        method = getattr(provider, method_name, None)
        if method is None:
            return _unavailable_result(capability, trade_date, f"provider method missing: {method_name}")
        raw = method(**kwargs)
        return _coerce_result(raw, capability=capability, trade_date=trade_date)


def _coerce_result(raw: Any, *, capability: str, trade_date: str | None) -> ProviderResult[Any]:
    if isinstance(raw, ProviderResult):
        return raw
    if isinstance(raw, dict) and "meta" in raw:
        meta_raw = raw.get("meta") or {}
        meta = SourceMetadata(
            provider=str(meta_raw.get("provider") or "unknown"),
            capability=str(meta_raw.get("capability") or capability),
            endpoint=str(meta_raw.get("endpoint") or ""),
            status=str(meta_raw.get("status") or DataStatus.UNAVAILABLE.value),
            trade_date=str(meta_raw.get("trade_date") or trade_date or "") or None,
            as_of=meta_raw.get("as_of"),
            fetched_at=meta_raw.get("fetched_at"),
            is_partial=bool(meta_raw.get("is_partial", False)),
            unit_map=dict(meta_raw.get("unit_map") or {}),
            warnings=list(meta_raw.get("warnings") or []),
            schema_version=str(meta_raw.get("schema_version") or "v1"),
        )
        return ProviderResult(data=raw.get("data"), meta=meta, coverage=dict(raw.get("coverage") or {}))
    return ProviderResult(
        data=raw,
        meta=SourceMetadata(
            provider="unknown",
            capability=capability,
            endpoint="",
            status=DataStatus.OK if raw else DataStatus.EMPTY,
            trade_date=trade_date,
        ),
    )


def _unavailable_result(capability: str, trade_date: str | None, warning: str) -> ProviderResult[list[dict]]:
    return ProviderResult(
        data=[],
        meta=SourceMetadata(
            provider="unconfigured",
            capability=capability,
            endpoint="",
            status=DataStatus.UNAVAILABLE,
            trade_date=trade_date,
            warnings=[warning],
        ),
        coverage={"coverage_ratio": 0.0, "warnings": [warning]},
    )


def _replace_result_data(
    result: ProviderResult[Any],
    data: Any,
    coverage_updates: dict[str, Any] | None = None,
) -> ProviderResult[Any]:
    coverage = dict(result.coverage or {})
    if coverage_updates:
        coverage.update(coverage_updates)
    if isinstance(data, list):
        coverage.setdefault("returned_count", len(data))
    return ProviderResult(data=data, meta=result.meta, coverage=coverage)


def _normalize_code(code: str) -> str:
    text = str(code or "").strip().upper()
    if text.startswith(("SH", "SZ", "BJ")) and len(text) >= 8:
        return text[2:]
    if "." in text:
        return text.split(".", 1)[0]
    return text


def _rows(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("rows", "items", "events", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _filter_rows_by_code(
    rows: Iterable[dict],
    code: str,
    *,
    require_explicit_code: bool = False,
) -> list[dict]:
    output: list[dict] = []
    for row in rows:
        row_code = _row_code(row)
        if (row_code is None and not require_explicit_code) or row_code == code:
            output.append(row)
    return output


def _row_code(row: dict) -> str | None:
    for key in ("code", "stock_code", "security_code", "SECURITY_CODE", "股票代码"):
        value = row.get(key)
        if value not in (None, ""):
            return _normalize_code(str(value))
    return None


def _safe_lookback(value: int) -> int:
    try:
        return max(1, min(int(value), 120))
    except (TypeError, ValueError):
        return 120


def _clip_rows_by_lookback(rows: list[dict], lookback: int) -> list[dict]:
    def sort_key(row: dict) -> str:
        for key in ("trade_date", "date", "TRADE_DATE", "日期"):
            value = row.get(key)
            if value not in (None, ""):
                return str(value)
        return ""

    sorted_rows = sorted(rows, key=sort_key, reverse=True)
    return sorted_rows[:lookback]
