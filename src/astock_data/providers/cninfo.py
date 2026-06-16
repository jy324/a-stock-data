"""Cninfo HTTP provider for announcement search."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..models import DataStatus, ProviderResult, SourceMetadata

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
ORGID_URL = "http://www.cninfo.com.cn/new/data/szse_stock.json"
ANNOUNCEMENT_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"


class CninfoProvider:
    """Minimal built-in provider for cninfo announcements."""

    provider_name = "cninfo"
    schema_version = "v1"

    def __init__(self, *, timeout: float = 15.0):
        self.timeout = timeout
        self._orgid_map: dict[str, str] = {}
        self._orgid_lock = threading.Lock()

    def get_announcements(
        self,
        *,
        code: str,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 30,
    ) -> ProviderResult[list[dict]]:
        normalized_code = _normalize_code(code)
        safe_limit = max(1, min(int(limit or 30), 100))
        try:
            org_id = self._orgid(normalized_code)
            body = {
                "stock": f"{normalized_code},{org_id}",
                "tabName": "fulltext",
                "pageSize": str(safe_limit),
                "pageNum": "1",
                "column": "",
                "category": "",
                "plate": "",
                "seDate": _date_range(start_date, end_date),
                "searchkey": "",
                "secid": "",
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true",
            }
            payload = self._post_json(ANNOUNCEMENT_URL, body)
            items = payload.get("announcements") if isinstance(payload, dict) else None
            rows = [
                {
                    "code": normalized_code,
                    "title": str(item.get("announcementTitle") or ""),
                    "type": str(item.get("announcementTypeName") or ""),
                    "date": _cninfo_ts_to_date(item.get("announcementTime")),
                    "announcement_id": str(item.get("announcementId") or ""),
                    "url": (
                        "https://www.cninfo.com.cn/new/disclosure/detail?"
                        f"annoId={item.get('announcementId') or ''}"
                    ),
                }
                for item in (items or [])
                if isinstance(item, dict)
            ]
            status = DataStatus.OK if rows else DataStatus.EMPTY
            return ProviderResult(
                data=rows,
                meta=SourceMetadata(
                    provider=self.provider_name,
                    capability="announcements",
                    endpoint=ANNOUNCEMENT_URL,
                    status=status,
                    trade_date=end_date,
                    schema_version=self.schema_version,
                ),
                coverage={
                    "coverage_ratio": 1.0 if rows else 0.0,
                    "requested_limit": safe_limit,
                    "returned_count": len(rows),
                    "filtered_code": normalized_code,
                },
            )
        except Exception as exc:
            warning = f"{type(exc).__name__}: {exc}"
            return ProviderResult(
                data=[],
                meta=SourceMetadata(
                    provider=self.provider_name,
                    capability="announcements",
                    endpoint=ANNOUNCEMENT_URL,
                    status=DataStatus.UNAVAILABLE,
                    trade_date=end_date,
                    warnings=[warning],
                    schema_version=self.schema_version,
                ),
                coverage={"coverage_ratio": 0.0, "warnings": [warning], "filtered_code": normalized_code},
            )

    def _orgid(self, code: str) -> str:
        with self._orgid_lock:
            if not self._orgid_map:
                payload = self._get_json(ORGID_URL)
                stocks = payload.get("stockList") if isinstance(payload, dict) else None
                self._orgid_map = {
                    str(item.get("code")): str(item.get("orgId"))
                    for item in (stocks or [])
                    if isinstance(item, dict) and item.get("code") and item.get("orgId")
                }
            return self._orgid_map.get(code) or _fallback_orgid(code)

    def _get_json(self, url: str) -> dict[str, Any]:
        request = Request(url, headers={"User-Agent": UA, "Accept": "application/json,text/plain,*/*"})
        with urlopen(request, timeout=self.timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {"data": payload}

    def _post_json(self, url: str, body: dict[str, str]) -> dict[str, Any]:
        request = Request(
            url,
            data=urlencode(body).encode("utf-8"),
            headers={
                "User-Agent": UA,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://www.cninfo.com.cn/new/disclosure",
                "Origin": "https://www.cninfo.com.cn",
                "Accept": "application/json,text/plain,*/*",
            },
            method="POST",
        )
        with urlopen(request, timeout=self.timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {"data": payload}


def _cninfo_ts_to_date(value: Any) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000).strftime("%Y-%m-%d")
    return str(value)[:10] if value else ""


def _date_range(start_date: str | None, end_date: str | None) -> str:
    if start_date and end_date:
        return f"{start_date}~{end_date}"
    if start_date:
        return f"{start_date}~"
    if end_date:
        return f"~{end_date}"
    return ""


def _fallback_orgid(code: str) -> str:
    if code.startswith("6"):
        return f"gssh0{code}"
    if code.startswith(("8", "4")):
        return f"gsbj0{code}"
    return f"gssz0{code}"


def _normalize_code(code: str) -> str:
    text = str(code or "").strip().upper()
    if text.startswith(("SH", "SZ", "BJ")) and len(text) >= 8:
        return text[2:]
    if "." in text:
        return text.split(".", 1)[0]
    return text
