"""Strict parsing and market routing for A-share-style ticker inputs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

MarketPrefix = Literal["sh", "sz", "bj"]

SH_INDEX = frozenset({"000300", "000905", "000016", "000688", "000852", "000010"})
_TICKER_RE = re.compile(
    r"^(?:(sh|sz|bj)(\d{6})|(\d{6})(?:\.(sh|sz|bj))?)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedTicker:
    digits: str
    explicit_market: MarketPrefix | None


def normalize_ticker(code: str, *, stock_only: bool = False) -> str:
    """Return a canonical six-digit ticker or raise on ambiguous bad input."""
    return parse_ticker(code, stock_only=stock_only).digits


def get_market_prefix(code: str) -> MarketPrefix:
    """Resolve a supported ticker input to the Tencent-style market prefix."""
    parsed = parse_ticker(code)
    if parsed.explicit_market:
        return parsed.explicit_market
    digits = parsed.digits
    if digits.startswith("92") or digits[:2] in {"43", "83", "87"}:
        return "bj"
    if digits.startswith(("5", "6", "9")) or digits in SH_INDEX:
        return "sh"
    return "sz"


def parse_ticker(code: str, *, stock_only: bool = False) -> ParsedTicker:
    """Parse a ticker while retaining an explicit market used for disambiguation."""
    raw = str(code).strip()
    match = _TICKER_RE.fullmatch(raw)
    if not match:
        raise ValueError(
            f"无法把 {code!r} 解析为 6 位股票代码；支持格式："
            "600519 / SH600519 / 600519.SH（前缀与后缀二选一）"
        )

    digits = match.group(2) or match.group(3)
    raw_market = (match.group(1) or match.group(4) or "").lower()
    market: MarketPrefix | None = raw_market if raw_market in {"sh", "sz", "bj"} else None

    if market:
        if digits.startswith("000"):
            if market == "bj":
                raise ValueError(f"{code!r} 市场标识与号段矛盾：000xxx 不属北交所")
            if stock_only and market == "sh":
                raise ValueError(f"{code!r} 指向沪市指数而非个股，本接口只服务个股")
        else:
            natural_market = _natural_market(digits)
            if market != natural_market:
                raise ValueError(
                    f"{code!r} 的市场标识与号段矛盾：{digits} 属 {natural_market} 市"
                )

    if stock_only and not _is_supported_stock(digits, market):
        raise ValueError(f"{code!r} 不是受支持的沪深北 A 股个股代码")

    return ParsedTicker(digits=digits, explicit_market=market)


def _natural_market(digits: str) -> MarketPrefix:
    if digits.startswith("92") or digits[:2] in {"43", "83", "87"}:
        return "bj"
    if digits.startswith(("5", "6", "9")):
        return "sh"
    return "sz"


def _is_supported_stock(digits: str, explicit_market: MarketPrefix | None) -> bool:
    if digits in SH_INDEX and explicit_market != "sz":
        return False
    return digits.startswith(
        (
            "000",
            "001",
            "002",
            "003",
            "300",
            "301",
            "600",
            "601",
            "603",
            "605",
            "688",
            "689",
            "920",
            "43",
            "83",
            "87",
        )
    )
