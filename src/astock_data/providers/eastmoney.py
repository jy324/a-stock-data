"""Eastmoney HTTP provider for the public AStockDataClient facade."""

from __future__ import annotations

import json
import random
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..models import DataStatus, ProviderResult, SourceMetadata
from ..tickers import normalize_ticker

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
BKZJ_URL = "https://data.eastmoney.com/dataapi/bkzj/getbkzj"
CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
MONITOR_URL = "https://mobappconfig.securities.eastmoney.com/emcfg/stock_monitor.json"
ANOMALY_BASE_URL = "https://dycalchis.eastmoney.com/price-anomaly"
REPORT_API_URL = "https://reportapi.eastmoney.com/report/list"
ANOMALY_COMMON_PARAMS = {
    "team": "h5",
    "product": "EastMoney",
    "client": "WAP",
    "version": "9001",
    "name": "WAP",
    "user": "123",
}
ANOMALY_RULES = {
    1: "主板连续10个交易日内4次出现同向异常波动",
    2: "创业板连续10个交易日内3次出现同向异常波动",
    3: "科创板连续10个交易日内3次出现同向异常波动",
    4: "连续十个交易日内日收盘价涨跌幅偏离值累计达到+100%",
    5: "连续十个交易日内日收盘价涨跌幅偏离值累计达到-50%",
    6: "连续三十个交易日内日收盘价涨跌幅偏离值累计达到+200%",
    7: "连续三十个交易日内日收盘价涨跌幅偏离值累计达到-70%",
    8: "北交所连续10个交易日内3次出现同向异常波动",
    40: "连续十个交易日内日收盘价涨跌幅偏离值累计达到+150%",
    50: "连续十个交易日内日收盘价涨跌幅偏离值累计达到-60%",
    60: "连续30个交易日内日收盘价涨跌幅偏离值累计达到+300%",
    70: "连续30个交易日内日收盘价涨跌幅偏离值累计达到-75%",
}
RETRYABLE_HTTP_STATUS = frozenset({429, 500, 502, 503, 504})
BOARD_FUND_FLOW_FILTERS = {
    "industry": "m:90+t:2",
    "concept": "m:90+t:3",
    "region": "m:90+t:1",
}
BOARD_FUND_FLOW_PERIODS: dict[str, dict[str, str | None]] = {
    "today": {
        "fid": "f62",
        "main_net_inflow": "f62",
        "main_net_inflow_pct": "f184",
        "change_pct": "f3",
        "leader": "f204",
    },
    "5d": {
        "fid": "f164",
        "main_net_inflow": "f164",
        "main_net_inflow_pct": "f165",
        "change_pct": "f109",
        "leader": "f257",
    },
    "10d": {
        "fid": "f174",
        "main_net_inflow": "f174",
        "main_net_inflow_pct": "f175",
        "change_pct": "f160",
        "leader": None,
    },
}
TODAY_SIZE_BUCKET_FIELDS = {
    "super_large_net_inflow": "f66",
    "large_net_inflow": "f72",
    "medium_net_inflow": "f78",
    "small_net_inflow": "f84",
}


class EastmoneyProvider:
    """Minimal built-in provider for DSA's A-share intelligence capabilities."""

    provider_name = "eastmoney"
    schema_version = "v1"

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        min_interval: float = 1.0,
        max_retries: int = 3,
        retry_backoff: float = 0.3,
    ):
        self.timeout = timeout
        self.min_interval = min_interval
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff = max(0.0, float(retry_backoff))
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
            selected_items = items[:safe_limit]
            rows = []
            for index, item in enumerate(selected_items, start=1):
                rows.append(_sector_flow_row(index, item))
            warnings = _sector_flow_warnings(selected_items)
            return self._result(
                "sector_flow_ranking",
                endpoint,
                rows,
                trade_date=trade_date,
                coverage={"requested_limit": safe_limit, "returned_count": len(rows)},
                status=DataStatus.PARTIAL if warnings else None,
                warnings=warnings,
            )
        except Exception as exc:
            return self._unavailable("sector_flow_ranking", endpoint, trade_date, exc)

    def get_board_fund_flow(
        self,
        *,
        board_type: Literal["industry", "concept", "region"] = "industry",
        period: Literal["today", "5d", "10d"] = "today",
        limit: int = 20,
    ) -> ProviderResult[list[dict]]:
        """Fetch the current board-fund-flow snapshot for one Eastmoney taxonomy."""
        safe_board_type = _validate_board_fund_flow_board_type(board_type)
        safe_period = _validate_board_fund_flow_period(period)
        safe_limit = _validate_board_fund_flow_limit(limit)
        endpoint = CLIST_URL
        period_fields = BOARD_FUND_FLOW_PERIODS[safe_period]
        fields = [
            "f12",
            "f14",
            period_fields["change_pct"],
            period_fields["main_net_inflow"],
            period_fields["main_net_inflow_pct"],
        ]
        if period_fields["leader"]:
            fields.append(period_fields["leader"])
        if safe_period == "today":
            fields.extend(TODAY_SIZE_BUCKET_FIELDS.values())
        coverage = {
            "requested_limit": safe_limit,
            "returned_count": 0,
            "board_type": safe_board_type,
            "period": safe_period,
            "upstream_total": None,
            "pages_fetched": 0,
            "requested_limit_satisfied": False,
            "is_full_universe": False,
        }
        items: list[dict[str, Any]] = []
        warnings: list[str] = []
        upstream_total: int | None = None
        reached_end = False
        try:
            page_number = 1
            while len(items) < safe_limit:
                try:
                    payload = self._get_json(
                        endpoint,
                        {
                            "pn": str(page_number),
                            "pz": "200",
                            "po": "1",
                            "np": "1",
                            "fltt": "2",
                            "invt": "2",
                            "fid": period_fields["fid"],
                            "fs": BOARD_FUND_FLOW_FILTERS[safe_board_type],
                            "fields": ",".join(dict.fromkeys(field for field in fields if field)),
                        },
                        referer="https://quote.eastmoney.com/",
                    )
                except Exception as exc:
                    if not items:
                        raise
                    warnings.append(f"page {page_number} unavailable: {type(exc).__name__}: {exc}")
                    break

                page_items, page_total = _board_fund_flow_page(payload)
                coverage["pages_fetched"] += 1
                if page_total is not None:
                    upstream_total = max(upstream_total or 0, page_total)
                items.extend(page_items)
                if not page_items or len(page_items) < 200:
                    reached_end = True
                    break
                if upstream_total is not None and len(items) >= upstream_total:
                    reached_end = True
                    break
                page_number += 1

            if upstream_total is None and reached_end:
                upstream_total = len(items)
            selected_items = items[:safe_limit]
            rows = [
                _board_fund_flow_row(
                    index,
                    item,
                    board_type=safe_board_type,
                    period=safe_period,
                    period_fields=period_fields,
                )
                for index, item in enumerate(selected_items, start=1)
            ]
            coverage["returned_count"] = len(rows)
            coverage["upstream_total"] = upstream_total
            coverage["requested_limit_satisfied"] = len(items) >= safe_limit or reached_end
            coverage["is_full_universe"] = reached_end or (
                upstream_total is not None and len(items) >= upstream_total
            )
            warnings.extend(_board_fund_flow_warnings(rows, period_fields))
            return self._result(
                "board_fund_flow",
                endpoint,
                rows,
                trade_date=None,
                unit_map=_board_fund_flow_unit_map(),
                coverage=coverage,
                status=DataStatus.PARTIAL if warnings else None,
                warnings=warnings,
            )
        except Exception as exc:
            return self._unavailable("board_fund_flow", endpoint, None, exc, coverage=coverage)

    def get_stock_monitor(self, *, active_only: bool = True) -> ProviderResult[list[dict]]:
        if not isinstance(active_only, bool):
            raise ValueError("active_only must be a boolean")
        evaluation_date = _cn_today()
        coverage = {
            "active_only": active_only,
            "evaluation_date": evaluation_date,
            "upstream_total": 0,
            "returned_count": 0,
        }
        try:
            payload = self._get_json(
                MONITOR_URL,
                {},
                referer="https://vipmoney.eastmoney.com/",
                timeout=20,
            )
            raw_rows = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(raw_rows, list):
                raise ValueError("Eastmoney stock monitor response is not a list")
            coverage["upstream_total"] = len(raw_rows)
            rows = []
            warnings = []
            market_map = {"1": "SH", "0": "SZ", "B": "BJ"}
            for raw in raw_rows:
                if not isinstance(raw, dict):
                    continue
                start = str(raw.get("VALIDATESTARTDATE") or "")
                end = str(raw.get("VALIDATEENDDATE") or "")
                if active_only and not (start <= evaluation_date <= end):
                    continue
                raw_market = str(raw.get("MARKET") or "").upper()
                market = market_map.get(raw_market, f"?{raw_market}")
                if market.startswith("?"):
                    warnings.append(f"unknown monitor market: {raw_market!r}")
                rows.append(
                    {
                        "code": str(raw.get("STKCODE") or ""),
                        "name": str(raw.get("STKNAME") or ""),
                        "market": market,
                        "start_date": start,
                        "end_date": end,
                        "link": str(raw.get("LINK_URL") or ""),
                    }
                )
            coverage["returned_count"] = len(rows)
            return self._result(
                "stock_monitor",
                MONITOR_URL,
                rows,
                trade_date=None,
                coverage=coverage,
                status=DataStatus.PARTIAL if warnings else None,
                warnings=warnings,
            )
        except Exception as exc:
            return self._unavailable("stock_monitor", MONITOR_URL, None, exc, coverage=coverage)

    def get_price_anomalies(
        self,
        *,
        page: int = 1,
        page_size: int = 200,
    ) -> ProviderResult[list[dict]]:
        safe_page = _validate_positive_int(page, "page")
        safe_page_size = _validate_page_size(page_size)
        endpoint = f"{ANOMALY_BASE_URL}/list"
        coverage = {"page": safe_page, "page_size": safe_page_size, "total_pages": 0, "returned_count": 0}
        try:
            payload = self._get_anomaly_payload(endpoint, safe_page, safe_page_size)
            rows = [_price_anomaly_row(raw) for raw in payload.get("data") or [] if isinstance(raw, dict)]
            trade_date = _format_compact_date(payload.get("date"))
            coverage["total_pages"] = _to_int(payload.get("pages"))
            coverage["returned_count"] = len(rows)
            return self._result(
                "price_anomalies",
                endpoint,
                rows,
                trade_date=trade_date,
                unit_map={"change_pct": "%", "deviation_pct": "%"},
                coverage=coverage,
            )
        except Exception as exc:
            return self._unavailable("price_anomalies", endpoint, None, exc, coverage=coverage)

    def get_price_anomaly_counts(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> ProviderResult[list[dict]]:
        safe_page = _validate_positive_int(page, "page")
        safe_page_size = _validate_page_size(page_size)
        endpoint = f"{ANOMALY_BASE_URL}/count"
        coverage = {"page": safe_page, "page_size": safe_page_size, "total_pages": 0, "returned_count": 0}
        try:
            payload = self._get_anomaly_payload(endpoint, safe_page, safe_page_size)
            rows = [_price_anomaly_count_row(raw) for raw in payload.get("data") or [] if isinstance(raw, dict)]
            trade_date = _format_compact_date(payload.get("date"))
            coverage["total_pages"] = _to_int(payload.get("pages"))
            coverage["returned_count"] = len(rows)
            return self._result(
                "price_anomaly_counts",
                endpoint,
                rows,
                trade_date=trade_date,
                unit_map={"price": "CNY", "change_pct": "%", "deviation_pct": "%"},
                coverage=coverage,
            )
        except Exception as exc:
            return self._unavailable("price_anomaly_counts", endpoint, None, exc, coverage=coverage)

    def get_stock_reports(self, *, code: str, max_pages: int = 5) -> ProviderResult[list[dict]]:
        normalized_code = normalize_ticker(code, stock_only=True)
        safe_max_pages = _validate_max_pages(max_pages)
        coverage = {
            "requested_max_pages": safe_max_pages,
            "pages_fetched": 0,
            "returned_count": 0,
            "filtered_code": normalized_code,
        }
        try:
            rows, warnings = self._fetch_reports(
                qtype="0",
                max_pages=safe_max_pages,
                extra_params={
                    "industryCode": "*",
                    "beginTime": "2000-01-01",
                    "code": normalized_code,
                    "orgCode": "",
                    "rcode": "",
                },
                coverage=coverage,
            )
            if not rows and normalized_code[:2] in {"43", "83", "87"}:
                raise ValueError(
                    f"{normalized_code} 属北交所老号段，研报索引多数已迁至 920xxx，请反查现行代码"
                )
            data = [_report_row(row) for row in rows]
            coverage["returned_count"] = len(data)
            return self._result(
                "stock_reports",
                REPORT_API_URL,
                data,
                trade_date=None,
                coverage=coverage,
                status=DataStatus.PARTIAL if warnings else None,
                warnings=warnings,
            )
        except Exception as exc:
            return self._unavailable("stock_reports", REPORT_API_URL, None, exc, coverage=coverage)

    def get_industry_reports(
        self,
        *,
        industry_code: str = "*",
        begin_date: str | None = None,
        max_pages: int = 5,
    ) -> ProviderResult[list[dict]]:
        safe_industry_code = str(industry_code or "*").strip()
        safe_max_pages = _validate_max_pages(max_pages)
        if begin_date is None:
            begin = (datetime.strptime(_cn_today(), "%Y-%m-%d") - timedelta(days=730)).date().isoformat()
        else:
            begin = _validate_iso_date(begin_date, "begin_date")
        coverage = {
            "requested_max_pages": safe_max_pages,
            "pages_fetched": 0,
            "returned_count": 0,
            "industry_code": safe_industry_code,
            "begin_date": begin,
        }
        try:
            rows, warnings = self._fetch_reports(
                qtype="1",
                max_pages=safe_max_pages,
                extra_params={"industryCode": safe_industry_code, "beginTime": begin},
                coverage=coverage,
            )
            data = [_report_row(row) for row in rows]
            coverage["returned_count"] = len(data)
            return self._result(
                "industry_reports",
                REPORT_API_URL,
                data,
                trade_date=None,
                coverage=coverage,
                status=DataStatus.PARTIAL if warnings else None,
                warnings=warnings,
            )
        except Exception as exc:
            return self._unavailable("industry_reports", REPORT_API_URL, None, exc, coverage=coverage)

    def _fetch_reports(
        self,
        *,
        qtype: str,
        max_pages: int,
        extra_params: dict[str, Any],
        coverage: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        all_rows: list[dict[str, Any]] = []
        warnings: list[str] = []
        for page in range(1, max_pages + 1):
            params = {
                "pageSize": "100",
                "industry": "*",
                "rating": "*",
                "ratingChange": "*",
                "endTime": "2030-01-01",
                "pageNo": str(page),
                "fields": "",
                "qType": qtype,
                "p": str(page),
                "pageNum": str(page),
                "pageNumber": str(page),
                **extra_params,
            }
            try:
                payload = self._get_json(
                    REPORT_API_URL,
                    params,
                    referer="https://data.eastmoney.com/",
                    timeout=30,
                )
            except Exception as exc:
                if not all_rows:
                    raise
                warnings.append(f"page {page} unavailable: {type(exc).__name__}: {exc}")
                break
            coverage["pages_fetched"] += 1
            page_rows = payload.get("data") or []
            if not isinstance(page_rows, list):
                raise ValueError("Eastmoney report response data is not a list")
            all_rows.extend(row for row in page_rows if isinstance(row, dict))
            total_pages = _to_int(payload.get("TotalPage")) or 1
            if not page_rows or page >= total_pages:
                break
        return all_rows, warnings

    def get_stock_dragon_tiger_summary(
        self,
        *,
        code: str,
        trade_date: str,
        lookback: int = 30,
    ) -> ProviderResult[dict[str, Any]]:
        normalized_code = normalize_ticker(code, stock_only=True)
        query_date = _validate_iso_date(trade_date, "trade_date")
        safe_lookback = _validate_bounded_positive_int(lookback, "lookback", 365)
        start_date = (
            datetime.strptime(query_date, "%Y-%m-%d") - timedelta(days=safe_lookback)
        ).strftime("%Y-%m-%d")
        coverage = {"lookback_days": safe_lookback, "record_count": 0, "latest_record_date": None}
        try:
            raw_records = self._datacenter(
                "RPT_DAILYBILLBOARD_DETAILSNEW",
                filter_str=(
                    f"(TRADE_DATE>='{start_date}')(TRADE_DATE<='{query_date}')"
                    f'(SECURITY_CODE="{normalized_code}")'
                ),
                page_size=50,
                sort_columns="TRADE_DATE",
                sort_types="-1",
            )
            records = [_dragon_summary_record(row) for row in raw_records]
            buy_rows: list[dict[str, Any]] = []
            sell_rows: list[dict[str, Any]] = []
            warnings: list[str] = []
            if records:
                latest_date = records[0]["date"]
                coverage["latest_record_date"] = latest_date
                for report_name, side in (
                    ("RPT_BILLBOARD_DAILYDETAILSBUY", "buy"),
                    ("RPT_BILLBOARD_DAILYDETAILSSELL", "sell"),
                ):
                    try:
                        detail_rows = self._datacenter(
                            report_name,
                            filter_str=f'(TRADE_DATE=\'{latest_date}\')(SECURITY_CODE="{normalized_code}")',
                            page_size=10,
                            sort_columns="BUY" if side == "buy" else "SELL",
                            sort_types="-1",
                        )
                        if side == "buy":
                            buy_rows = detail_rows
                        else:
                            sell_rows = detail_rows
                    except Exception as exc:
                        warnings.append(f"{side} seats unavailable: {type(exc).__name__}: {exc}")
            institution_buy = sum(_to_float(row.get("BUY")) for row in buy_rows if str(row.get("OPERATEDEPT_CODE") or "") == "0")
            institution_sell = sum(_to_float(row.get("SELL")) for row in sell_rows if str(row.get("OPERATEDEPT_CODE") or "") == "0")
            data = {
                "records": records,
                "seats": {
                    "buy": [_dragon_seat_row(row) for row in buy_rows[:5]],
                    "sell": [_dragon_seat_row(row) for row in sell_rows[:5]],
                },
                "institution": {
                    "buy_amount": _cny_amount(institution_buy),
                    "sell_amount": _cny_amount(institution_sell),
                    "net_amount": _cny_amount(institution_buy - institution_sell),
                },
            }
            coverage["record_count"] = len(records)
            status = DataStatus.PARTIAL if warnings else (DataStatus.OK if records else DataStatus.EMPTY)
            return self._result(
                "stock_dragon_tiger_summary",
                DATACENTER_URL,
                data,
                trade_date=query_date,
                unit_map={
                    "records.net_buy": "CNY",
                    "seats.buy_amount": "CNY",
                    "seats.sell_amount": "CNY",
                    "seats.net_amount": "CNY",
                    "institution": "CNY",
                },
                coverage=coverage,
                status=status,
                warnings=warnings,
            )
        except Exception as exc:
            return self._unavailable(
                "stock_dragon_tiger_summary",
                DATACENTER_URL,
                query_date,
                exc,
                coverage=coverage,
            )

    def _get_anomaly_payload(self, endpoint: str, page: int, page_size: int) -> dict[str, Any]:
        payload = self._get_json(
            endpoint,
            {**ANOMALY_COMMON_PARAMS, "pageSize": str(page_size), "pageNo": str(page)},
            referer="https://vipmoney.eastmoney.com/",
            timeout=20,
        )
        if payload.get("result") != 0:
            raise RuntimeError(
                f"Eastmoney price anomaly rejected: result={payload.get('result')} msg={payload.get('msg')!r}"
            )
        return payload

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
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                with urlopen(request, timeout=timeout or self.timeout) as response:
                    text = response.read().decode("utf-8", errors="replace")
                return _loads_json_or_jsonp(text)
            except HTTPError as exc:
                if exc.code not in RETRYABLE_HTTP_STATUS or attempt >= self.max_retries:
                    raise
                if self.retry_backoff:
                    time.sleep(self.retry_backoff * (2**attempt))
            except URLError:
                if attempt >= self.max_retries:
                    raise
                if self.retry_backoff:
                    time.sleep(self.retry_backoff * (2**attempt))
        raise RuntimeError("unreachable retry state")

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
        status: DataStatus | None = None,
        warnings: list[str] | None = None,
    ) -> ProviderResult[list[dict]]:
        result_status = status or (DataStatus.OK if data else DataStatus.EMPTY)
        result_coverage = {
            "coverage_ratio": 0.0 if result_status == DataStatus.EMPTY else (1.0 if data else 0.0)
        }
        if coverage:
            result_coverage.update(coverage)
        if warnings:
            result_coverage["warnings"] = warnings
        return ProviderResult(
            data=data,
            meta=SourceMetadata(
                provider=self.provider_name,
                capability=capability,
                endpoint=endpoint,
                status=result_status,
                trade_date=trade_date,
                unit_map=unit_map or {},
                warnings=warnings or [],
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
        *,
        coverage: dict[str, Any] | None = None,
    ) -> ProviderResult[list[dict]]:
        warning = f"{type(exc).__name__}: {exc}"
        result_coverage = {"coverage_ratio": 0.0, "warnings": [warning]}
        if coverage:
            result_coverage.update(coverage)
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
            coverage=result_coverage,
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


def _board_fund_flow_page(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int | None]:
    data = payload.get("data") if isinstance(payload, dict) else None
    diff = data.get("diff") if isinstance(data, dict) else None
    if not isinstance(diff, list):
        raise ValueError("Eastmoney board fund flow response missing data.diff list")
    response_code = payload.get("rc") if isinstance(payload, dict) else None
    if response_code not in (None, "", 0, "0"):
        raise ValueError(f"Eastmoney board fund flow upstream error: {response_code}")
    total_value = data.get("total") if isinstance(data, dict) else None
    try:
        total = int(total_value) if total_value not in (None, "") else None
    except (TypeError, ValueError):
        total = None
    return [row for row in diff if isinstance(row, dict)], total


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
        "main_net_inflow": {"amount": _to_optional_float(item.get("f62")), "unit": "CNY"},
        "change_pct": _to_optional_float(item.get("f3")),
        "up_count": _to_optional_int(item.get("f104")),
        "down_count": _to_optional_int(item.get("f105")),
        "leader": item.get("f140") or item.get("f128") or None,
        "leader_change": _to_optional_float(item.get("f136")),
    }


def _sector_flow_warnings(items: list[dict[str, Any]]) -> list[str]:
    if not items:
        return []
    missing_fields = []
    field_names = {
        "f3": "change_pct",
        "f104": "up_count",
        "f105": "down_count",
        "f136": "leader_change",
    }
    for source_field, output_field in field_names.items():
        if all(_is_missing(item.get(source_field)) for item in items):
            missing_fields.append(output_field)
    if all(_is_missing(item.get("f140")) and _is_missing(item.get("f128")) for item in items):
        missing_fields.append("leader")
    if not missing_fields:
        return []
    return ["sector_flow_ranking missing upstream fields: " + ", ".join(missing_fields)]


def _board_fund_flow_row(
    index: int,
    item: dict[str, Any],
    *,
    board_type: str,
    period: str,
    period_fields: dict[str, str | None],
) -> dict[str, Any]:
    row = {
        "rank": index,
        "board_name": _to_optional_str(item.get("f14")),
        "board_type": board_type,
        "provider_board_code": _to_optional_str(item.get("f12")),
        "taxonomy": "eastmoney",
        "period": period,
        "change_pct": _to_optional_float(item.get(period_fields["change_pct"])),
        "main_net_inflow": _cny_amount(item.get(period_fields["main_net_inflow"])),
        "main_net_inflow_pct": _to_optional_float(item.get(period_fields["main_net_inflow_pct"])),
        "leader": _to_optional_str(item.get(period_fields["leader"])) if period_fields["leader"] else None,
    }
    for output_field, source_field in TODAY_SIZE_BUCKET_FIELDS.items():
        row[output_field] = _cny_amount(item.get(source_field)) if period == "today" else _cny_amount(None)
    return row


def _board_fund_flow_warnings(rows: list[dict[str, Any]], period_fields: dict[str, str | None]) -> list[str]:
    if not rows:
        return []
    missing_by_row = [
        _board_fund_flow_missing_core_fields(row, period_fields)
        for row in rows
    ]
    if not all(missing_by_row):
        return []
    missing_fields = sorted({field for row_fields in missing_by_row for field in row_fields})
    return [
        "board_fund_flow missing required core fields for every selected row: " + ", ".join(missing_fields)
    ]


def _board_fund_flow_missing_core_fields(row: dict[str, Any], period_fields: dict[str, str | None]) -> list[str]:
    missing_fields = []
    if row["provider_board_code"] is None:
        missing_fields.append("f12")
    if row["board_name"] is None:
        missing_fields.append("f14")
    if row["main_net_inflow"]["amount"] is None:
        missing_fields.append(period_fields["main_net_inflow"])
    if row["main_net_inflow_pct"] is None:
        missing_fields.append(period_fields["main_net_inflow_pct"])
    if row["change_pct"] is None:
        missing_fields.append(period_fields["change_pct"])
    if period_fields["leader"] and row["leader"] is None:
        missing_fields.append(period_fields["leader"])
    return [field for field in missing_fields if field]


def _board_fund_flow_unit_map() -> dict[str, str]:
    return {
        "main_net_inflow": "CNY",
        "super_large_net_inflow": "CNY",
        "large_net_inflow": "CNY",
        "medium_net_inflow": "CNY",
        "small_net_inflow": "CNY",
    }


def _cn_today() -> str:
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def _anomaly_market(code: Any, market: Any, board: Any = None) -> str:
    digits = str(code or "")
    if digits.startswith("920") or digits[:2] in {"43", "83", "87"} or board == 8:
        return "BJ"
    return "SH" if market == 1 else "SZ"


def _price_anomaly_row(raw: dict[str, Any]) -> dict[str, Any]:
    source_rule = raw.get("e")
    rule_code = source_rule * 10 if raw.get("s") == 6 and source_rule in {4, 5, 6, 7} else source_rule
    return {
        "code": str(raw.get("c") or ""),
        "name": str(raw.get("n") or ""),
        "market": _anomaly_market(raw.get("c"), raw.get("m"), raw.get("s")),
        "change_pct": _to_optional_float(raw.get("a")),
        "deviation_pct": _to_optional_float(raw.get("x")),
        "days": _to_optional_int(raw.get("d")),
        "board_code": _to_optional_int(raw.get("s")),
        "rule_code": rule_code,
        "rule": ANOMALY_RULES.get(rule_code, f"未知规则码 {rule_code}"),
        "is_today": raw.get("o") != 2,
    }


def _price_anomaly_count_row(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": str(raw.get("c") or ""),
        "name": str(raw.get("n") or ""),
        "market": _anomaly_market(raw.get("c"), raw.get("m"), raw.get("s")),
        "price": _to_optional_float(raw.get("p")),
        "change_pct": _to_optional_float(raw.get("a")),
        "times": _to_optional_int(raw.get("t")),
        "deviation_pct": _to_optional_float(raw.get("x")),
        "days": _to_optional_int(raw.get("d")),
        "board_code": _to_optional_int(raw.get("s")),
    }


def _report_row(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "report_id": str(raw.get("infoCode") or ""),
        "title": str(raw.get("title") or ""),
        "publish_date": str(raw.get("publishDate") or "")[:10],
        "organization": str(raw.get("orgSName") or ""),
        "rating": _to_optional_str(raw.get("emRatingName")),
        "industry_name": _to_optional_str(raw.get("indvInduName") or raw.get("industryName")),
        "industry_code": _to_optional_str(raw.get("industryCode")),
        "eps_current_year": _to_optional_float(raw.get("predictThisYearEps")),
        "eps_next_year": _to_optional_float(raw.get("predictNextYearEps")),
        "eps_next_two_year": _to_optional_float(raw.get("predictNextTwoYearEps")),
        "report_type": _to_optional_str(raw.get("reportType")),
        "pages": _to_optional_int(raw.get("attachPages")),
        "size_kb": _to_optional_float(raw.get("attachSize")),
    }


def _dragon_summary_record(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": str(raw.get("TRADE_DATE") or "")[:10],
        "reason": str(raw.get("EXPLANATION") or ""),
        "net_buy": _cny_amount(raw.get("BILLBOARD_NET_AMT")),
        "turnover_pct": _to_optional_float(raw.get("TURNOVERRATE")),
    }


def _dragon_seat_row(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(raw.get("OPERATEDEPT_NAME") or ""),
        "buy_amount": _cny_amount(raw.get("BUY")),
        "sell_amount": _cny_amount(raw.get("SELL")),
        "net_amount": _cny_amount(raw.get("NET")),
        "is_institution": str(raw.get("OPERATEDEPT_CODE") or "") == "0",
    }


def _format_compact_date(value: Any) -> str | None:
    text = str(value or "")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text or None


def _validate_positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _validate_page_size(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 200:
        raise ValueError("page_size must be an integer between 1 and 200")
    return value


def _validate_max_pages(value: Any) -> int:
    return _validate_bounded_positive_int(value, "max_pages", 20)


def _validate_bounded_positive_int(value: Any, name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer between 1 and {maximum}")
    return value


def _validate_iso_date(value: Any, name: str) -> str:
    text = str(value or "")
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{name} must use YYYY-MM-DD") from exc


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
    shares = row.get("FREE_SHARES")
    if _is_missing(shares):
        shares = row.get("FREE_SHARES_NUM")
    return {
        "code": row.get("SECURITY_CODE") or "",
        "name": row.get("SECURITY_NAME_ABBR") or "",
        "unlock_date": str(row.get("FREE_DATE") or "")[:10],
        "date": str(row.get("FREE_DATE") or "")[:10],
        "type": row.get("FREE_SHARES_TYPE") or row.get("LIMITED_STOCK_TYPE") or "",
        "shares": _to_float(shares),
        "able_shares": _to_float(row.get("ABLE_FREE_SHARES")),
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


def _to_optional_float(value: Any) -> float | None:
    if _is_missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_optional_str(value: Any) -> str | None:
    return None if _is_missing(value) else str(value)


def _to_optional_int(value: Any) -> int | None:
    parsed = _to_optional_float(value)
    return int(parsed) if parsed is not None else None


def _is_missing(value: Any) -> bool:
    return value in (None, "", "-")


def _cny_amount(value: Any) -> dict[str, float | None | str]:
    return {"amount": _to_optional_float(value), "unit": "CNY"}


def _validate_board_fund_flow_board_type(value: Any) -> str:
    if not isinstance(value, str) or value not in BOARD_FUND_FLOW_FILTERS:
        raise ValueError("board_type must be one of: industry, concept, region")
    return value


def _validate_board_fund_flow_period(value: Any) -> str:
    if not isinstance(value, str) or value not in BOARD_FUND_FLOW_PERIODS:
        raise ValueError("period must be one of: today, 5d, 10d")
    return value


def _validate_board_fund_flow_limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1000:
        raise ValueError("limit must be an integer between 1 and 1000")
    return value
