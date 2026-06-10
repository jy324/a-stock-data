from __future__ import annotations

import os

import pytest

from astock_data import AStockDataClient


@pytest.mark.network
@pytest.mark.skipif(
    os.environ.get("ASTOCK_DATA_LIVE_SMOKE") != "1",
    reason="set ASTOCK_DATA_LIVE_SMOKE=1 to call live upstream providers",
)
def test_live_sector_flow_ranking_smoke():
    client = AStockDataClient.from_defaults()

    result = client.get_sector_flow_ranking(limit=3)

    assert result.status in {"ok", "partial"}
    assert result.data
    assert result.coverage.get("coverage_ratio", 0) > 0
