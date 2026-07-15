from __future__ import annotations

from unittest.mock import patch
from urllib.error import HTTPError, URLError

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
