from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from freqtrade.markets.instrument import MarketType


class CatalogStatus(StrEnum):
    ACTIVE = "active"
    PLANNED = "planned"
    DISABLED = "disabled"


class ProductType(StrEnum):
    SPOT = "spot"
    MARGIN = "margin"
    PERPETUAL = "perpetual"
    DELIVERY_FUTURE = "delivery_future"
    OPTION = "option"
    EQUITY = "equity"
    ETF = "etf"
    INDEX = "index"
    CONVERTIBLE_BOND = "convertible_bond"
    WARRANT = "warrant"
    CBBC = "cbbc"


class CatalogModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class MarketDefinition(CatalogModel):
    market_id: MarketType
    display_name: str = Field(min_length=1)
    status: CatalogStatus


class ProductDefinition(CatalogModel):
    market_id: MarketType
    product_id: ProductType
    display_name: str = Field(min_length=1)
    status: CatalogStatus


class VenueDefinition(CatalogModel):
    venue_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    market_id: MarketType
    display_name: str = Field(min_length=1)
    status: CatalogStatus
    product_ids: tuple[ProductType, ...]


class MarketScope(CatalogModel):
    market_id: MarketType
    product_ids: tuple[ProductType, ...]
    venue_ids: tuple[str, ...] = ()
    instrument_keys: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_products(self) -> "MarketScope":
        if not self.product_ids:
            raise ValueError("market scope requires at least one product")
        return self


class MarketCatalog(CatalogModel):
    schema_version: Literal[1] = 1
    markets: tuple[MarketDefinition, ...]
    products: tuple[ProductDefinition, ...]
    venues: tuple[VenueDefinition, ...]

    @model_validator(mode="after")
    def validate_references(self) -> "MarketCatalog":
        market_ids = [market.market_id for market in self.markets]
        if len(market_ids) != len(set(market_ids)):
            raise ValueError("duplicate market definition")
        product_keys = [
            (product.market_id, product.product_id) for product in self.products
        ]
        if len(product_keys) != len(set(product_keys)):
            raise ValueError("duplicate product definition")
        known_markets = set(market_ids)
        known_products = set(product_keys)
        for product in self.products:
            if product.market_id not in known_markets:
                raise ValueError("product references unknown market")
        venue_ids = [venue.venue_id for venue in self.venues]
        if len(venue_ids) != len(set(venue_ids)):
            raise ValueError("duplicate venue definition")
        for venue in self.venues:
            if venue.market_id not in known_markets:
                raise ValueError("venue references unknown market")
            for product_id in venue.product_ids:
                if (venue.market_id, product_id) not in known_products:
                    raise ValueError("venue references unknown product")
        return self

    def products_for(self, market_id: MarketType) -> tuple[ProductDefinition, ...]:
        return tuple(product for product in self.products if product.market_id == market_id)
