from __future__ import annotations

from astock_data.providers.tdx import TdxProvider


class Table:
    def __init__(self, rows):
        self.rows = rows
        self.empty = not rows

    def to_dict(self, orient):
        assert orient == "records"
        return self.rows


class ExtClient:
    def bars(self, **kwargs):
        raise AssertionError("non-std client must not be validated with an A-share bar")

    def quotes(self, **kwargs):
        return Table([{"symbol": kwargs["symbol"], "market": "ext"}])


def test_tdx_non_standard_market_skips_a_share_validation():
    provider = TdxProvider(
        quotes_factory=lambda **kwargs: ExtClient(),
        socket_probe=lambda *_: True,
        servers=[("127.0.0.1", 7709)],
    )

    result = provider.get_quote(symbol="10001234", market="ext")

    assert result.status == "ok"
    assert result.data == [{"symbol": "10001234", "market": "ext"}]


def test_tdx_standard_market_skips_candidate_that_returns_empty_validation_bar():
    class EmptyCandidate:
        def bars(self, **kwargs):
            return Table([])

    class WorkingFallback:
        def bars(self, **kwargs):
            return Table([{"code": "000001"}])

        def quotes(self, **kwargs):
            return Table([{"symbol": kwargs["symbol"], "price": 12.3}])

    def factory(**kwargs):
        return WorkingFallback() if kwargs.get("bestip") else EmptyCandidate()

    provider = TdxProvider(
        quotes_factory=factory,
        socket_probe=lambda *_: True,
        servers=[("127.0.0.1", 7709)],
    )

    result = provider.get_quote(symbol="000001", market="std")

    assert result.status == "ok"
    assert result.data == [{"symbol": "000001", "price": 12.3}]


def test_tdx_bars_and_transactions_convert_tables_to_provider_results():
    class WorkingClient:
        def bars(self, **kwargs):
            if kwargs.get("offset") == 1:
                return Table([{"code": "000001"}])
            return Table([{"close": 12.3, "frequency": kwargs["frequency"]}])

        def transaction(self, **kwargs):
            return Table([{"time": "09:30:00", "price": 12.3, "date": kwargs["date"]}])

    provider = TdxProvider(
        quotes_factory=lambda **kwargs: WorkingClient(),
        socket_probe=lambda *_: True,
        servers=[("127.0.0.1", 7709)],
    )

    bars = provider.get_bars(symbol="600519", frequency=9, offset=2)
    transactions = provider.get_transactions(symbol="600519", date="20260808")

    assert bars.data == [{"close": 12.3, "frequency": 9}]
    assert transactions.data == [{"time": "09:30:00", "price": 12.3, "date": "20260808"}]


def test_tdx_factory_failure_returns_structured_unavailable():
    def unavailable_factory(**kwargs):
        raise ModuleNotFoundError("mootdx is not installed")

    provider = TdxProvider(
        quotes_factory=unavailable_factory,
        socket_probe=lambda *_: True,
        servers=[("127.0.0.1", 7709)],
    )

    result = provider.get_quote(symbol="600519")

    assert result.status == "unavailable"
    assert "mootdx" in result.meta.warnings[0]
