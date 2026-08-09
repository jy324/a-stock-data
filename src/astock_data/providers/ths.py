"""Optional Tonghuashun HTML research provider."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from io import StringIO
from typing import Any
from urllib.request import Request, urlopen

from ..models import DataStatus, ProviderResult, SourceMetadata
from ..tickers import normalize_ticker

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


class ThsProvider:
    provider_name = "tonghuashun"
    schema_version = "v1"

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        min_interval: float = 1.0,
        table_reader: Callable[[StringIO], list[Any]] | None = None,
    ):
        self.timeout = timeout
        self.min_interval = max(0.0, float(min_interval))
        self._table_reader = table_reader
        self._lock = threading.Lock()
        self._last_call = 0.0

    def get_eps_forecast(self, *, code: str) -> ProviderResult[list[dict]]:
        normalized_code = normalize_ticker(code, stock_only=True)
        endpoint = f"https://basic.10jqka.com.cn/new/{normalized_code}/worth.html"
        try:
            self._throttle()
            request = Request(
                endpoint,
                headers={"User-Agent": UA, "Referer": "https://basic.10jqka.com.cn/"},
            )
            with urlopen(request, timeout=self.timeout) as response:
                html = response.read().decode("gbk", errors="replace")
            tables = self._reader()(StringIO(html))
            selected = _select_eps_table(tables)
            rows = _normalize_eps_rows(selected)
            warnings = [] if selected is not None else ["no EPS forecast table found"]
            return ProviderResult(
                data=rows,
                meta=SourceMetadata(
                    provider=self.provider_name,
                    capability="eps_forecast",
                    endpoint=endpoint,
                    status=DataStatus.OK if rows else DataStatus.EMPTY,
                    unit_map={"eps_min": "CNY/share", "eps_mean": "CNY/share", "eps_max": "CNY/share"},
                    warnings=warnings,
                    schema_version=self.schema_version,
                ),
                coverage={
                    "coverage_ratio": 1.0 if rows else 0.0,
                    "returned_count": len(rows),
                    "filtered_code": normalized_code,
                    "warnings": warnings,
                },
            )
        except Exception as exc:
            warning = f"{type(exc).__name__}: {exc}"
            return ProviderResult(
                data=[],
                meta=SourceMetadata(
                    provider=self.provider_name,
                    capability="eps_forecast",
                    endpoint=endpoint,
                    status=DataStatus.UNAVAILABLE,
                    warnings=[warning],
                    schema_version=self.schema_version,
                ),
                coverage={"coverage_ratio": 0.0, "returned_count": 0, "warnings": [warning]},
            )

    def _reader(self) -> Callable[[StringIO], list[Any]]:
        if self._table_reader is not None:
            return self._table_reader
        try:
            import pandas as pd
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                "pandas/lxml are not installed; install astock-data[reports]"
            ) from exc
        return pd.read_html

    def _throttle(self) -> None:
        with self._lock:
            remaining = self.min_interval - (time.time() - self._last_call)
            if remaining > 0:
                time.sleep(remaining)
            self._last_call = time.time()


def _select_eps_table(tables: list[Any]) -> Any | None:
    for table in tables:
        columns = [_column_name(column) for column in getattr(table, "columns", [])]
        if any("每股收益" in column or "均值" in column for column in columns):
            return table
    return tables[0] if tables else None


def _normalize_eps_rows(table: Any | None) -> list[dict]:
    if table is None or not hasattr(table, "to_dict"):
        return []
    rows = table.to_dict("records")
    output = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        flattened = {_column_name(key): value for key, value in raw.items()}
        output.append(
            {
                "year": str(_find_value(flattened, "年度", "年份") or ""),
                "institution_count": _optional_int(_find_value(flattened, "预测机构数", "机构数")),
                "eps_min": _optional_float(_find_value(flattened, "最小值")),
                "eps_mean": _optional_float(_find_value(flattened, "均值", "平均值")),
                "eps_max": _optional_float(_find_value(flattened, "最大值")),
            }
        )
    return output


def _column_name(column: Any) -> str:
    if isinstance(column, tuple):
        return " ".join(str(part) for part in column if str(part) != "nan")
    return str(column)


def _find_value(row: dict[str, Any], *needles: str) -> Any:
    for key, value in row.items():
        if any(needle in key for needle in needles):
            return value
    return None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "", "-") else None
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    parsed = _optional_float(value)
    return int(parsed) if parsed is not None else None
