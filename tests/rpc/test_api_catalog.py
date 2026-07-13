from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from requests.auth import _basic_auth_str

from freqtrade.enums import RunMode
from freqtrade.loggers import setup_logging, setup_logging_pre
from freqtrade.rpc.api_server import ApiServer


_TEST_USER = "FreqTrader"
_TEST_PASS = "SuperSecurePassword1!"
_JWT_SECRET_KEY = "99980ff8fcf77f21ef610adb46b788c505b8483897bc26203b5591eefe0d15"


@contextmanager
def make_catalog_client(default_conf, mocker):
    default_conf["runmode"] = RunMode.WEBSERVER
    default_conf["api_server"] = {
        "enabled": True,
        "listen_ip_address": "127.0.0.1",
        "listen_port": 8080,
        "CORS_origins": ["http://example.com"],
        "jwt_secret_key": _JWT_SECRET_KEY,
        "username": _TEST_USER,
        "password": _TEST_PASS,
    }
    setup_logging_pre()
    setup_logging(default_conf)
    mocker.patch("freqtrade.rpc.api_server.ApiServer.start_api", MagicMock())
    api_server = ApiServer(default_conf)
    try:
        with TestClient(api_server.app) as client:
            yield client
    finally:
        ApiServer.shutdown()


@pytest.fixture
def catalog_client(default_conf, mocker):
    with make_catalog_client(default_conf, mocker) as client:
        yield client


def authenticated_get(client: TestClient, url: str):
    return client.get(
        url,
        headers={
            "Authorization": _basic_auth_str(_TEST_USER, _TEST_PASS),
            "Origin": "http://example.com",
        },
    )


def authenticated_request(client: TestClient, method: str, url: str):
    return client.request(
        method,
        url,
        headers={
            "Authorization": _basic_auth_str(_TEST_USER, _TEST_PASS),
            "Origin": "http://example.com",
        },
    )


def test_catalog_v2_requires_authentication(catalog_client) -> None:
    response = catalog_client.get("/api/v2/catalog")

    assert response.status_code == 401


def test_catalog_products_v2_requires_authentication(catalog_client) -> None:
    response = catalog_client.get(
        "/api/v2/catalog/markets/digital_asset/products"
    )

    assert response.status_code == 401


def test_catalog_v2_returns_the_immutable_default_snapshot(catalog_client) -> None:
    response = authenticated_get(catalog_client, "/api/v2/catalog")

    assert response.status_code == 200
    payload = response.json()
    assert payload["revision_id"] == "builtin-market-catalog-v1"
    assert {market["market_id"] for market in payload["catalog"]["markets"]} == {
        "digital_asset",
        "a_share",
        "hk_stock",
        "us_stock",
    }


def test_catalog_v2_lists_products_and_rejects_unknown_market(catalog_client) -> None:
    response = authenticated_get(
        catalog_client,
        "/api/v2/catalog/markets/digital_asset/products",
    )
    assert response.status_code == 200
    assert {item["product_id"] for item in response.json()["products"]} >= {
        "spot",
        "perpetual",
        "option",
    }

    missing = authenticated_get(
        catalog_client,
        "/api/v2/catalog/markets/unknown/products",
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "unknown_market"


def test_catalog_v2_exposes_only_get_handlers(catalog_client) -> None:
    paths = (
        "/api/v2/catalog",
        "/api/v2/catalog/markets/digital_asset/products",
    )
    for path in paths:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            response = authenticated_request(catalog_client, method, path)

            assert response.status_code == 405

    openapi_paths = catalog_client.app.openapi()["paths"]
    assert set(openapi_paths["/api/v2/catalog"]) == {"get"}
    assert set(openapi_paths["/api/v2/catalog/markets/{market_id}/products"]) == {
        "get"
    }
