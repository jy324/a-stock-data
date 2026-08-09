"""Domain-level A-share data facade.

The facade intentionally exposes stable capability names. Individual upstream
providers, such as Eastmoney or Cninfo, remain implementation details behind
the injected provider objects.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any, Literal

from .models import DataStatus, ProviderResult, SourceMetadata
from .tickers import normalize_ticker


class AStockDataClient:
    """Facade consumed by DSA and other applications."""

    def __init__(
        self,
        *,
        eastmoney_provider: Any = None,
        cninfo_provider: Any = None,
        tencent_provider: Any = None,
        tdx_provider: Any = None,
        ths_provider: Any = None,
    ):
        self._eastmoney = eastmoney_provider
        self._cninfo = cninfo_provider
        self._tencent = tencent_provider
        self._tdx = tdx_provider
        self._ths = ths_provider

    @classmethod
    def from_defaults(cls) -> AStockDataClient:
        """Build a client with the package's built-in HTTP providers."""
        from .providers.cninfo import CninfoProvider
        from .providers.eastmoney import EastmoneyProvider
        from .providers.tdx import TdxProvider
        from .providers.tencent import TencentProvider
        from .providers.ths import ThsProvider

        return cls(
            eastmoney_provider=EastmoneyProvider(),
            cninfo_provider=CninfoProvider(),
            tencent_provider=TencentProvider(),
            tdx_provider=TdxProvider(),
            ths_provider=ThsProvider(),
        )

    def get_realtime_quotes(self, codes: Iterable[str]) -> ProviderResult[list[dict]]:
        if isinstance(codes, (str, bytes)):
            raise ValueError("codes must be an iterable of ticker strings")
        requested = [str(code).strip() for code in codes]
        if not requested:
            raise ValueError("codes must contain at least one ticker")
        for code in requested:
            normalize_ticker(code)
        result = self._call(
            self._tencent,
            "get_realtime_quotes",
            capability="realtime_quotes",
            trade_date=None,
            codes=requested,
        )
        return _replace_result_data(
            result,
            _rows(result.data),
            {"requested_count": len(requested)},
        )

    def get_tdx_bars(
        self,
        symbol: str,
        *,
        frequency: int = 9,
        offset: int = 800,
        market: str = "std",
    ) -> ProviderResult[list[dict]]:
        safe_symbol = _normalize_tdx_symbol(symbol, market)
        if isinstance(frequency, bool) or not isinstance(frequency, int):
            raise ValueError("frequency must be an integer")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 1:
            raise ValueError("offset must be a positive integer")
        result = self._call(
            self._tdx,
            "get_bars",
            capability="tdx_bars",
            trade_date=None,
            symbol=safe_symbol,
            frequency=frequency,
            offset=offset,
            market=market,
        )
        return _replace_result_data(result, _rows(result.data), {"market": market})

    def get_tdx_quote(self, symbol: str, *, market: str = "std") -> ProviderResult[list[dict]]:
        safe_symbol = _normalize_tdx_symbol(symbol, market)
        result = self._call(
            self._tdx,
            "get_quote",
            capability="tdx_quote",
            trade_date=None,
            symbol=safe_symbol,
            market=market,
        )
        return _replace_result_data(result, _rows(result.data), {"market": market})

    def get_tdx_transactions(
        self,
        symbol: str,
        *,
        trade_date: str,
        market: str = "std",
    ) -> ProviderResult[list[dict]]:
        safe_symbol = _normalize_tdx_symbol(symbol, market)
        date_text = _validate_compact_date(trade_date, "trade_date")
        result = self._call(
            self._tdx,
            "get_transactions",
            capability="tdx_transactions",
            trade_date=f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:]}",
            symbol=safe_symbol,
            date=date_text,
            market=market,
        )
        return _replace_result_data(result, _rows(result.data), {"market": market})

    def get_stock_reports(self, code: str, *, max_pages: int = 5) -> ProviderResult[list[dict]]:
        normalized = normalize_ticker(code, stock_only=True)
        safe_max_pages = _validate_bounded_positive_int(max_pages, "max_pages", 20)
        result = self._call(
            self._eastmoney,
            "get_stock_reports",
            capability="stock_reports",
            trade_date=None,
            code=normalized,
            max_pages=safe_max_pages,
        )
        return _replace_result_data(
            result,
            _rows(result.data),
            {"filtered_code": normalized, "requested_max_pages": safe_max_pages},
        )

    def get_industry_reports(
        self,
        *,
        industry_code: str = "*",
        begin_date: str | None = None,
        max_pages: int = 5,
    ) -> ProviderResult[list[dict]]:
        safe_industry_code = str(industry_code or "").strip()
        if not safe_industry_code:
            raise ValueError("industry_code must not be empty")
        safe_begin_date = _validate_iso_date(begin_date, "begin_date") if begin_date is not None else None
        safe_max_pages = _validate_bounded_positive_int(max_pages, "max_pages", 20)
        result = self._call(
            self._eastmoney,
            "get_industry_reports",
            capability="industry_reports",
            trade_date=None,
            industry_code=safe_industry_code,
            begin_date=safe_begin_date,
            max_pages=safe_max_pages,
        )
        return _replace_result_data(
            result,
            _rows(result.data),
            {"industry_code": safe_industry_code, "requested_max_pages": safe_max_pages},
        )

    def get_eps_forecast(self, code: str) -> ProviderResult[list[dict]]:
        normalized = normalize_ticker(code, stock_only=True)
        result = self._call(
            self._ths,
            "get_eps_forecast",
            capability="eps_forecast",
            trade_date=None,
            code=normalized,
        )
        return _replace_result_data(result, _rows(result.data), {"filtered_code": normalized})

    def get_stock_dragon_tiger_summary(
        self,
        code: str,
        *,
        trade_date: str,
        lookback: int = 30,
    ) -> ProviderResult[dict[str, Any]]:
        normalized = normalize_ticker(code, stock_only=True)
        safe_trade_date = _validate_iso_date(trade_date, "trade_date")
        safe_lookback = _validate_bounded_positive_int(lookback, "lookback", 365)
        result = self._call_with_provider_kwargs(
            self._eastmoney,
            "get_stock_dragon_tiger_summary",
            capability="stock_dragon_tiger_summary",
            trade_date=safe_trade_date,
            provider_kwargs={
                "code": normalized,
                "trade_date": safe_trade_date,
                "lookback": safe_lookback,
            },
        )
        return _replace_result_data(
            result,
            _dragon_tiger_summary_data(result.data),
            {"filtered_code": normalized, "lookback_days": safe_lookback},
        )

    def get_stock_intraday_flow(self, code: str, *, trade_date: str | None = None) -> ProviderResult[list[dict]]:
        return self._call(
            self._eastmoney,
            "get_stock_intraday_flow",
            capability="stock_intraday_flow",
            trade_date=trade_date,
            code=normalize_ticker(code, stock_only=True),
        )

    def get_stock_flow_history(
        self,
        code: str,
        *,
        trade_date: str | None = None,
        lookback: int = 120,
    ) -> ProviderResult[list[dict]]:
        normalized = normalize_ticker(code, stock_only=True)
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

    def get_board_fund_flow(
        self,
        *,
        board_type: Literal["industry", "concept", "region"] = "industry",
        period: Literal["today", "5d", "10d"] = "today",
        limit: int = 20,
    ) -> ProviderResult[list[dict]]:
        """Return a current board-fund-flow snapshot; historical date replay is not supported."""
        safe_board_type = _validate_board_fund_flow_board_type(board_type)
        safe_period = _validate_board_fund_flow_period(period)
        safe_limit = _validate_board_fund_flow_limit(limit)
        result = self._call(
            self._eastmoney,
            "get_board_fund_flow",
            capability="board_fund_flow",
            trade_date=None,
            board_type=safe_board_type,
            period=safe_period,
            limit=safe_limit,
        )
        rows = _rows(result.data)[:safe_limit]
        return _replace_result_data(
            result,
            rows,
            {
                "requested_limit": safe_limit,
                "returned_count": len(rows),
                "board_type": safe_board_type,
                "period": safe_period,
            },
        )

    def get_stock_monitor(self, *, active_only: bool = True) -> ProviderResult[list[dict]]:
        if not isinstance(active_only, bool):
            raise ValueError("active_only must be a boolean")
        result = self._call(
            self._eastmoney,
            "get_stock_monitor",
            capability="stock_monitor",
            trade_date=None,
            active_only=active_only,
        )
        return _replace_result_data(result, _rows(result.data), {"active_only": active_only})

    def get_price_anomalies(
        self,
        *,
        page: int = 1,
        page_size: int = 200,
    ) -> ProviderResult[list[dict]]:
        safe_page = _validate_positive_int(page, "page")
        safe_page_size = _validate_page_size(page_size)
        result = self._call(
            self._eastmoney,
            "get_price_anomalies",
            capability="price_anomalies",
            trade_date=None,
            page=safe_page,
            page_size=safe_page_size,
        )
        return _replace_result_data(
            result,
            _rows(result.data),
            {"page": safe_page, "page_size": safe_page_size},
        )

    def get_price_anomaly_counts(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> ProviderResult[list[dict]]:
        safe_page = _validate_positive_int(page, "page")
        safe_page_size = _validate_page_size(page_size)
        result = self._call(
            self._eastmoney,
            "get_price_anomaly_counts",
            capability="price_anomaly_counts",
            trade_date=None,
            page=safe_page,
            page_size=safe_page_size,
        )
        return _replace_result_data(
            result,
            _rows(result.data),
            {"page": safe_page, "page_size": safe_page_size},
        )

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
        normalized = normalize_ticker(code, stock_only=True)
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
        normalized = normalize_ticker(code, stock_only=True)
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
        normalized = normalize_ticker(code, stock_only=True)
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
        return self._call_with_provider_kwargs(
            provider,
            method_name,
            capability=capability,
            trade_date=trade_date,
            provider_kwargs=kwargs,
        )

    def _call_with_provider_kwargs(
        self,
        provider: Any,
        method_name: str,
        *,
        capability: str,
        trade_date: str | None,
        provider_kwargs: dict[str, Any],
    ):
        if provider is None:
            return _unavailable_result(capability, trade_date, "provider is not configured")
        method = getattr(provider, method_name, None)
        if method is None:
            return _unavailable_result(capability, trade_date, f"provider method missing: {method_name}")
        raw = method(**provider_kwargs)
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
            try:
                return normalize_ticker(str(value))
            except ValueError:
                return None
    return None


def _safe_lookback(value: int) -> int:
    try:
        return max(1, min(int(value), 120))
    except (TypeError, ValueError):
        return 120


def _validate_board_fund_flow_board_type(value: Any) -> str:
    if not isinstance(value, str) or value not in {"industry", "concept", "region"}:
        raise ValueError("board_type must be one of: industry, concept, region")
    return value


def _validate_board_fund_flow_period(value: Any) -> str:
    if not isinstance(value, str) or value not in {"today", "5d", "10d"}:
        raise ValueError("period must be one of: today, 5d, 10d")
    return value


def _validate_board_fund_flow_limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1000:
        raise ValueError("limit must be an integer between 1 and 1000")
    return value


def _validate_positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _validate_page_size(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 200:
        raise ValueError("page_size must be an integer between 1 and 200")
    return value


def _normalize_tdx_symbol(symbol: str, market: str) -> str:
    if not isinstance(market, str) or not market.strip():
        raise ValueError("market must be a non-empty string")
    if market == "std":
        return normalize_ticker(symbol, stock_only=True)
    text = str(symbol).strip()
    if not text:
        raise ValueError("symbol must not be empty")
    return text


def _validate_bounded_positive_int(value: Any, name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer between 1 and {maximum}")
    return value


def _validate_iso_date(value: Any, name: str) -> str:
    text = str(value or "")
    if len(text) != 10 or text[4:5] != "-" or text[7:8] != "-":
        raise ValueError(f"{name} must use YYYY-MM-DD")
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"{name} must use YYYY-MM-DD") from exc


def _validate_compact_date(value: Any, name: str) -> str:
    text = str(value or "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"{name} must use YYYYMMDD")
    try:
        date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:]}")
    except ValueError as exc:
        raise ValueError(f"{name} must use YYYYMMDD") from exc
    return text


def _dragon_tiger_summary_data(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {
        "records": [],
        "seats": {"buy": [], "sell": []},
        "institution": {
            "buy_amount": {"amount": 0.0, "unit": "CNY"},
            "sell_amount": {"amount": 0.0, "unit": "CNY"},
            "net_amount": {"amount": 0.0, "unit": "CNY"},
        },
    }


def _clip_rows_by_lookback(rows: list[dict], lookback: int) -> list[dict]:
    def sort_key(row: dict) -> str:
        for key in ("trade_date", "date", "TRADE_DATE", "日期"):
            value = row.get(key)
            if value not in (None, ""):
                return str(value)
        return ""

    sorted_rows = sorted(rows, key=sort_key, reverse=True)
    return sorted_rows[:lookback]
