"""Stable domain models exposed by astock_data."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Generic, Literal, TypeVar


class DataStatus(str, Enum):
    OK = "ok"
    EMPTY = "empty"
    PARTIAL = "partial"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


Currency = Literal["CNY"]


@dataclass(frozen=True)
class Money:
    amount: Decimal | float | None
    currency: Currency = "CNY"

    def to_dict(self) -> dict[str, Any]:
        amount: Any = self.amount
        if isinstance(amount, Decimal):
            amount = float(amount)
        return {"amount": amount, "currency": self.currency}


@dataclass(frozen=True)
class SourceMetadata:
    provider: str
    capability: str
    endpoint: str
    status: DataStatus | str
    trade_date: str | None = None
    as_of: str | None = None
    fetched_at: str | None = None
    is_partial: bool = False
    unit_map: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    schema_version: str = "v1"

    def __post_init__(self) -> None:
        if self.fetched_at is None:
            object.__setattr__(self, "fetched_at", datetime.now().isoformat())
        if self.as_of is None:
            object.__setattr__(self, "as_of", self.fetched_at)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = str(self.status.value if isinstance(self.status, DataStatus) else self.status)
        return payload


T = TypeVar("T")


@dataclass(frozen=True)
class ProviderResult(Generic[T]):
    data: T | None
    meta: SourceMetadata
    coverage: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        status = self.meta.status
        return str(status.value if isinstance(status, DataStatus) else status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "data": self.data,
            "meta": self.meta.to_dict(),
            "coverage": dict(self.coverage or {}),
        }
