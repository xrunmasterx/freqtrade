import re
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from requests.auth import _basic_auth_str

from freqtrade.enums import RunMode
from freqtrade.loggers import bufferHandler, setup_logging, setup_logging_pre
from freqtrade.rpc.api_server import ApiServer


BASE_URI = "/api/v1"
_TEST_USER = "FreqTrader"
_TEST_PASS = "SuperSecurePassword1!"
_JWT_SECRET_KEY = "99980ff8fcf77f21ef610adb46b788c505b8483897bc26203b5591eefe0d15"
_PRIVATE_PATH_TOKEN = "PRIVATE_RESEARCH_PATH_TOKEN"


def _clear_log_buffer() -> None:
    bufferHandler.acquire()
    try:
        bufferHandler.buffer.clear()
    finally:
        bufferHandler.release()


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
    (tmp_path / "secret-1d.csv").write_text(
        "date,open,high,low,close,volume\n2026-07-08,424242,424242,424242,424242,424242\n",
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
    setup_logging_pre()
    setup_logging(default_conf)
    _clear_log_buffer()
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


def test_research_chart_candles_missing_ohlcv_does_not_leak_local_path(
    research_client,
    tmp_path,
) -> None:
    response = client_post(
        research_client,
        f"{BASE_URI}/research/chart_candles",
        data={
            "bot_id": "a-share-local",
            "instrument": "600519.SH",
            "timeframe": "5m",
        },
    )

    response_text = response.text
    assert response.status_code == 404
    assert response.json()["detail"] == "Research OHLCV not found for 600519.SH 5m"
    assert str(tmp_path) not in response_text
    assert "research_data" not in response_text
    assert "data_root" not in response_text
    assert "\\" not in response_text
    assert re.search(r"[A-Za-z]:", response_text) is None


def test_research_chart_candles_rejects_path_traversal_without_leaking_secret(
    research_client,
    tmp_path,
) -> None:
    response = client_post(
        research_client,
        f"{BASE_URI}/research/chart_candles",
        data={
            "bot_id": "a-share-local",
            "instrument": "../../secret",
            "timeframe": "1d",
        },
    )

    response_text = response.text
    assert response.status_code == 400
    assert "424242" not in response_text
    assert str(tmp_path) not in response_text
    assert "research_data" not in response_text
    assert "data_root" not in response_text
    assert "\\" not in response_text
    assert re.search(r"[A-Za-z]:", response_text) is None


@pytest.mark.parametrize("timeframe", ["../1d", r"..\1d"])
def test_research_chart_candles_rejects_timeframe_traversal_without_leaking_secret(
    research_client,
    tmp_path,
    timeframe,
) -> None:
    response = client_post(
        research_client,
        f"{BASE_URI}/research/chart_candles",
        data={
            "bot_id": "a-share-local",
            "instrument": "600519.SH",
            "timeframe": timeframe,
        },
    )

    response_text = response.text
    assert response.status_code == 400
    assert "424242" not in response_text
    assert str(tmp_path) not in response_text
    assert "research_data" not in response_text
    assert "data_root" not in response_text
    assert "\\" not in response_text
    assert re.search(r"(?<![A-Za-z])[A-Za-z]:[\\/]", response_text) is None


def test_research_chart_candles_unexpected_error_does_not_leak_detail(
    research_client,
    mocker,
) -> None:
    private_path = rf"G:\private\research_data\data_root\{_PRIVATE_PATH_TOKEN}\600519.SH-1d.csv"
    mocker.patch(
        "freqtrade.rpc.api_server.api_research.build_research_chart_candles_response",
        side_effect=RuntimeError(private_path),
    )

    response = client_post(
        research_client,
        f"{BASE_URI}/research/chart_candles",
        data={
            "bot_id": "a-share-local",
            "instrument": "600519.SH",
            "timeframe": "1d",
        },
    )

    response_text = response.text
    assert response.status_code == 502
    assert response.json()["detail"] == "Research chart data unavailable"
    assert "research_data" not in response_text
    assert "data_root" not in response_text
    assert "\\" not in response_text
    assert re.search(r"[A-Za-z]:", response_text) is None

    logs_response = client_get(research_client, f"{BASE_URI}/logs?limit=20")
    assert logs_response.status_code == 200
    logs_text = "\n".join(record[4] for record in logs_response.json()["logs"])
    assert _PRIVATE_PATH_TOKEN not in logs_text
    assert "research_data" not in logs_text
    assert "data_root" not in logs_text
    assert "\\" not in logs_text
    assert re.search(r"(?<![A-Za-z])[A-Za-z]:[\\/]", logs_text) is None


def test_research_backtest_returns_simple_research_result(research_client) -> None:
    response = client_post(
        research_client,
        f"{BASE_URI}/research/backtest",
        data={
            "bot_id": "a-share-local",
            "instrument": "600519.SH",
            "timeframe": "1d",
            "initial_cash": 100000,
            "strategy": {
                "type": "sma_cross",
                "fast": 1,
                "slow": 2,
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["metrics"]["initial_cash"] == 100000
    assert "return_ratio" in body["metrics"]
    assert body["equity_curve"]
    assert "live_trade" not in body["capability"]


def test_research_backtest_unknown_bot_returns_404(research_client) -> None:
    response = client_post(
        research_client,
        f"{BASE_URI}/research/backtest",
        data={
            "bot_id": "unknown",
            "instrument": "600519.SH",
            "timeframe": "1d",
        },
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    "payload_update",
    [
        {"instrument": "../../secret"},
        {"timeframe": "../1d"},
    ],
)
def test_research_backtest_rejects_path_traversal_without_leaking_secret(
    research_client,
    tmp_path,
    payload_update,
) -> None:
    payload = {
        "bot_id": "a-share-local",
        "instrument": "600519.SH",
        "timeframe": "1d",
    }
    payload.update(payload_update)

    response = client_post(
        research_client,
        f"{BASE_URI}/research/backtest",
        data=payload,
    )

    response_text = response.text
    assert response.status_code == 400
    assert "secret" not in response_text
    assert "424242" not in response_text
    assert str(tmp_path) not in response_text
    assert "research_data" not in response_text
    assert "data_root" not in response_text
    assert "\\" not in response_text
    assert re.search(r"[A-Za-z]:", response_text) is None


def test_research_backtest_unexpected_error_does_not_leak_detail(
    research_client,
    mocker,
) -> None:
    private_path = rf"G:\private\research_data\data_root\{_PRIVATE_PATH_TOKEN}\600519.SH-1d.csv"
    mocker.patch(
        "freqtrade.rpc.api_server.api_research.run_research_backtest",
        side_effect=RuntimeError(private_path),
    )

    response = client_post(
        research_client,
        f"{BASE_URI}/research/backtest",
        data={
            "bot_id": "a-share-local",
            "instrument": "600519.SH",
            "timeframe": "1d",
        },
    )

    response_text = response.text
    assert response.status_code == 502
    assert response.json()["detail"] == "Research backtest unavailable"
    assert _PRIVATE_PATH_TOKEN not in response_text
    assert "research_data" not in response_text
    assert "data_root" not in response_text
    assert "\\" not in response_text
    assert re.search(r"[A-Za-z]:", response_text) is None

    logs_response = client_get(research_client, f"{BASE_URI}/logs?limit=20")
    assert logs_response.status_code == 200
    logs_text = "\n".join(record[4] for record in logs_response.json()["logs"])
    assert _PRIVATE_PATH_TOKEN not in logs_text
    assert "research_data" not in logs_text
    assert "data_root" not in logs_text
    assert "\\" not in logs_text
    assert re.search(r"(?<![A-Za-z])[A-Za-z]:[\\/]", logs_text) is None


def test_research_unknown_bot_returns_404(research_client) -> None:
    response = client_get(
        research_client,
        f"{BASE_URI}/research/instruments?bot_id=unknown",
    )

    assert response.status_code == 404
