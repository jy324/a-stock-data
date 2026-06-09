"""Public facade for the A-share data package."""

from .client import AStockDataClient
from .exceptions import AStockDataError, ProviderUnavailable
from .models import DataStatus, Money, ProviderResult, SourceMetadata

__all__ = [
    "AStockDataClient",
    "AStockDataError",
    "DataStatus",
    "Money",
    "ProviderResult",
    "ProviderUnavailable",
    "SourceMetadata",
]
