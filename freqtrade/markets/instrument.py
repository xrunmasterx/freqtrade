from enum import StrEnum

from pydantic import BaseModel


class MarketType(StrEnum):
    CONTRACT = "contract"
    A_SHARE = "a_share"
    HK_STOCK = "hk_stock"
    US_STOCK = "us_stock"


class Instrument(BaseModel):
    key: str
    market: MarketType
    venue: str
    symbol: str
    currency: str
    asset_type: str = "equity"
    display_name: str | None = None


_A_SHARE_VENUES = {
    "SH": "SSE",
    "SZ": "SZSE",
}


def parse_instrument_key(key: str, *, market: MarketType) -> Instrument:
    if market != MarketType.A_SHARE:
        raise ValueError(f"Unsupported market: {market}")

    symbol, separator, suffix = key.partition(".")
    suffix = suffix.upper()

    if (
        separator != "."
        or not symbol.isdigit()
        or len(symbol) != 6
        or suffix not in _A_SHARE_VENUES
    ):
        raise ValueError(f"Invalid A-share instrument key: {key}")

    return Instrument(
        key=f"{symbol}.{suffix}",
        market=market,
        venue=_A_SHARE_VENUES[suffix],
        symbol=symbol,
        currency="CNY",
    )
