"""Package-level exceptions."""


class AStockDataError(RuntimeError):
    """Base exception for astock_data."""


class ProviderUnavailable(AStockDataError):
    """Raised when a configured provider cannot serve a capability."""
