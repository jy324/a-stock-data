from __future__ import annotations

from typing import ClassVar
from unittest.mock import patch

from astock_data.providers.ths import ThsProvider


class FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._payload


class FakeTable:
    columns: ClassVar[list[str]] = ["年度", "预测机构数", "最小值", "均值", "最大值"]

    def to_dict(self, orient):
        assert orient == "records"
        return [{"年度": "2027", "预测机构数": 12, "最小值": 1.1, "均值": 1.5, "最大值": 1.9}]


def test_eps_forecast_normalizes_ticker_and_maps_dataframe_rows_to_stable_dicts():
    def read_tables(html):
        assert "每股收益" in html.read()
        return [FakeTable()]

    def open_worth(request, *, timeout):
        assert request.full_url == "https://basic.10jqka.com.cn/new/688017/worth.html"
        return FakeResponse("<table>每股收益</table>".encode("gbk"))

    provider = ThsProvider(min_interval=0, table_reader=read_tables)
    with patch("astock_data.providers.ths.urlopen", side_effect=open_worth):
        result = provider.get_eps_forecast(code="688017.SH")

    assert result.status == "ok"
    assert result.data == [
        {
            "year": "2027",
            "institution_count": 12,
            "eps_min": 1.1,
            "eps_mean": 1.5,
            "eps_max": 1.9,
        }
    ]
    assert result.meta.unit_map == {"eps_min": "CNY/share", "eps_mean": "CNY/share", "eps_max": "CNY/share"}


def test_eps_forecast_missing_reports_extra_returns_unavailable():
    def missing_reader(html):
        raise ModuleNotFoundError("pandas is not installed; install astock-data[reports]")

    provider = ThsProvider(min_interval=0, table_reader=missing_reader)
    with patch("astock_data.providers.ths.urlopen", return_value=FakeResponse(b"<table></table>")):
        result = provider.get_eps_forecast(code="688017")

    assert result.status == "unavailable"
    assert "astock-data[reports]" in result.meta.warnings[0]
