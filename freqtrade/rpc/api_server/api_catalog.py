from fastapi import APIRouter
from fastapi.exceptions import HTTPException

from freqtrade.markets import MarketType, default_catalog_snapshot
from freqtrade.rpc.api_server.api_schemas import (
    CatalogProductsResponse,
    CatalogResponse,
)


router = APIRouter()


@router.get("/catalog", response_model=CatalogResponse)
def catalog() -> CatalogResponse:
    return CatalogResponse(**default_catalog_snapshot().model_dump())


@router.get(
    "/catalog/markets/{market_id}/products",
    response_model=CatalogProductsResponse,
)
def catalog_products(market_id: str) -> CatalogProductsResponse:
    try:
        resolved_market = MarketType(market_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_market", "message": "Unknown market."},
        ) from None
    snapshot = default_catalog_snapshot()
    products = snapshot.catalog.products_for(resolved_market)
    if not any(
        market.market_id == resolved_market
        for market in snapshot.catalog.markets
    ):
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_market", "message": "Unknown market."},
        )
    return CatalogProductsResponse(
        market_id=resolved_market,
        products=products,
    )
