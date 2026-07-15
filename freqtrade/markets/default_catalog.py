from functools import cache

from pydantic import Field, model_validator

from freqtrade.markets.capability_policy import (
    CapabilityDecision,
    CapabilityName,
    ProductCapabilityPolicy,
)
from freqtrade.markets.catalog import (
    CatalogModel,
    CatalogStatus,
    MarketCatalog,
    MarketDefinition,
    ProductDefinition,
    ProductType,
    VenueDefinition,
)
from freqtrade.markets.instrument import MarketType


class CatalogSnapshot(CatalogModel):
    revision_id: str = Field(min_length=1, max_length=128)
    catalog: MarketCatalog
    product_policies: tuple[ProductCapabilityPolicy, ...]

    @model_validator(mode="after")
    def validate_product_policies(self) -> "CatalogSnapshot":
        policy_keys = [(policy.market_id, policy.product_id) for policy in self.product_policies]
        policy_key_set = set(policy_keys)
        if len(policy_keys) != len(policy_key_set):
            raise ValueError("duplicate product capability policy")
        known_products = {
            (product.market_id, product.product_id) for product in self.catalog.products
        }
        if any(policy_key not in known_products for policy_key in policy_keys):
            raise ValueError("policy references unknown product")
        if known_products != policy_key_set:
            raise ValueError("product is missing capability policy")
        return self

    def capability(
        self,
        market_id: MarketType,
        product_id: ProductType,
        capability: CapabilityName,
    ) -> CapabilityDecision:
        for policy in self.product_policies:
            if policy.market_id == market_id and policy.product_id == product_id:
                return policy.decision(capability)
        return CapabilityDecision.deny("product_policy_not_declared")


_MARKET_ROWS = (
    (MarketType.DIGITAL_ASSET, "Digital Assets", CatalogStatus.ACTIVE),
    (MarketType.A_SHARE, "A-Share", CatalogStatus.ACTIVE),
    (MarketType.HK_STOCK, "Hong Kong", CatalogStatus.PLANNED),
    (MarketType.US_STOCK, "US Stock", CatalogStatus.PLANNED),
)

_PRODUCT_ROWS = (
    (MarketType.DIGITAL_ASSET, ProductType.SPOT, "Spot", CatalogStatus.ACTIVE),
    (MarketType.DIGITAL_ASSET, ProductType.MARGIN, "Margin", CatalogStatus.PLANNED),
    (MarketType.DIGITAL_ASSET, ProductType.PERPETUAL, "Perpetual", CatalogStatus.ACTIVE),
    (
        MarketType.DIGITAL_ASSET,
        ProductType.DELIVERY_FUTURE,
        "Delivery Future",
        CatalogStatus.PLANNED,
    ),
    (MarketType.DIGITAL_ASSET, ProductType.OPTION, "Option", CatalogStatus.PLANNED),
    (MarketType.A_SHARE, ProductType.EQUITY, "Equity", CatalogStatus.ACTIVE),
    (MarketType.A_SHARE, ProductType.ETF, "ETF", CatalogStatus.ACTIVE),
    (MarketType.A_SHARE, ProductType.INDEX, "Index", CatalogStatus.ACTIVE),
    (
        MarketType.A_SHARE,
        ProductType.CONVERTIBLE_BOND,
        "Convertible Bond",
        CatalogStatus.ACTIVE,
    ),
    (MarketType.A_SHARE, ProductType.OPTION, "Option", CatalogStatus.PLANNED),
    (MarketType.HK_STOCK, ProductType.EQUITY, "Equity", CatalogStatus.PLANNED),
    (MarketType.HK_STOCK, ProductType.ETF, "ETF", CatalogStatus.PLANNED),
    (MarketType.HK_STOCK, ProductType.INDEX, "Index", CatalogStatus.PLANNED),
    (MarketType.HK_STOCK, ProductType.WARRANT, "Warrant", CatalogStatus.PLANNED),
    (MarketType.HK_STOCK, ProductType.CBBC, "CBBC", CatalogStatus.PLANNED),
    (MarketType.HK_STOCK, ProductType.OPTION, "Option", CatalogStatus.PLANNED),
    (MarketType.US_STOCK, ProductType.EQUITY, "Equity", CatalogStatus.PLANNED),
    (MarketType.US_STOCK, ProductType.ETF, "ETF", CatalogStatus.PLANNED),
    (MarketType.US_STOCK, ProductType.INDEX, "Index", CatalogStatus.PLANNED),
    (MarketType.US_STOCK, ProductType.OPTION, "Option", CatalogStatus.PLANNED),
)

_DIGITAL_PRODUCTS = (
    ProductType.SPOT,
    ProductType.MARGIN,
    ProductType.PERPETUAL,
    ProductType.DELIVERY_FUTURE,
    ProductType.OPTION,
)


def _markets() -> tuple[MarketDefinition, ...]:
    return tuple(
        MarketDefinition(
            market_id=market_id,
            display_name=display_name,
            status=status,
        )
        for market_id, display_name, status in _MARKET_ROWS
    )


def _products() -> tuple[ProductDefinition, ...]:
    return tuple(
        ProductDefinition(
            market_id=market_id,
            product_id=product_id,
            display_name=display_name,
            status=status,
        )
        for market_id, product_id, display_name, status in _PRODUCT_ROWS
    )


def _venues() -> tuple[VenueDefinition, ...]:
    venues = tuple(
        VenueDefinition(
            venue_id=venue_id,
            market_id=MarketType.DIGITAL_ASSET,
            display_name=display_name,
            status=CatalogStatus.ACTIVE,
            product_ids=_DIGITAL_PRODUCTS,
        )
        for venue_id, display_name in (
            ("okx", "OKX"),
            ("binance", "Binance"),
            ("bybit", "Bybit"),
            ("gate", "Gate"),
        )
    )
    return (
        *venues,
        VenueDefinition(
            venue_id="bitget",
            market_id=MarketType.DIGITAL_ASSET,
            display_name="Bitget",
            status=CatalogStatus.ACTIVE,
            product_ids=(ProductType.SPOT,),
        ),
    )


def _deny_all(
    market_id: MarketType,
    product_id: ProductType,
    reason_code: str,
) -> ProductCapabilityPolicy:
    return ProductCapabilityPolicy(
        market_id=market_id,
        product_id=product_id,
        decisions={
            capability: CapabilityDecision.deny(reason_code) for capability in CapabilityName
        },
    )


def _policies() -> tuple[ProductCapabilityPolicy, ...]:
    policies: list[ProductCapabilityPolicy] = []
    for market_id, product_id, _display_name, _status in _PRODUCT_ROWS:
        if market_id == MarketType.DIGITAL_ASSET and product_id in {
            ProductType.SPOT,
            ProductType.PERPETUAL,
        }:
            policies.append(
                ProductCapabilityPolicy(
                    market_id=market_id,
                    product_id=product_id,
                    decisions={
                        CapabilityName.MARKET_DATA: CapabilityDecision.allow(),
                        CapabilityName.RESEARCH: CapabilityDecision.allow(),
                        CapabilityName.BACKTEST: CapabilityDecision.allow(),
                        CapabilityName.SIMULATION: CapabilityDecision.allow(),
                        CapabilityName.PAPER_TRADING: CapabilityDecision.allow(),
                        CapabilityName.LIVE_TRADING: CapabilityDecision.deny(
                            "live_lane_not_enabled"
                        ),
                    },
                )
            )
        elif market_id == MarketType.A_SHARE and product_id == ProductType.EQUITY:
            policies.append(
                ProductCapabilityPolicy(
                    market_id=market_id,
                    product_id=product_id,
                    decisions={
                        CapabilityName.MARKET_DATA: CapabilityDecision.allow(),
                        CapabilityName.RESEARCH: CapabilityDecision.allow(),
                        CapabilityName.BACKTEST: CapabilityDecision.allow(),
                        CapabilityName.PAPER_TRADING: CapabilityDecision.deny(
                            "execution_adapter_not_installed"
                        ),
                        CapabilityName.LIVE_TRADING: CapabilityDecision.deny(
                            "execution_adapter_not_installed"
                        ),
                    },
                )
            )
        elif market_id == MarketType.DIGITAL_ASSET and product_id == ProductType.OPTION:
            policies.append(_deny_all(market_id, product_id, "options_adapter_not_installed"))
        elif market_id in {MarketType.HK_STOCK, MarketType.US_STOCK}:
            policies.append(_deny_all(market_id, product_id, "market_adapter_not_installed"))
        else:
            policies.append(_deny_all(market_id, product_id, "product_adapter_not_installed"))
    return tuple(policies)


@cache
def default_catalog_snapshot() -> CatalogSnapshot:
    return CatalogSnapshot(
        revision_id="builtin-market-catalog-v2",
        catalog=MarketCatalog(
            markets=_markets(),
            products=_products(),
            venues=_venues(),
        ),
        product_policies=_policies(),
    )
