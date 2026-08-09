from __future__ import annotations

import json
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

from astock_data.providers.eastmoney import EastmoneyProvider


class FakeResponse:
    def __init__(self, payload: str):
        self._payload = payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._payload


def test_stock_reports_normalize_ticker_and_map_stable_fields():
    payload = {
        "data": [
            {
                "infoCode": "AP202608080001",
                "title": "公司深度报告",
                "publishDate": "2026-08-08 00:00:00",
                "orgSName": "测试证券",
                "emRatingName": "买入",
                "indvInduName": "白酒",
                "predictThisYearEps": 50.1,
                "predictNextYearEps": 55.2,
                "predictNextTwoYearEps": 60.3,
                "attachPages": 30,
                "attachSize": 1024,
            }
        ],
        "TotalPage": 1,
    }

    def open_reports(request, *, timeout):
        query = parse_qs(urlparse(request.full_url).query)
        assert query["code"] == ["600519"]
        assert query["qType"] == ["0"]
        return FakeResponse(json.dumps(payload))

    provider = EastmoneyProvider(min_interval=0)
    with patch("astock_data.providers.eastmoney.urlopen", side_effect=open_reports):
        result = provider.get_stock_reports(code="SH600519", max_pages=2)

    assert result.status == "ok"
    assert result.data == [
        {
            "report_id": "AP202608080001",
            "title": "公司深度报告",
            "publish_date": "2026-08-08",
            "organization": "测试证券",
            "rating": "买入",
            "industry_name": "白酒",
            "industry_code": None,
            "eps_current_year": 50.1,
            "eps_next_year": 55.2,
            "eps_next_two_year": 60.3,
            "report_type": None,
            "pages": 30,
            "size_kb": 1024.0,
        }
    ]
    assert result.coverage["pages_fetched"] == 1


def test_old_bse_stock_report_code_is_not_silently_reported_empty():
    provider = EastmoneyProvider(min_interval=0)

    with patch(
        "astock_data.providers.eastmoney.urlopen",
        return_value=FakeResponse('{"data":[],"TotalPage":0}'),
    ):
        result = provider.get_stock_reports(code="832982", max_pages=1)

    assert result.status == "unavailable"
    assert "920" in result.meta.warnings[0]


def test_stock_reports_mark_max_pages_truncation_partial():
    payload = {"data": [{"infoCode": "report-1", "title": "报告"}], "TotalPage": 3}
    provider = EastmoneyProvider(min_interval=0)

    with patch(
        "astock_data.providers.eastmoney.urlopen",
        return_value=FakeResponse(json.dumps(payload)),
    ):
        result = provider.get_stock_reports(code="600519", max_pages=1)

    assert result.status == "partial"
    assert result.meta.is_partial is True
    assert result.coverage["upstream_total_pages"] == 3
    assert result.coverage["coverage_ratio"] == 1 / 3
    assert result.coverage["is_full_coverage"] is False
    assert "max_pages=1" in result.meta.warnings[0]


def test_stock_reports_keep_truthful_coverage_when_a_later_page_fails():
    first_page = {"data": [{"infoCode": "report-1", "title": "报告"}], "TotalPage": 3}

    def open_page(request, *, timeout):
        query = parse_qs(urlparse(request.full_url).query)
        if query["pageNo"] == ["1"]:
            return FakeResponse(json.dumps(first_page))
        raise URLError("page 2 failed")

    provider = EastmoneyProvider(min_interval=0, max_retries=0)
    with patch("astock_data.providers.eastmoney.urlopen", side_effect=open_page):
        result = provider.get_stock_reports(code="600519", max_pages=3)

    assert result.status == "partial"
    assert result.meta.is_partial is True
    assert result.coverage["upstream_total_pages"] == 3
    assert result.coverage["coverage_ratio"] == 1 / 3
    assert result.coverage["is_full_coverage"] is False
    assert "page 2 unavailable" in result.meta.warnings[0]


def test_industry_reports_default_begin_date_moves_with_beijing_today():
    def open_reports(request, *, timeout):
        query = parse_qs(urlparse(request.full_url).query)
        assert query["industryCode"] == ["1238"]
        assert query["beginTime"] == ["2024-08-09"]
        assert query["qType"] == ["1"]
        return FakeResponse('{"data":[],"TotalPage":0}')

    provider = EastmoneyProvider(min_interval=0)
    with (
        patch("astock_data.providers.eastmoney._cn_today", return_value="2026-08-09"),
        patch("astock_data.providers.eastmoney.urlopen", side_effect=open_reports),
    ):
        result = provider.get_industry_reports(industry_code="1238", max_pages=1)

    assert result.status == "empty"
    assert result.coverage["begin_date"] == "2024-08-09"


def test_dragon_tiger_summary_returns_semantic_empty_structure_when_no_records():
    provider = EastmoneyProvider(min_interval=0)

    with patch(
        "astock_data.providers.eastmoney.urlopen",
        return_value=FakeResponse('{"result":{"data":[]}}'),
    ):
        result = provider.get_stock_dragon_tiger_summary(
            code="600519",
            trade_date="2026-08-05",
            lookback=30,
        )

    assert result.status == "empty"
    assert result.coverage["coverage_ratio"] == 0.0
    assert result.data == {
        "records": [],
        "seats": {"buy": [], "sell": []},
        "institution": {
            "buy_amount": {"amount": 0.0, "unit": "CNY"},
            "sell_amount": {"amount": 0.0, "unit": "CNY"},
            "net_amount": {"amount": 0.0, "unit": "CNY"},
        },
    }


def test_dragon_tiger_summary_keeps_dict_shape_when_provider_is_unavailable():
    provider = EastmoneyProvider(min_interval=0, max_retries=0)

    with patch(
        "astock_data.providers.eastmoney.urlopen",
        side_effect=URLError("unavailable"),
    ):
        result = provider.get_stock_dragon_tiger_summary(
            code="600519",
            trade_date="2026-08-05",
        )

    assert result.status == "unavailable"
    assert result.data == {
        "records": [],
        "seats": {"buy": [], "sell": []},
        "institution": {
            "buy_amount": {"amount": 0.0, "unit": "CNY"},
            "sell_amount": {"amount": 0.0, "unit": "CNY"},
            "net_amount": {"amount": 0.0, "unit": "CNY"},
        },
    }


def test_dragon_tiger_summary_maps_latest_seats_and_institution_amounts():
    payloads = {
        "RPT_DAILYBILLBOARD_DETAILSNEW": [
            {
                "TRADE_DATE": "2026-08-01 00:00:00",
                "EXPLANATION": "日涨幅偏离值达到7%",
                "BILLBOARD_NET_AMT": 10_000_000,
                "TURNOVERRATE": 8.5,
            }
        ],
        "RPT_BILLBOARD_DAILYDETAILSBUY": [
            {"OPERATEDEPT_NAME": "机构专用", "OPERATEDEPT_CODE": "0", "BUY": 8_000_000, "SELL": 1_000_000, "NET": 7_000_000}
        ],
        "RPT_BILLBOARD_DAILYDETAILSSELL": [
            {"OPERATEDEPT_NAME": "机构专用", "OPERATEDEPT_CODE": "0", "BUY": 500_000, "SELL": 2_000_000, "NET": -1_500_000}
        ],
    }

    def open_datacenter(request, *, timeout):
        query = parse_qs(urlparse(request.full_url).query)
        return FakeResponse(json.dumps({"result": {"data": payloads[query["reportName"][0]]}}))

    provider = EastmoneyProvider(min_interval=0)
    with patch("astock_data.providers.eastmoney.urlopen", side_effect=open_datacenter):
        result = provider.get_stock_dragon_tiger_summary(
            code="002475",
            trade_date="2026-08-05",
            lookback=30,
        )

    assert result.status == "ok"
    assert result.data["records"][0]["net_buy"] == {"amount": 10_000_000.0, "unit": "CNY"}
    assert result.data["seats"]["buy"][0]["net_amount"] == {"amount": 7_000_000.0, "unit": "CNY"}
    assert result.data["institution"] == {
        "buy_amount": {"amount": 8_000_000.0, "unit": "CNY"},
        "sell_amount": {"amount": 2_000_000.0, "unit": "CNY"},
        "net_amount": {"amount": 6_000_000.0, "unit": "CNY"},
    }
