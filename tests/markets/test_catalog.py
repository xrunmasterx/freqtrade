import pytest
from pydantic import ValidationError

from freqtrade.markets import (
    CapabilityName,
    CatalogStatus,
    MarketCatalog,
    MarketDefinition,
    MarketScope,
    MarketType,
    ProductDefinition,
    ProductType,
    VenueDefinition,
    default_catalog_snapshot,
)


def test_market_type_adds_digital_asset_without_changing_legacy_values() -> None:
    assert MarketType.DIGITAL_ASSET == "digital_asset"
    assert MarketType.CONTRACT == "contract"
    assert MarketType.A_SHARE == "a_share"
    assert MarketType.HK_STOCK == "hk_stock"
    assert MarketType.US_STOCK == "us_stock"


def test_catalog_enums_have_the_public_values() -> None:
    assert {status.value for status in CatalogStatus} == {
        "active",
        "planned",
        "disabled",
    }
    assert {product.value for product in ProductType} == {
        "spot",
        "margin",
        "perpetual",
        "delivery_future",
        "option",
        "equity",
        "etf",
        "index",
        "convertible_bond",
        "warrant",
        "cbbc",
    }


def _market() -> MarketDefinition:
    return MarketDefinition(
        market_id=MarketType.DIGITAL_ASSET,
        display_name="Digital Assets",
        status=CatalogStatus.ACTIVE,
    )


def _product() -> ProductDefinition:
    return ProductDefinition(
        market_id=MarketType.DIGITAL_ASSET,
        product_id=ProductType.SPOT,
        display_name="Spot",
        status=CatalogStatus.ACTIVE,
    )


def _venue() -> VenueDefinition:
    return VenueDefinition(
        venue_id="okx",
        market_id=MarketType.DIGITAL_ASSET,
        display_name="OKX",
        status=CatalogStatus.ACTIVE,
        product_ids=(ProductType.SPOT,),
    )


def test_market_catalog_returns_matching_immutable_product_tuple() -> None:
    product = _product()
    catalog = MarketCatalog(
        markets=(_market(),),
        products=(product,),
        venues=(_venue(),),
    )

    assert catalog.schema_version == 1
    assert catalog.products_for(MarketType.DIGITAL_ASSET) == (product,)
    assert catalog.products_for(MarketType.A_SHARE) == ()
    with pytest.raises(ValidationError):
        product.display_name = "Changed"
    with pytest.raises(ValidationError):
        catalog.products = ()


def test_market_catalog_rejects_duplicate_market_definitions() -> None:
    market = _market()

    with pytest.raises(ValidationError, match="duplicate market"):
        MarketCatalog(
            markets=(market, market),
            products=(_product(),),
            venues=(),
        )


def test_market_catalog_rejects_duplicate_product_definitions() -> None:
    product = _product()

    with pytest.raises(ValidationError, match="duplicate product"):
        MarketCatalog(
            markets=(_market(),),
            products=(product, product),
            venues=(),
        )


def test_market_catalog_rejects_duplicate_venue_definitions() -> None:
    venue = _venue()

    with pytest.raises(ValidationError, match="duplicate venue"):
        MarketCatalog(
            markets=(_market(),),
            products=(_product(),),
            venues=(venue, venue),
        )


def test_market_catalog_rejects_product_with_unknown_market() -> None:
    with pytest.raises(ValidationError, match="product references unknown market"):
        MarketCatalog(markets=(), products=(_product(),), venues=())


def test_market_catalog_rejects_venue_with_unknown_market() -> None:
    with pytest.raises(ValidationError, match="venue references unknown market"):
        MarketCatalog(markets=(), products=(), venues=(_venue(),))


def test_market_catalog_rejects_venue_with_unknown_product() -> None:
    with pytest.raises(ValidationError, match="venue references unknown product"):
        MarketCatalog(markets=(_market(),), products=(), venues=(_venue(),))


def test_market_scope_requires_products_and_is_immutable() -> None:
    scope = MarketScope(
        market_id=MarketType.DIGITAL_ASSET,
        product_ids=(ProductType.PERPETUAL,),
        venue_ids=("okx",),
    )

    assert scope.product_ids == (ProductType.PERPETUAL,)
    with pytest.raises(ValidationError, match="at least one product"):
        MarketScope(market_id=MarketType.DIGITAL_ASSET, product_ids=())
    with pytest.raises(ValidationError):
        scope.venue_ids = ("bybit",)


def test_default_catalog_declares_target_markets_without_claiming_live() -> None:
    snapshot = default_catalog_snapshot()

    assert snapshot.revision_id == "builtin-market-catalog-v1"
    assert {market.market_id for market in snapshot.catalog.markets} == {
        MarketType.DIGITAL_ASSET,
        MarketType.A_SHARE,
        MarketType.HK_STOCK,
        MarketType.US_STOCK,
    }
    assert snapshot.capability(
        MarketType.DIGITAL_ASSET,
        ProductType.PERPETUAL,
        CapabilityName.PAPER_TRADING,
    ).allowed is True
    live = snapshot.capability(
        MarketType.DIGITAL_ASSET,
        ProductType.PERPETUAL,
        CapabilityName.LIVE_TRADING,
    )
    assert live.allowed is False
    assert live.reason_code == "live_lane_not_enabled"


def test_planned_product_capability_has_a_reason() -> None:
    snapshot = default_catalog_snapshot()
    decision = snapshot.capability(
        MarketType.US_STOCK,
        ProductType.OPTION,
        CapabilityName.BACKTEST,
    )

    assert decision.allowed is False
    assert decision.reason_code == "market_adapter_not_installed"


def test_cached_snapshot_capability_decisions_are_immutable() -> None:
    snapshot = default_catalog_snapshot()
    policy = snapshot.product_policies[0]

    with pytest.raises(TypeError):
        policy.decisions[CapabilityName.LIVE_TRADING] = policy.decision(
            CapabilityName.LIVE_TRADING
        )
