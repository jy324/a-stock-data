import pytest

from astock_data import AStockDataClient, DataStatus, ProviderResult, SourceMetadata


class FakeEastmoneyProvider:
    def get_stock_intraday_flow(self, **kwargs):
        assert kwargs["code"] == "600519"
        return ProviderResult(
            data=[],
            meta=SourceMetadata(
                provider="fake",
                capability="stock_intraday_flow",
                endpoint="fixture",
                status=DataStatus.EMPTY,
            ),
        )

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

    def get_stock_monitor(self, **kwargs):
        assert kwargs == {"active_only": False}
        return ProviderResult(
            data=[{"code": "920575"}],
            meta=SourceMetadata(
                provider="fake",
                capability="stock_monitor",
                endpoint="fixture",
                status=DataStatus.OK,
            ),
        )

    def get_price_anomalies(self, **kwargs):
        assert kwargs == {"page": 2, "page_size": 10}
        return ProviderResult(
            data=[{"code": "688017"}],
            meta=SourceMetadata(
                provider="fake",
                capability="price_anomalies",
                endpoint="fixture",
                status=DataStatus.OK,
            ),
        )

    def get_stock_reports(self, **kwargs):
        assert kwargs == {"code": "600519", "max_pages": 2}
        return ProviderResult(
            data=[{"report_id": "r1"}],
            meta=SourceMetadata(
                provider="fake",
                capability="stock_reports",
                endpoint="fixture",
                status=DataStatus.OK,
            ),
        )

    def get_industry_reports(self, **kwargs):
        return ProviderResult(
            data=[{"industry_code": kwargs["industry_code"]}],
            meta=SourceMetadata(
                provider="fake",
                capability="industry_reports",
                endpoint="fixture",
                status=DataStatus.OK,
            ),
        )

    def get_stock_dragon_tiger_summary(self, **kwargs):
        assert kwargs == {"code": "600519", "trade_date": "2026-08-05", "lookback": 30}
        return ProviderResult(
            data={"records": [], "seats": {"buy": [], "sell": []}, "institution": {}},
            meta=SourceMetadata(
                provider="fake",
                capability="stock_dragon_tiger_summary",
                endpoint="fixture",
                status=DataStatus.EMPTY,
                trade_date="2026-08-05",
            ),
        )


class FakeTencentProvider:
    def get_realtime_quotes(self, **kwargs):
        assert kwargs == {"codes": ["sh000001", "sz000001"]}
        return ProviderResult(
            data=[{"requested_code": code} for code in kwargs["codes"]],
            meta=SourceMetadata(
                provider="fake",
                capability="realtime_quotes",
                endpoint="fixture",
                status=DataStatus.OK,
            ),
        )


class FakeTdxProvider:
    def get_bars(self, **kwargs):
        assert kwargs == {"symbol": "600519", "frequency": 9, "offset": 2, "market": "std"}
        return ProviderResult(
            data=[{"close": 1500}],
            meta=SourceMetadata(
                provider="fake",
                capability="tdx_bars",
                endpoint="fixture",
                status=DataStatus.OK,
            ),
        )

    def get_quote(self, **kwargs):
        return ProviderResult(
            data=[{"symbol": kwargs["symbol"]}],
            meta=SourceMetadata(
                provider="fake",
                capability="tdx_quote",
                endpoint="fixture",
                status=DataStatus.OK,
            ),
        )

    def get_transactions(self, **kwargs):
        return ProviderResult(
            data=[{"date": kwargs["date"]}],
            meta=SourceMetadata(
                provider="fake",
                capability="tdx_transactions",
                endpoint="fixture",
                status=DataStatus.OK,
            ),
        )


class FakeThsProvider:
    def get_eps_forecast(self, **kwargs):
        assert kwargs == {"code": "688017"}
        return ProviderResult(
            data=[{"year": "2027", "eps_mean": 1.5}],
            meta=SourceMetadata(
                provider="fake",
                capability="eps_forecast",
                endpoint="fixture",
                status=DataStatus.OK,
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
        ({"limit": 1001}, "limit"),
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
    assert client._tencent.provider_name == "tencent"
    assert client._tdx.provider_name == "mootdx"
    assert client._ths.provider_name == "tonghuashun"


def test_stock_facade_normalizes_supported_suffix_before_calling_provider():
    client = AStockDataClient(eastmoney_provider=FakeEastmoneyProvider())

    result = client.get_stock_intraday_flow("600519.SH")

    assert result.status == "empty"


@pytest.mark.parametrize("code", ["SZ600519", "SH000001", "6005190"])
def test_stock_facade_rejects_market_mismatch_indices_and_malformed_codes(code):
    client = AStockDataClient(eastmoney_provider=FakeEastmoneyProvider())

    with pytest.raises(ValueError):
        client.get_stock_intraday_flow(code)


def test_market_risk_facade_forwards_validated_snapshot_options():
    client = AStockDataClient(eastmoney_provider=FakeEastmoneyProvider())

    monitor = client.get_stock_monitor(active_only=False)
    anomalies = client.get_price_anomalies(page=2, page_size=10)

    assert monitor.data == [{"code": "920575"}]
    assert anomalies.data == [{"code": "688017"}]


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("get_stock_monitor", {"active_only": 1}),
        ("get_price_anomalies", {"page": 0}),
        ("get_price_anomalies", {"page_size": 201}),
        ("get_price_anomaly_counts", {"page": True}),
    ],
)
def test_market_risk_facade_rejects_invalid_options_before_provider_selection(method, kwargs):
    client = AStockDataClient()

    with pytest.raises(ValueError):
        getattr(client, method)(**kwargs)


def test_realtime_quote_facade_preserves_explicit_market_inputs():
    client = AStockDataClient(tencent_provider=FakeTencentProvider())

    result = client.get_realtime_quotes(["sh000001", "sz000001"])

    assert [row["requested_code"] for row in result.data] == ["sh000001", "sz000001"]


def test_realtime_quote_facade_rejects_empty_or_invalid_codes_before_provider_selection():
    client = AStockDataClient()

    with pytest.raises(ValueError):
        client.get_realtime_quotes([])
    with pytest.raises(ValueError):
        client.get_realtime_quotes(["SZ600519"])


def test_tdx_facade_normalizes_stock_symbols_and_forwards_market_options():
    client = AStockDataClient(tdx_provider=FakeTdxProvider())

    bars = client.get_tdx_bars("600519.SH", frequency=9, offset=2)
    quote = client.get_tdx_quote("SH600519")
    transactions = client.get_tdx_transactions("600519", trade_date="20260808")

    assert bars.data == [{"close": 1500}]
    assert quote.data == [{"symbol": "600519"}]
    assert transactions.data == [{"date": "20260808"}]


def test_tdx_facade_validates_arguments_before_provider_selection():
    client = AStockDataClient()

    with pytest.raises(ValueError):
        client.get_tdx_bars("SZ600519")
    with pytest.raises(ValueError):
        client.get_tdx_bars("600519", offset=0)
    with pytest.raises(ValueError):
        client.get_tdx_transactions("600519", trade_date="2026-08-08")
    with pytest.raises(ValueError):
        client.get_tdx_transactions("600519", trade_date="20261399")


def test_research_and_dragon_summary_facades_keep_stable_result_shapes():
    client = AStockDataClient(
        eastmoney_provider=FakeEastmoneyProvider(),
        ths_provider=FakeThsProvider(),
    )

    reports = client.get_stock_reports("600519.SH", max_pages=2)
    industry = client.get_industry_reports(industry_code="1238", max_pages=1)
    eps = client.get_eps_forecast("SH688017")
    summary = client.get_stock_dragon_tiger_summary(
        "600519",
        trade_date="2026-08-05",
        lookback=30,
    )

    assert reports.data == [{"report_id": "r1"}]
    assert industry.data == [{"industry_code": "1238"}]
    assert eps.data == [{"year": "2027", "eps_mean": 1.5}]
    assert summary.data["records"] == []


def test_unconfigured_dragon_summary_facade_keeps_dict_shape():
    result = AStockDataClient().get_stock_dragon_tiger_summary(
        "600519",
        trade_date="2026-08-05",
    )

    assert result.status == "unavailable"
    assert isinstance(result.data, dict)
    assert result.data["records"] == []


@pytest.mark.parametrize(
    ("method", "args", "kwargs"),
    [
        ("get_stock_reports", ("SH000001",), {}),
        ("get_stock_reports", ("600519",), {"max_pages": 0}),
        ("get_industry_reports", (), {"max_pages": 21}),
        ("get_eps_forecast", ("SZ600519",), {}),
        ("get_stock_dragon_tiger_summary", ("600519",), {"trade_date": "20260805"}),
    ],
)
def test_research_and_dragon_facades_validate_before_provider_selection(method, args, kwargs):
    client = AStockDataClient()

    with pytest.raises(ValueError):
        getattr(client, method)(*args, **kwargs)
