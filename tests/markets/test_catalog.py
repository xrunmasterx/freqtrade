import json

import pytest
from pydantic import ValidationError

from freqtrade.markets import (
    CapabilityDecision,
    CapabilityName,
    CatalogSnapshot,
    CatalogStatus,
    MarketCatalog,
    MarketDefinition,
    MarketScope,
    MarketType,
    ProductCapabilityPolicy,
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


def _snapshot_with(
    *,
    revision_id: str = "test-revision",
    product_policies: tuple[ProductCapabilityPolicy, ...] | None = None,
) -> CatalogSnapshot:
    default = default_catalog_snapshot()
    return CatalogSnapshot(
        revision_id=revision_id,
        catalog=default.catalog,
        product_policies=(
            default.product_policies if product_policies is None else product_policies
        ),
    )


def _unknown_product_policy() -> ProductCapabilityPolicy:
    return ProductCapabilityPolicy(
        market_id=MarketType.A_SHARE,
        product_id=ProductType.WARRANT,
        decisions={},
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


def test_catalog_snapshot_rejects_duplicate_policy_before_other_reference_errors() -> None:
    policies = default_catalog_snapshot().product_policies
    invalid_policies = (
        policies[0],
        *policies[2:],
        policies[0],
        _unknown_product_policy(),
    )

    with pytest.raises(ValidationError) as exc_info:
        _snapshot_with(product_policies=invalid_policies)

    assert str(exc_info.value.errors()[0]["ctx"]["error"]) == (
        "duplicate product capability policy"
    )


def test_catalog_snapshot_rejects_dangling_policy_before_missing_policy() -> None:
    policies = default_catalog_snapshot().product_policies
    invalid_policies = (*policies[1:], _unknown_product_policy())

    with pytest.raises(ValidationError) as exc_info:
        _snapshot_with(product_policies=invalid_policies)

    assert str(exc_info.value.errors()[0]["ctx"]["error"]) == ("policy references unknown product")


def test_catalog_snapshot_rejects_product_without_policy() -> None:
    policies = default_catalog_snapshot().product_policies

    with pytest.raises(ValidationError) as exc_info:
        _snapshot_with(product_policies=policies[1:])

    assert str(exc_info.value.errors()[0]["ctx"]["error"]) == (
        "product is missing capability policy"
    )


@pytest.mark.parametrize("revision_id", ["", "r" * 129])
def test_catalog_snapshot_rejects_revision_id_outside_sql_boundaries(
    revision_id: str,
) -> None:
    with pytest.raises(ValidationError):
        _snapshot_with(revision_id=revision_id)


def test_catalog_snapshot_accepts_revision_id_at_sql_boundary() -> None:
    revision_id = "r" * 128

    assert _snapshot_with(revision_id=revision_id).revision_id == revision_id


@pytest.mark.parametrize(
    "reason_code",
    [
        "",
        "contains spaces",
        "contains\nnewline",
        "Uppercase",
        "hyphen-code",
        "1_starts_with_digit",
    ],
)
def test_denied_capability_rejects_unstable_reason_code(reason_code: str) -> None:
    with pytest.raises(ValidationError):
        CapabilityDecision.deny(reason_code)


def test_denied_capability_accepts_snake_case_reason_code() -> None:
    decision = CapabilityDecision.deny("stable_reason_2")

    assert decision.reason_code == "stable_reason_2"


def test_default_catalog_v2_adds_spot_only_bitget_without_claiming_live() -> None:
    snapshot = default_catalog_snapshot()

    assert default_catalog_snapshot() is snapshot
    assert snapshot.revision_id == "builtin-market-catalog-v2"
    assert len(snapshot.catalog.products) == 20
    assert len(snapshot.product_policies) == 20
    assert {market.market_id for market in snapshot.catalog.markets} == {
        MarketType.DIGITAL_ASSET,
        MarketType.A_SHARE,
        MarketType.HK_STOCK,
        MarketType.US_STOCK,
    }
    bitget = next(venue for venue in snapshot.catalog.venues if venue.venue_id == "bitget")
    assert bitget.status is CatalogStatus.ACTIVE
    assert bitget.product_ids == (ProductType.SPOT,)
    assert (
        snapshot.capability(
            MarketType.DIGITAL_ASSET,
            ProductType.SPOT,
            CapabilityName.PAPER_TRADING,
        ).allowed
        is True
    )
    spot_live = snapshot.capability(
        MarketType.DIGITAL_ASSET,
        ProductType.SPOT,
        CapabilityName.LIVE_TRADING,
    )
    assert spot_live.allowed is False
    assert spot_live.reason_code == "live_lane_not_enabled"
    assert (
        snapshot.capability(
            MarketType.DIGITAL_ASSET,
            ProductType.PERPETUAL,
            CapabilityName.PAPER_TRADING,
        ).allowed
        is True
    )
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
        policy.decisions[CapabilityName.LIVE_TRADING] = policy.decision(CapabilityName.LIVE_TRADING)


def test_cached_snapshot_capability_decisions_are_json_serializable() -> None:
    snapshot = default_catalog_snapshot()
    spot_policy_index = next(
        index
        for index, policy in enumerate(snapshot.product_policies)
        if policy.market_id == MarketType.DIGITAL_ASSET and policy.product_id == ProductType.SPOT
    )
    expected_decisions = {
        "market_data": {"allowed": True, "reason_code": None},
        "research": {"allowed": True, "reason_code": None},
        "backtest": {"allowed": True, "reason_code": None},
        "simulation": {"allowed": True, "reason_code": None},
        "paper_trading": {"allowed": True, "reason_code": None},
        "live_trading": {
            "allowed": False,
            "reason_code": "live_lane_not_enabled",
        },
    }

    dumped_decisions = snapshot.model_dump(mode="json")["product_policies"][spot_policy_index][
        "decisions"
    ]
    json_decisions = json.loads(snapshot.model_dump_json())["product_policies"][spot_policy_index][
        "decisions"
    ]

    assert dumped_decisions == expected_decisions
    assert json_decisions == expected_decisions
