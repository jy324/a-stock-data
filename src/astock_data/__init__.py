"""Public facade for the A-share data package."""

from .client import AStockDataClient
from .exceptions import AStockDataError, ProviderUnavailable
from .models import DataStatus, Money, ProviderResult, SourceMetadata
from .tickers import get_market_prefix, normalize_ticker

__all__ = [
    "AStockDataClient",
    "AStockDataError",
    "DataStatus",
    "Money",
    "ProviderResult",
    "ProviderUnavailable",
    "SourceMetadata",
    "get_market_prefix",
    "normalize_ticker",
]
