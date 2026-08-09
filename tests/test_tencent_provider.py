from __future__ import annotations

from unittest.mock import patch

from astock_data.providers.tencent import TencentProvider


class FakeResponse:
    def __init__(self, payload: str):
        self._payload = payload.encode("gbk")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._payload


def _quote_line(prefix: str, name: str, code: str, *, price: float, previous_close: float, amount_wan: float) -> str:
    values = [""] * 53
    values[1] = name
    values[2] = code
    values[3] = str(price)
    values[4] = str(previous_close)
    values[5] = str(previous_close + 0.1)
    values[31] = str(price - previous_close)
    values[32] = "1.25"
    values[33] = str(price + 1)
    values[34] = str(price - 1)
    values[37] = str(amount_wan)
    values[38] = "2.5"
    values[39] = "18.2"
    values[43] = "3.6"
    values[44] = "123.4"
    values[45] = "234.5"
    values[46] = "2.1"
    values[47] = str(price + 10)
    values[48] = str(price - 10)
    values[49] = "1.3"
    values[52] = "20.0"
    return f'v_{prefix}="{"~".join(values)}";'


def test_realtime_quotes_preserve_explicit_market_identity_and_route_920_to_bse():
    payload = "\n".join(
        [
            _quote_line("sh000001", "上证指数", "000001", price=3600, previous_close=3590, amount_wan=100),
            _quote_line("sz000001", "平安银行", "000001", price=12, previous_close=11.8, amount_wan=200),
            _quote_line("bj920982", "北交样本", "920982", price=131.7, previous_close=130, amount_wan=30),
        ]
    )

    def open_quotes(request, *, timeout):
        assert request.full_url.endswith("/q=sh000001,sz000001,bj920982")
        return FakeResponse(payload)

    provider = TencentProvider()
    with patch("astock_data.providers.tencent.urlopen", side_effect=open_quotes):
        result = provider.get_realtime_quotes(codes=["sh000001", "sz000001", "920982"])

    assert result.status == "ok", result.meta.warnings
    assert [(row["requested_code"], row["market"], row["name"]) for row in result.data] == [
        ("sh000001", "SH", "上证指数"),
        ("sz000001", "SZ", "平安银行"),
        ("920982", "BJ", "北交样本"),
    ]
    assert result.data[1]["turnover_amount"] == {"amount": 2_000_000.0, "unit": "CNY"}
    assert result.data[1]["market_cap"] == {"amount": 23_450_000_000.0, "unit": "CNY"}
    assert result.coverage["requested_count"] == 3
    assert result.coverage["returned_count"] == 3


def test_realtime_quotes_mark_old_bse_zero_turnover_quote_stale():
    payload = _quote_line("bj832982", "旧码样本", "832982", price=112.6, previous_close=112.6, amount_wan=0)
    provider = TencentProvider()

    with patch("astock_data.providers.tencent.urlopen", return_value=FakeResponse(payload)):
        result = provider.get_realtime_quotes(codes=["832982"])

    assert result.status == "stale"
    assert result.data[0]["is_stale"] is True
    assert "920" in result.data[0]["stale_reason"]


def test_realtime_quotes_mark_missing_requested_code_partial():
    payload = _quote_line("sh600519", "贵州茅台", "600519", price=1500, previous_close=1490, amount_wan=1000)
    provider = TencentProvider()

    with patch("astock_data.providers.tencent.urlopen", return_value=FakeResponse(payload)):
        result = provider.get_realtime_quotes(codes=["600519", "000001"])

    assert result.status == "partial"
    assert result.coverage["missing_codes"] == ["000001"]
