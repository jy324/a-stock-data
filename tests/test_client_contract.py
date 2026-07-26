import pytest

from astock_data import AStockDataClient, DataStatus, ProviderResult, SourceMetadata


class FakeEastmoneyProvider:
    def get_stock_flow_history(self, **kwargs):
        assert kwargs["code"] == "600519"
        assert kwargs["lookback"] == 2
        return ProviderResult(
            data=[
                {"code": "000001", "trade_date": "2026-06-09", "value": "wrong"},
                {"code": "600519", "trade_date": "2026-06-07", "value": 7},
                {"code": "600519", "trade_date": "2026-06-09", "value": 9},
                {"code": "600519", "trade_date": "2026-06-08", "value": 8},
            ],
            meta=SourceMetadata(
                provider="fake",
                capability="stock_flow_history",
                endpoint="fixture",
                status=DataStatus.OK,
                trade_date="2026-06-09",
            ),
        )

    def get_lockup_events(self, **kwargs):
        return ProviderResult(
            data=[
                {"code": "600519", "unlock_date": "2026-06-10"},
                {"code": "000001", "unlock_date": "2026-06-10"},
            ],
            meta=SourceMetadata(
                provider="fake",
                capability="lockup_events",
                endpoint="fixture",
                status=DataStatus.OK,
                trade_date="2026-06-09",
            ),
        )


def test_stock_flow_history_filters_code_and_clips_lookback():
    client = AStockDataClient(eastmoney_provider=FakeEastmoneyProvider())

    result = client.get_stock_flow_history("SH600519", trade_date="2026-06-09", lookback=2)

    assert [row["value"] for row in result.data] == [9, 8]
    assert result.coverage["requested_lookback"] == 2
    assert result.coverage["filtered_count"] == 3


def test_lockup_events_filters_to_requested_code():
    client = AStockDataClient(eastmoney_provider=FakeEastmoneyProvider())

    result = client.get_lockup_events("600519", trade_date="2026-06-09")

    assert len(result.data) == 1
    assert result.data[0]["code"] == "600519"


def test_unconfigured_provider_returns_structured_unavailable():
    client = AStockDataClient()

    result = client.get_sector_flow_ranking(trade_date="2026-06-09")

    assert result.status == "unavailable"
    assert result.coverage["coverage_ratio"] == 0.0


def test_unconfigured_board_fund_flow_returns_structured_unavailable_with_request_coverage():
    client = AStockDataClient()

    result = client.get_board_fund_flow(board_type="region", period="10d", limit=4)

    assert result.status == "unavailable"
    assert result.data == []
    assert result.meta.capability == "board_fund_flow"
    assert result.coverage == {
        "coverage_ratio": 0.0,
        "warnings": ["provider is not configured"],
        "requested_limit": 4,
        "returned_count": 0,
        "board_type": "region",
        "period": "10d",
    }


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"board_type": "theme"}, "board_type"),
        ({"period": "3d"}, "period"),
        ({"limit": 0}, "limit"),
        ({"limit": 101}, "limit"),
    ],
)
def test_board_fund_flow_facade_validates_inputs_before_provider_selection(kwargs, message):
    client = AStockDataClient()

    with pytest.raises(ValueError, match=message):
        client.get_board_fund_flow(**kwargs)


def test_from_defaults_builds_configured_eastmoney_provider():
    client = AStockDataClient.from_defaults()

    assert client._eastmoney.provider_name == "eastmoney"
    assert client._cninfo.provider_name == "cninfo"
