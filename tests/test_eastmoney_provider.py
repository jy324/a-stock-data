from __future__ import annotations

from unittest.mock import patch

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


def test_http_exception_returns_structured_unavailable():
    provider = EastmoneyProvider(min_interval=0)

    with patch("astock_data.providers.eastmoney.urlopen", side_effect=OSError("network down")):
        result = provider.get_market_dragon_tiger(trade_date="2026-06-09")

    assert result.status == "unavailable"
    assert result.data == []
    assert result.coverage["coverage_ratio"] == 0.0
    assert "network down" in result.coverage["warnings"][0]
