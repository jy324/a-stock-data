import pytest

from astock_data import get_market_prefix, normalize_ticker


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("600519", "600519"),
        ("SH600519", "600519"),
        ("600519.sh", "600519"),
        ("bj920982", "920982"),
        ("SZ000001", "000001"),
    ],
)
def test_normalize_ticker_accepts_only_supported_complete_formats(raw, expected):
    assert normalize_ticker(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "6005190",
        "foo600519bar",
        "SH600519.SZ",
        "SZ600519",
        "BJ600519",
        "BJ000001",
    ],
)
def test_normalize_ticker_rejects_truncation_and_market_mismatch(raw):
    with pytest.raises(ValueError):
        normalize_ticker(raw)


@pytest.mark.parametrize("raw", ["SH000001", "000016.SH"])
def test_stock_only_normalization_rejects_explicit_shanghai_indices(raw):
    with pytest.raises(ValueError, match="指数"):
        normalize_ticker(raw, stock_only=True)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("920982", "bj"),
        ("832982", "bj"),
        ("600519", "sh"),
        ("510300", "sh"),
        ("000300", "sh"),
        ("000001", "sz"),
        ("sz000016", "sz"),
    ],
)
def test_get_market_prefix_routes_ambiguous_and_bse_codes(raw, expected):
    assert get_market_prefix(raw) == expected
