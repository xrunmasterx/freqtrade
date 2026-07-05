from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from requests.auth import _basic_auth_str

from freqtrade.enums import RunMode
from freqtrade.rpc.api_server import ApiServer


BASE_URI = "/api/v1"
_TEST_USER = "FreqTrader"
_TEST_PASS = "SuperSecurePassword1!"
_JWT_SECRET_KEY = "99980ff8fcf77f21ef610adb46b788c505b8483897bc26203b5591eefe0d15"


@pytest.fixture
def research_client(default_conf, tmp_path, mocker):
    data_root = tmp_path / "research_data" / "a_share"
    data_root.mkdir(parents=True)
    (data_root / "600519.SH-1d.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2026-07-06,1700,1710,1690,1705,100000\n"
        "2026-07-07,1705,1715,1700,1710,200000\n",
        encoding="utf-8",
    )
    default_conf["runmode"] = RunMode.OTHER
    default_conf["user_data_dir"] = tmp_path
    default_conf["research_bots"] = [
        {
            "id": "a-share-local",
            "label": "A Share Local",
            "market": "a_share",
            "data_source": {
                "type": "local_csv",
                "root": "research_data/a_share",
            },
        }
    ]
    default_conf.update(
        {
            "api_server": {
                "enabled": True,
                "listen_ip_address": "127.0.0.1",
                "listen_port": 8080,
                "CORS_origins": ["http://example.com"],
                "jwt_secret_key": _JWT_SECRET_KEY,
                "username": _TEST_USER,
                "password": _TEST_PASS,
            }
        }
    )
    mocker.patch("freqtrade.rpc.api_server.ApiServer.start_api", MagicMock())
    apiserver = ApiServer(default_conf)
    try:
        with TestClient(apiserver.app) as client:
            yield client
    finally:
        ApiServer.shutdown()


def client_get(client: TestClient, url):
    return client.get(
        url,
        headers={
            "Authorization": _basic_auth_str(_TEST_USER, _TEST_PASS),
            "Origin": "http://example.com",
        },
    )


def client_post(client: TestClient, url, data=None):
    if data is None:
        data = {}
    return client.post(
        url,
        json=data,
        headers={
            "Authorization": _basic_auth_str(_TEST_USER, _TEST_PASS),
            "Origin": "http://example.com",
            "content-type": "application/json",
        },
    )


def test_research_bots_returns_public_profile_without_data_root(research_client) -> None:
    response = client_get(research_client, f"{BASE_URI}/research/bots")

    assert response.status_code == 200
    body = response.json()
    assert body["bots"][0]["id"] == "a-share-local"
    assert body["bots"][0]["capabilities"]["live_trade"] is False
    assert "data_root" not in body["bots"][0]


def test_research_instruments_returns_instrument_objects(research_client) -> None:
    response = client_get(
        research_client,
        f"{BASE_URI}/research/instruments?bot_id=a-share-local",
    )

    assert response.status_code == 200
    assert response.json()["instruments"][0]["key"] == "600519.SH"


def test_research_chart_candles_returns_pair_history_shape(research_client) -> None:
    response = client_post(
        research_client,
        f"{BASE_URI}/research/chart_candles",
        data={
            "bot_id": "a-share-local",
            "instrument": "600519.SH",
            "timeframe": "1d",
            "limit": 100,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["pair"] == "600519.SH"
    assert body["length"] == 2
    assert body["meta"]["layers"][0]["source"] == "market"


def test_research_unknown_bot_returns_404(research_client) -> None:
    response = client_get(
        research_client,
        f"{BASE_URI}/research/instruments?bot_id=unknown",
    )

    assert response.status_code == 404
