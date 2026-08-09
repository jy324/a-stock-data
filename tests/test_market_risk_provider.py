from __future__ import annotations

from unittest.mock import patch
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


def test_stock_monitor_filters_against_beijing_date_and_maps_bse_market():
    payload = """
    [
      {"STKCODE":"920575","STKNAME":"北交样本","MARKET":"B","VALIDATESTARTDATE":"2026-08-01","VALIDATEENDDATE":"2026-08-10","LINK_URL":"em://monitor/1"},
      {"STKCODE":"600000","STKNAME":"已结束","MARKET":"1","VALIDATESTARTDATE":"2026-07-01","VALIDATEENDDATE":"2026-07-31","LINK_URL":""}
    ]
    """
    provider = EastmoneyProvider(min_interval=0)

    with (
        patch("astock_data.providers.eastmoney._cn_today", return_value="2026-08-09"),
        patch("astock_data.providers.eastmoney.urlopen", return_value=FakeResponse(payload)),
    ):
        result = provider.get_stock_monitor(active_only=True)

    assert result.status == "ok"
    assert result.data == [
        {
            "code": "920575",
            "name": "北交样本",
            "market": "BJ",
            "start_date": "2026-08-01",
            "end_date": "2026-08-10",
            "link": "em://monitor/1",
        }
    ]
    assert result.coverage == {
        "coverage_ratio": 1.0,
        "active_only": True,
        "evaluation_date": "2026-08-09",
        "upstream_total": 2,
        "returned_count": 1,
    }


def test_price_anomalies_send_required_h5_params_and_decode_bse_rule():
    payload = """
    {"result":0,"date":"20260808","pages":2,"data":[
      {"c":"920982","n":"北交样本","m":0,"s":8,"e":8,"a":12.3,"x":45.6,"d":10,"o":1}
    ]}
    """

    def open_anomalies(request, *, timeout):
        parsed = urlparse(request.full_url)
        query = parse_qs(parsed.query)
        assert parsed.path.endswith("/price-anomaly/list")
        assert query["team"] == ["h5"]
        assert query["product"] == ["EastMoney"]
        assert query["client"] == ["WAP"]
        assert query["version"] == ["9001"]
        assert query["name"] == ["WAP"]
        assert query["user"] == ["123"]
        assert query["pageSize"] == ["200"]
        assert query["pageNo"] == ["1"]
        return FakeResponse(payload)

    provider = EastmoneyProvider(min_interval=0)
    with patch("astock_data.providers.eastmoney.urlopen", side_effect=open_anomalies):
        result = provider.get_price_anomalies()

    assert result.status == "ok"
    assert result.meta.trade_date == "2026-08-08"
    assert result.data[0] == {
        "code": "920982",
        "name": "北交样本",
        "market": "BJ",
        "change_pct": 12.3,
        "deviation_pct": 45.6,
        "days": 10,
        "board_code": 8,
        "rule_code": 8,
        "rule": "北交所连续10个交易日内3次出现同向异常波动",
        "is_today": True,
    }
    assert result.coverage["page"] == 1
    assert result.coverage["page_size"] == 200
    assert result.coverage["total_pages"] == 2


def test_price_anomaly_counts_decode_market_and_statistics():
    payload = """
    {"result":0,"date":"20260808","pages":1,"data":[
      {"c":"688017","n":"科创样本","m":1,"s":6,"p":88.8,"a":3.2,"t":4,"x":102.5,"d":10}
    ]}
    """
    provider = EastmoneyProvider(min_interval=0)

    with patch("astock_data.providers.eastmoney.urlopen", return_value=FakeResponse(payload)):
        result = provider.get_price_anomaly_counts(page=1, page_size=50)

    assert result.status == "ok"
    assert result.data == [
        {
            "code": "688017",
            "name": "科创样本",
            "market": "SH",
            "price": 88.8,
            "change_pct": 3.2,
            "times": 4,
            "deviation_pct": 102.5,
            "days": 10,
            "board_code": 6,
        }
    ]


def test_price_anomaly_business_rejection_is_unavailable_not_empty():
    provider = EastmoneyProvider(min_interval=0, max_retries=0)

    with patch(
        "astock_data.providers.eastmoney.urlopen",
        return_value=FakeResponse('{"result":1001,"msg":"unknow team","data":[]}'),
    ):
        result = provider.get_price_anomalies(page=2, page_size=10)

    assert result.status == "unavailable"
    assert result.data == []
    assert "unknow team" in result.meta.warnings[0]
