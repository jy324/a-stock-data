from __future__ import annotations

import json
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import pytest

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


def _board_response_for_query(
    *,
    fs: str,
    fid: str,
    limit: int,
    fields: set[str],
    payload: str,
):
    """Return the fixture only when the provider emits the intended board query."""

    def open_board_request(request, *, timeout):
        query = parse_qs(urlparse(request.full_url).query)
        requested_fields = set(query.get("fields", [""])[0].split(","))
        if (
            query.get("fs") == [fs]
            and query.get("fid") == [fid]
            and query.get("pz") == ["200"]
            and requested_fields == fields
        ):
            return FakeResponse(payload)
        return FakeResponse('{"data":{"diff":[]}}')

    return open_board_request


def test_sector_flow_ranking_normalizes_eastmoney_fields():
    payload = """
    {
      "data": {
        "diff": [
          {"f12": "BK1036", "f14": "半导体", "f62": 123400000, "f3": 1.23, "f104": 48, "f105": 10, "f140": "龙头", "f136": 5.6}
        ]
      }
    }
    """
    provider = EastmoneyProvider(min_interval=0)

    with patch("astock_data.providers.eastmoney.urlopen", return_value=FakeResponse(payload)):
        result = provider.get_sector_flow_ranking(trade_date="2026-06-09", limit=1)

    assert result.status == "ok"
    assert result.data == [
        {
            "rank": 1,
            "sector_name": "半导体",
            "sector_type": "industry",
            "provider_sector_code": "BK1036",
            "taxonomy": "eastmoney",
            "main_net_inflow": {"amount": 123400000.0, "unit": "CNY"},
            "change_pct": 1.23,
            "up_count": 48,
            "down_count": 10,
            "leader": "龙头",
            "leader_change": 5.6,
        }
    ]


def test_sector_flow_ranking_marks_missing_optional_fields_partial():
    payload = """
    {
      "data": {
        "diff": [
          {"f12": "BK1033", "f13": 90, "f14": "电池", "f62": 4436057088}
        ]
      }
    }
    """
    provider = EastmoneyProvider(min_interval=0)

    with patch("astock_data.providers.eastmoney.urlopen", return_value=FakeResponse(payload)):
        result = provider.get_sector_flow_ranking(trade_date="2026-06-16", limit=1)

    assert result.status == "partial"
    assert result.data[0]["main_net_inflow"] == {"amount": 4436057088.0, "unit": "CNY"}
    assert result.data[0]["change_pct"] is None
    assert result.data[0]["up_count"] is None
    assert result.data[0]["down_count"] is None
    assert result.data[0]["leader"] is None
    assert result.data[0]["leader_change"] is None
    assert "missing upstream fields" in result.meta.warnings[0]


def test_board_fund_flow_normalizes_today_rows_and_current_snapshot_metadata():
    payload = """
    {
      "data": {
        "diff": [
          {
            "f12": "BK0477",
            "f14": "半导体",
            "f62": 123400000,
            "f184": 12.5,
            "f3": 1.23,
            "f204": "龙头股份",
            "f66": 90000000,
            "f72": 33400000,
            "f78": -200,
            "f84": -100
          }
        ]
      }
    }
    """
    provider = EastmoneyProvider(min_interval=0)

    with patch(
        "astock_data.providers.eastmoney.urlopen",
        side_effect=_board_response_for_query(
            fs="m:90+t:2",
            fid="f62",
            limit=20,
            fields={"f12", "f14", "f62", "f184", "f3", "f204", "f66", "f72", "f78", "f84"},
            payload=payload,
        ),
    ):
        result = provider.get_board_fund_flow()

    assert result.status == "ok"
    assert result.data == [
        {
            "rank": 1,
            "board_name": "半导体",
            "board_type": "industry",
            "provider_board_code": "BK0477",
            "taxonomy": "eastmoney",
            "period": "today",
            "change_pct": 1.23,
            "main_net_inflow": {"amount": 123400000.0, "unit": "CNY"},
            "main_net_inflow_pct": 12.5,
            "leader": "龙头股份",
            "super_large_net_inflow": {"amount": 90000000.0, "unit": "CNY"},
            "large_net_inflow": {"amount": 33400000.0, "unit": "CNY"},
            "medium_net_inflow": {"amount": -200.0, "unit": "CNY"},
            "small_net_inflow": {"amount": -100.0, "unit": "CNY"},
        }
    ]
    assert result.meta.capability == "board_fund_flow"
    assert result.meta.endpoint == "https://push2.eastmoney.com/api/qt/clist/get"
    assert result.meta.trade_date is None
    assert result.meta.fetched_at is not None
    assert result.meta.as_of == result.meta.fetched_at
    assert result.coverage == {
        "coverage_ratio": 1.0,
        "requested_limit": 20,
        "returned_count": 1,
        "board_type": "industry",
        "period": "today",
        "upstream_total": 1,
        "pages_fetched": 1,
        "requested_limit_satisfied": True,
        "is_full_universe": True,
    }


def test_board_fund_flow_normalizes_five_day_rows_without_size_buckets():
    payload = """
    {
      "data": {
        "diff": [
          {
            "f12": "BK0818",
            "f14": "机器人概念",
            "f164": 987654321,
            "f165": 8.76,
            "f109": -2.34,
            "f257": "领涨机器人"
          }
        ]
      }
    }
    """
    provider = EastmoneyProvider(min_interval=0)

    with patch(
        "astock_data.providers.eastmoney.urlopen",
        side_effect=_board_response_for_query(
            fs="m:90+t:3",
            fid="f164",
            limit=2,
            fields={"f12", "f14", "f164", "f165", "f109", "f257"},
            payload=payload,
        ),
    ):
        result = provider.get_board_fund_flow(board_type="concept", period="5d", limit=2)

    assert result.status == "ok"
    assert result.data == [
        {
            "rank": 1,
            "board_name": "机器人概念",
            "board_type": "concept",
            "provider_board_code": "BK0818",
            "taxonomy": "eastmoney",
            "period": "5d",
            "change_pct": -2.34,
            "main_net_inflow": {"amount": 987654321.0, "unit": "CNY"},
            "main_net_inflow_pct": 8.76,
            "leader": "领涨机器人",
            "super_large_net_inflow": {"amount": None, "unit": "CNY"},
            "large_net_inflow": {"amount": None, "unit": "CNY"},
            "medium_net_inflow": {"amount": None, "unit": "CNY"},
            "small_net_inflow": {"amount": None, "unit": "CNY"},
        }
    ]
    assert result.coverage["board_type"] == "concept"
    assert result.coverage["period"] == "5d"
    assert result.coverage["requested_limit"] == 2
    assert result.coverage["returned_count"] == 1


def test_board_fund_flow_normalizes_ten_day_rows_without_leader_or_size_buckets():
    payload = """
    {
      "data": {
        "diff": [
          {
            "f12": "BK0734",
            "f14": "宁夏板块",
            "f174": -765432100,
            "f175": -6.54,
            "f160": 3.21,
            "f267": "ignored unstable leader"
          }
        ]
      }
    }
    """
    provider = EastmoneyProvider(min_interval=0)

    with patch(
        "astock_data.providers.eastmoney.urlopen",
        side_effect=_board_response_for_query(
            fs="m:90+t:1",
            fid="f174",
            limit=1,
            fields={"f12", "f14", "f174", "f175", "f160"},
            payload=payload,
        ),
    ):
        result = provider.get_board_fund_flow(board_type="region", period="10d", limit=1)

    assert result.status == "ok"
    assert result.data == [
        {
            "rank": 1,
            "board_name": "宁夏板块",
            "board_type": "region",
            "provider_board_code": "BK0734",
            "taxonomy": "eastmoney",
            "period": "10d",
            "change_pct": 3.21,
            "main_net_inflow": {"amount": -765432100.0, "unit": "CNY"},
            "main_net_inflow_pct": -6.54,
            "leader": None,
            "super_large_net_inflow": {"amount": None, "unit": "CNY"},
            "large_net_inflow": {"amount": None, "unit": "CNY"},
            "medium_net_inflow": {"amount": None, "unit": "CNY"},
            "small_net_inflow": {"amount": None, "unit": "CNY"},
        }
    ]
    assert result.coverage["board_type"] == "region"
    assert result.coverage["period"] == "10d"


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"board_type": "theme"}, "board_type"),
        ({"period": "3d"}, "period"),
        ({"limit": 0}, "limit"),
        ({"limit": 1001}, "limit"),
        ({"limit": "20"}, "limit"),
    ],
)
def test_board_fund_flow_rejects_invalid_inputs_before_transport(kwargs, message):
    provider = EastmoneyProvider(min_interval=0)

    with patch("astock_data.providers.eastmoney.urlopen", side_effect=AssertionError("network should not be used")):
        with pytest.raises(ValueError, match=message):
            provider.get_board_fund_flow(**kwargs)


def test_board_fund_flow_returns_empty_for_an_empty_upstream_list():
    provider = EastmoneyProvider(min_interval=0)

    with patch("astock_data.providers.eastmoney.urlopen", return_value=FakeResponse('{"data":{"diff":[]}}')):
        result = provider.get_board_fund_flow(board_type="concept", period="5d", limit=3)

    assert result.status == "empty"
    assert result.data == []
    assert result.coverage == {
        "coverage_ratio": 0.0,
        "requested_limit": 3,
        "returned_count": 0,
        "board_type": "concept",
        "period": "5d",
        "upstream_total": 0,
        "pages_fetched": 1,
        "requested_limit_satisfied": True,
        "is_full_universe": True,
    }


def test_board_fund_flow_returns_unavailable_for_json_error_payload_without_data_diff():
    provider = EastmoneyProvider(min_interval=0)

    with patch(
        "astock_data.providers.eastmoney.urlopen",
        return_value=FakeResponse('{"rc": -1, "msg": "upstream failure"}'),
    ):
        result = provider.get_board_fund_flow(board_type="concept", period="5d", limit=3)

    assert result.status == "unavailable"
    assert result.data == []
    assert result.coverage["requested_limit"] == 3
    assert result.coverage["returned_count"] == 0
    assert result.coverage["board_type"] == "concept"
    assert result.coverage["period"] == "5d"
    assert "data.diff" in result.meta.warnings[0]


def test_board_fund_flow_returns_unavailable_for_json_error_code_with_an_empty_data_diff():
    provider = EastmoneyProvider(min_interval=0)

    with patch(
        "astock_data.providers.eastmoney.urlopen",
        return_value=FakeResponse('{"rc": -1, "msg": "upstream failure", "data": {"diff": []}}'),
    ):
        result = provider.get_board_fund_flow()

    assert result.status == "unavailable"
    assert result.data == []
    assert "upstream error" in result.meta.warnings[0]


def test_board_fund_flow_marks_partial_when_every_selected_row_lacks_a_core_field():
    payload = """
    {
      "data": {
        "diff": [
          {"f12": "BK0001", "f14": "第一板块", "f184": 3.2, "f3": 1.1, "f204": "甲"},
          {"f12": "BK0002", "f14": "第二板块", "f62": "-", "f184": 2.1, "f3": -1.5, "f204": "乙"}
        ]
      }
    }
    """
    provider = EastmoneyProvider(min_interval=0)

    with patch("astock_data.providers.eastmoney.urlopen", return_value=FakeResponse(payload)):
        result = provider.get_board_fund_flow(limit=2)

    assert result.status == "partial"
    assert [row["main_net_inflow"] for row in result.data] == [
        {"amount": None, "unit": "CNY"},
        {"amount": None, "unit": "CNY"},
    ]
    assert "required core fields" in result.meta.warnings[0]


def test_board_fund_flow_marks_partial_when_required_numeric_fields_are_unparseable():
    payload = """
    {
      "data": {
        "diff": [
          {
            "f12": "BK0001",
            "f14": "无效数值板块",
            "f62": "not-a-number",
            "f184": "not-a-percent",
            "f3": "not-a-change",
            "f204": "甲",
            "f66": 1,
            "f72": 2,
            "f78": 3,
            "f84": 4
          }
        ]
      }
    }
    """
    provider = EastmoneyProvider(min_interval=0)

    with patch("astock_data.providers.eastmoney.urlopen", return_value=FakeResponse(payload)):
        result = provider.get_board_fund_flow()

    assert result.status == "partial"
    assert result.data[0]["main_net_inflow"] == {"amount": None, "unit": "CNY"}
    assert result.data[0]["main_net_inflow_pct"] is None
    assert result.data[0]["change_pct"] is None
    assert "f62" in result.meta.warnings[0]
    assert "f184" in result.meta.warnings[0]
    assert "f3" in result.meta.warnings[0]


def test_board_fund_flow_keeps_ok_when_any_selected_row_has_all_core_fields():
    payload = """
    {
      "data": {
        "diff": [
          {"f12": "BK0001", "f14": "完整板块", "f62": 10, "f184": 3.2, "f3": 1.1, "f204": "甲", "f66": 8, "f72": 2, "f78": 1, "f84": -1},
          {"f12": "BK0002", "f14": "缺失板块", "f62": "-", "f184": 2.1, "f3": -1.5, "f204": "乙"}
        ]
      }
    }
    """
    provider = EastmoneyProvider(min_interval=0)

    with patch("astock_data.providers.eastmoney.urlopen", return_value=FakeResponse(payload)):
        result = provider.get_board_fund_flow(limit=2)

    assert result.status == "ok"
    assert result.meta.warnings == []


def test_board_fund_flow_returns_structured_unavailable_for_transport_errors():
    provider = EastmoneyProvider(min_interval=0, max_retries=0)

    with patch("astock_data.providers.eastmoney.urlopen", side_effect=OSError("network down")):
        result = provider.get_board_fund_flow(board_type="region", period="10d", limit=4)

    assert result.status == "unavailable"
    assert result.data == []
    assert result.coverage == {
        "coverage_ratio": 0.0,
        "warnings": ["OSError: network down"],
        "requested_limit": 4,
        "returned_count": 0,
        "board_type": "region",
        "period": "10d",
        "upstream_total": None,
        "pages_fetched": 0,
        "requested_limit_satisfied": False,
        "is_full_universe": False,
    }


def test_stock_flow_history_normalizes_klines():
    payload = '{"data":{"klines":["2026-06-09,10,1,2,3,4","2026-06-08,-,-,-,-,-"]}}'
    provider = EastmoneyProvider(min_interval=0)

    with patch("astock_data.providers.eastmoney.urlopen", return_value=FakeResponse(payload)):
        result = provider.get_stock_flow_history(code="SH600519", trade_date="2026-06-09", lookback=2)

    assert result.status == "ok"
    assert result.data[0]["code"] == "600519"
    assert result.data[0]["trade_date"] == "2026-06-09"
    assert result.data[0]["main_net"] == 10.0
    assert result.data[1]["main_net"] == 0.0


def test_lockup_events_normalize_current_eastmoney_fields():
    payload = """
    {
      "result": {
        "data": [
          {
            "SECURITY_CODE": "688783",
            "SECURITY_NAME_ABBR": "测试股份",
            "FREE_DATE": "2035-10-29 00:00:00",
            "FREE_SHARES_TYPE": "首发原股东限售股份",
            "FREE_SHARES": 403780,
            "ABLE_FREE_SHARES": 30267.0051,
            "FREE_RATIO": 0.25
          }
        ]
      }
    }
    """
    provider = EastmoneyProvider(min_interval=0)

    with patch("astock_data.providers.eastmoney.urlopen", return_value=FakeResponse(payload)):
        result = provider.get_lockup_events(code="688783", trade_date="2035-10-01", forward_days=60)

    assert result.status == "ok"
    assert result.data == [
        {
            "code": "688783",
            "name": "测试股份",
            "unlock_date": "2035-10-29",
            "date": "2035-10-29",
            "type": "首发原股东限售股份",
            "shares": 403780.0,
            "able_shares": 30267.0051,
            "ratio": 0.25,
        }
    ]


def test_lockup_events_keep_legacy_field_fallbacks():
    payload = """
    {
      "result": {
        "data": [
          {
            "SECURITY_CODE": "600519",
            "FREE_DATE": "2026-08-01",
            "LIMITED_STOCK_TYPE": "股权激励限售股份",
            "FREE_SHARES_NUM": 1200
          }
        ]
      }
    }
    """
    provider = EastmoneyProvider(min_interval=0)

    with patch("astock_data.providers.eastmoney.urlopen", return_value=FakeResponse(payload)):
        result = provider.get_lockup_events(code="600519", trade_date="2026-07-15", forward_days=30)

    assert result.data[0]["type"] == "股权激励限售股份"
    assert result.data[0]["shares"] == 1200.0
    assert result.data[0]["able_shares"] == 0.0


def test_http_exception_returns_structured_unavailable():
    provider = EastmoneyProvider(min_interval=0)

    with patch("astock_data.providers.eastmoney.urlopen", side_effect=OSError("network down")):
        result = provider.get_market_dragon_tiger(trade_date="2026-06-09")

    assert result.status == "unavailable"
    assert result.data == []
    assert result.coverage["coverage_ratio"] == 0.0
    assert "network down" in result.coverage["warnings"][0]


def test_transient_http_error_retries_and_returns_data():
    payload = '{"data":{"diff":[{"f12":"BK1036","f14":"半导体","f62":123}]}}'
    provider = EastmoneyProvider(min_interval=0, retry_backoff=0)
    rate_limited = HTTPError("https://example.test", 429, "Too Many Requests", None, None)

    with patch(
        "astock_data.providers.eastmoney.urlopen",
        side_effect=[rate_limited, FakeResponse(payload)],
    ) as mocked_urlopen:
        result = provider.get_sector_flow_ranking(limit=1)

    assert result.status == "partial"
    assert result.data[0]["sector_name"] == "半导体"
    assert mocked_urlopen.call_count == 2


def test_transient_connection_error_retries_and_returns_data():
    payload = '{"data":{"diff":[{"f12":"BK1036","f14":"半导体","f62":123}]}}'
    provider = EastmoneyProvider(min_interval=0, retry_backoff=0)

    with patch(
        "astock_data.providers.eastmoney.urlopen",
        side_effect=[URLError("connection reset"), FakeResponse(payload)],
    ) as mocked_urlopen:
        result = provider.get_sector_flow_ranking(limit=1)

    assert result.status == "partial"
    assert result.data[0]["sector_name"] == "半导体"
    assert mocked_urlopen.call_count == 2


def test_http_403_does_not_retry():
    provider = EastmoneyProvider(min_interval=0, retry_backoff=0)
    forbidden = HTTPError("https://example.test", 403, "Forbidden", None, None)

    with patch("astock_data.providers.eastmoney.urlopen", side_effect=forbidden) as mocked_urlopen:
        result = provider.get_sector_flow_ranking(limit=1)

    assert result.status == "unavailable"
    assert "HTTP Error 403" in result.meta.warnings[0]
    assert mocked_urlopen.call_count == 1


def test_retry_exhaustion_returns_structured_unavailable():
    provider = EastmoneyProvider(min_interval=0, max_retries=2, retry_backoff=0)
    unavailable = HTTPError("https://example.test", 503, "Unavailable", None, None)

    with patch("astock_data.providers.eastmoney.urlopen", side_effect=unavailable) as mocked_urlopen:
        result = provider.get_sector_flow_ranking(limit=1)

    assert result.status == "unavailable"
    assert result.data == []
    assert "HTTP Error 503" in result.meta.warnings[0]
    assert mocked_urlopen.call_count == 3


def test_board_fund_flow_transparently_fetches_more_than_one_page_and_reports_total():
    first_page = [
        {
            "f12": f"BK{index:04d}",
            "f14": f"板块{index}",
            "f62": 1000 - index,
            "f184": 1.0,
            "f3": 0.5,
            "f204": "领涨股",
            "f66": 1,
            "f72": 2,
            "f78": 3,
            "f84": 4,
        }
        for index in range(1, 201)
    ]
    second_page = [
        {
            "f12": "BK0201",
            "f14": "板块201",
            "f62": 799,
            "f184": 1.0,
            "f3": 0.5,
            "f204": "领涨股",
            "f66": 1,
            "f72": 2,
            "f78": 3,
            "f84": 4,
        }
    ]

    def open_page(request, *, timeout):
        query = parse_qs(urlparse(request.full_url).query)
        assert query["pz"] == ["200"]
        page = query["pn"][0]
        rows = first_page if page == "1" else second_page
        return FakeResponse(json.dumps({"data": {"total": 201, "diff": rows}}))

    provider = EastmoneyProvider(min_interval=0)
    with patch("astock_data.providers.eastmoney.urlopen", side_effect=open_page):
        result = provider.get_board_fund_flow(limit=201)

    assert result.status == "ok"
    assert len(result.data) == 201
    assert result.data[-1]["rank"] == 201
    assert result.data[-1]["provider_board_code"] == "BK0201"
    assert result.coverage["upstream_total"] == 201
    assert result.coverage["pages_fetched"] == 2
    assert result.coverage["requested_limit_satisfied"] is True
    assert result.coverage["is_full_universe"] is True
