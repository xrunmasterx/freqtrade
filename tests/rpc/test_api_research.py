import re
from contextlib import contextmanager
from unittest.mock import MagicMock

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from requests.auth import _basic_auth_str

from freqtrade.enums import RunMode
from freqtrade.loggers import bufferHandler, setup_logging, setup_logging_pre
from freqtrade.research import create_research_data_source
from freqtrade.rpc.api_server import ApiServer
from freqtrade.rpc.api_server.api_schemas import (
    ChartAxisMeta,
    ChartLayerMeta,
    ChartSeriesCoverage,
    ChartSeriesMeta,
    ResearchBacktestRequest,
    ResearchChartCandlesRequest,
    ResearchSideLayerSelection,
)


BASE_URI = "/api/v1"
_TEST_USER = "FreqTrader"
_TEST_PASS = "SuperSecurePassword1!"
_JWT_SECRET_KEY = "99980ff8fcf77f21ef610adb46b788c505b8483897bc26203b5591eefe0d15"
_PRIVATE_PATH_TOKEN = "PRIVATE_RESEARCH_PATH_TOKEN"
_USE_DEFAULT_RESEARCH_BOTS = object()
_MISSING_RESEARCH_BOTS = object()
_RESEARCH_DISABLED_DETAIL = {
    "code": "research_api_disabled",
    "message": "Research API is not enabled.",
}


def _clear_log_buffer() -> None:
    bufferHandler.acquire()
    try:
        bufferHandler.buffer.clear()
    finally:
        bufferHandler.release()


@contextmanager
def make_research_client(
    default_conf,
    tmp_path,
    mocker,
    *,
    runmode=RunMode.WEBSERVER,
    research_bots=_USE_DEFAULT_RESEARCH_BOTS,
    raise_server_exceptions=True,
):
    data_root = tmp_path / "research_data" / "a_share"
    data_root.mkdir(parents=True)
    (data_root / "600519.SH-1d.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2026-07-06,1700,1710,1690,1705,100000\n"
        "2026-07-07,1705,1715,1700,1710,200000\n"
        "2026-07-08,1710,1720,1705,1715,300000\n",
        encoding="utf-8",
    )
    (data_root / "000001.SZ-bad.csv").write_text(
        "date,open,high,low,close,volume\n2026-07-08,1,1,1,1,1\n",
        encoding="utf-8",
    )
    (tmp_path / "secret-1d.csv").write_text(
        "date,open,high,low,close,volume\n2026-07-08,424242,424242,424242,424242,424242\n",
        encoding="utf-8",
    )
    default_conf["runmode"] = runmode
    default_conf["user_data_dir"] = tmp_path
    if research_bots is _USE_DEFAULT_RESEARCH_BOTS:
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
    elif research_bots is not _MISSING_RESEARCH_BOTS:
        default_conf["research_bots"] = research_bots
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
        with TestClient(
            apiserver.app,
            raise_server_exceptions=raise_server_exceptions,
        ) as client:
            yield client
    finally:
        ApiServer.shutdown()


@pytest.fixture
def research_client(default_conf, tmp_path, mocker):
    with make_research_client(default_conf, tmp_path, mocker) as client:
        yield client


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


def client_post_raw_json(client: TestClient, url, content: str):
    return client.post(
        url,
        content=content,
        headers={
            "Authorization": _basic_auth_str(_TEST_USER, _TEST_PASS),
            "Origin": "http://example.com",
            "content-type": "application/json",
        },
    )


def _write_feature_backtest_side_data(tmp_path) -> None:
    meta_root = tmp_path / "research_data" / "a_share_meta"
    (meta_root / "calendar").mkdir(parents=True)
    (meta_root / "features" / "fund_flow_daily").mkdir(parents=True)
    (meta_root / ".manifests").mkdir(parents=True)
    (meta_root / "calendar" / "trade_dates.csv").write_text(
        "date,is_open,source\n"
        "2026-07-06,1,test\n"
        "2026-07-07,1,test\n"
        "2026-07-08,1,test\n",
        encoding="utf-8",
    )
    (meta_root / "features" / "fund_flow_daily" / "600519.SH.csv").write_text(
        "date,instrument,main_net_inflow,large_net_inflow,medium_net_inflow,"
        "small_net_inflow,source,publish_time,ingest_time\n"
        "2026-07-06,600519.SH,1000,800,100,100,eastmoney,"
        "2026-07-06T14:30:00+08:00,2026-07-06T16:00:00+08:00\n",
        encoding="utf-8",
    )
    (meta_root / ".manifests" / "fund-flow.json").write_text(
        (
            '{"run_id":"phase3b-api-fixture","provider":"akshare",'
            '"provider_version":"1.17.0","created_at":"2026-07-07T20:30:00+08:00",'
            '"files":[{"path":"features/fund_flow_daily/600519.SH.csv",'
            '"dataset":"fund_flow_daily","kind":"feature","rows":1,'
            '"start":"2026-07-06","stop":"2026-07-06","status":"ok","warnings":[]}]}'
        ),
        encoding="utf-8",
    )


def test_research_bots_returns_public_profile_without_data_root(research_client) -> None:
    response = client_get(research_client, f"{BASE_URI}/research/bots")

    assert response.status_code == 200
    body = response.json()
    assert body["bots"][0]["id"] == "a-share-local"
    assert body["bots"][0]["capabilities"]["live_trade"] is False
    assert "data_root" not in body["bots"][0]


def test_research_bots_requires_research_webserver_mode(default_conf, tmp_path, mocker) -> None:
    with make_research_client(default_conf, tmp_path, mocker, runmode=RunMode.OTHER) as client:
        response = client_get(client, f"{BASE_URI}/research/bots")

    assert response.status_code == 503
    assert response.json()["detail"] == _RESEARCH_DISABLED_DETAIL


@pytest.mark.parametrize("research_bots", [_MISSING_RESEARCH_BOTS, []])
def test_research_bots_requires_configured_research_bots(
    default_conf,
    tmp_path,
    mocker,
    research_bots,
) -> None:
    with make_research_client(
        default_conf, tmp_path, mocker, research_bots=research_bots
    ) as client:
        response = client_get(client, f"{BASE_URI}/research/bots")

    assert response.status_code == 503
    assert response.json()["detail"] == _RESEARCH_DISABLED_DETAIL


def test_research_bots_maps_malformed_config_to_bad_request(default_conf, tmp_path, mocker) -> None:
    with make_research_client(
        default_conf,
        tmp_path,
        mocker,
        research_bots=[
            {
                "id": "a-share-local",
                "label": "A Share Local",
                "market": "a_share",
            }
        ],
        raise_server_exceptions=False,
    ) as client:
        response = client_get(client, f"{BASE_URI}/research/bots")

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": "invalid_research_config",
        "message": "Missing research_bots[0].data_source",
    }


def test_research_bots_maps_unsupported_market_to_bad_request(
    default_conf, tmp_path, mocker
) -> None:
    with make_research_client(
        default_conf,
        tmp_path,
        mocker,
        research_bots=[
            {
                "id": "hk-local",
                "label": "HK Local",
                "market": "hk_stock",
                "data_source": {
                    "type": "local_csv",
                    "root": "research_data/hk",
                },
            }
        ],
    ) as client:
        response = client_get(client, f"{BASE_URI}/research/bots")

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": "invalid_research_config",
        "message": "Unsupported research_bots[0].market: hk_stock",
    }


def test_research_instruments_maps_malformed_config_to_bad_request(
    default_conf,
    tmp_path,
    mocker,
) -> None:
    with make_research_client(
        default_conf,
        tmp_path,
        mocker,
        research_bots=[
            {
                "id": "a-share-local",
                "label": "A Share Local",
                "market": "a_share",
                "data_source": {
                    "type": "local_csv",
                },
            }
        ],
    ) as client:
        response = client_get(
            client,
            f"{BASE_URI}/research/instruments?bot_id=a-share-local",
        )

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": "invalid_research_config",
        "message": "Invalid research_bots[0].data_source.root",
    }


def test_research_instruments_returns_instrument_objects(research_client) -> None:
    response = client_get(
        research_client,
        f"{BASE_URI}/research/instruments?bot_id=a-share-local",
    )

    assert response.status_code == 200
    assert response.json()["instruments"][0]["key"] == "600519.SH"


def test_research_instruments_uses_data_source_factory(research_client, mocker) -> None:
    factory = mocker.patch(
        "freqtrade.rpc.api_server.api_research.create_research_data_source",
        wraps=create_research_data_source,
    )

    response = client_get(
        research_client,
        f"{BASE_URI}/research/instruments?bot_id=a-share-local",
    )

    assert response.status_code == 200
    assert factory.call_count == 1


def test_research_instruments_returns_available_timeframes(research_client) -> None:
    response = client_get(
        research_client,
        f"{BASE_URI}/research/instruments?bot_id=a-share-local",
    )

    assert response.status_code == 200
    instruments = response.json()["instruments"]
    assert instruments == [
        {
            "key": "600519.SH",
            "market": "a_share",
            "venue": "SSE",
            "symbol": "600519",
            "currency": "CNY",
            "asset_type": "equity",
            "display_name": None,
            "available_timeframes": ["1d"],
        }
    ]
    assert all(instrument["available_timeframes"] for instrument in instruments)


def test_research_instruments_exposes_minute_timeframes(research_client, tmp_path) -> None:
    data_root = tmp_path / "research_data" / "a_share"
    (data_root / "688017.SH-1m.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2026-07-07T01:30:00Z,460,461,459,460.5,1000\n",
        encoding="utf-8",
    )
    (data_root / "688017.SH-5m.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2026-07-07T01:30:00Z,460,461,459,460.5,1000\n",
        encoding="utf-8",
    )

    response = client_get(
        research_client,
        f"{BASE_URI}/research/instruments?bot_id=a-share-local",
    )

    assert response.status_code == 200
    item = next(item for item in response.json()["instruments"] if item["key"] == "688017.SH")
    assert item["available_timeframes"][:2] == ["1m", "5m"]


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
    assert body["length"] == 3
    assert body["meta"]["layers"][0]["source"] == "market"


def test_research_chart_candles_returns_minute_local_ohlcv(research_client, tmp_path) -> None:
    data_root = tmp_path / "research_data" / "a_share"
    (data_root / "688017.SH-1m.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2026-07-07T01:30:00Z,460,461,459,460.5,1000\n"
        "2026-07-07T01:31:00Z,460.5,462,460,461.5,1200\n",
        encoding="utf-8",
    )

    response = client_post(
        research_client,
        f"{BASE_URI}/research/chart_candles",
        data={
            "bot_id": "a-share-local",
            "instrument": "688017.SH",
            "timeframe": "1m",
            "limit": 10,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["pair"] == "688017.SH"
    assert body["chart_timeframe"] == "1m"
    assert body["length"] == 2


def test_research_chart_exposes_a_share_trading_session_axis(research_client) -> None:
    response = client_post(
        research_client,
        f"{BASE_URI}/research/chart_candles",
        data={
            "bot_id": "a-share-local",
            "instrument": "600519.SH",
            "timeframe": "1d",
            "adjustment": "raw",
            "limit": 20,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    display_column = "__display_x"
    assert payload["meta"]["axis"] == {
        "mode": "trading_session",
        "source_column": "__date_ts",
        "display_column": display_column,
        "timezone": "Asia/Shanghai",
    }
    assert display_column in payload["columns"]
    display_column_index = payload["columns"].index(display_column)
    assert [row[display_column_index] for row in payload["data"]] == list(range(payload["length"]))


def test_research_chart_exposes_axis_columns_for_empty_a_share_window(research_client) -> None:
    response = client_post(
        research_client,
        f"{BASE_URI}/research/chart_candles",
        data={
            "bot_id": "a-share-local",
            "instrument": "600519.SH",
            "timeframe": "1d",
            "adjustment": "raw",
            "timerange": "20250101-20250131",
            "limit": 20,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["length"] == 0
    assert "__date_ts" in payload["columns"]
    assert "__display_x" in payload["columns"]
    assert payload["data"] == []


def test_chart_axis_meta_defaults_to_time_axis() -> None:
    axis = ChartAxisMeta()

    assert axis.mode == "time"
    assert axis.source_column == "__date_ts"
    assert axis.display_column is None
    assert axis.timezone is None


def test_research_chart_schema_accepts_side_data_source_literals() -> None:
    coverage = ChartSeriesCoverage()

    for source in ("feature", "event", "document"):
        series = ChartSeriesMeta(
            column=f"{source}_column",
            label=source,
            source=source,
            kind=source,
            panel="main",
            coverage=coverage,
        )
        layer = ChartLayerMeta(
            id=f"{source}-layer",
            source=source,
            status="ok",
            label=source,
            series=[series],
        )

        assert layer.source == source
        assert layer.series[0].source == source


def test_research_chart_schema_accepts_canonical_side_layer_selection() -> None:
    request = ResearchChartCandlesRequest.model_validate(
        {
            "bot_id": "a-share-local",
            "instrument": "600519.SH",
            "timeframe": "1d",
            "side_layers": {
                "features": ["fund_flow_daily"],
                "events": ["limit_pool"],
                "documents": ["announcements"],
            },
        }
    )

    assert isinstance(request.side_layers, ResearchSideLayerSelection)
    assert request.side_layers is not None
    assert request.side_layers.features == ["fund_flow_daily"]
    assert request.side_layers.events == ["limit_pool"]
    assert request.side_layers.documents == ["announcements"]


def test_research_backtest_schema_accepts_feature_filter_strategy() -> None:
    request = ResearchBacktestRequest.model_validate(
        {
            "bot_id": "a-share-local",
            "instrument": "600519.SH",
            "timeframe": "1d",
            "strategy": {
                "type": "sma_cross_feature_filter",
                "fast": 5,
                "slow": 20,
                "feature_filter": {
                    "dataset": "fund_flow_daily",
                    "field": "main_net_inflow",
                    "operator": ">",
                    "value": 0,
                    "missing": "block",
                },
            },
        }
    )

    assert request.strategy.type == "sma_cross_feature_filter"
    assert request.strategy.fast == 5
    assert request.strategy.slow == 20
    assert request.strategy.feature_filter.dataset == "fund_flow_daily"
    assert request.strategy.feature_filter.field == "main_net_inflow"
    assert request.strategy.feature_filter.operator == ">"
    assert request.strategy.feature_filter.value == 0
    assert request.strategy.feature_filter.missing == "block"


def test_research_backtest_schema_rejects_feature_strategy_without_filter() -> None:
    with pytest.raises(ValidationError):
        ResearchBacktestRequest.model_validate(
            {
                "bot_id": "a-share-local",
                "instrument": "600519.SH",
                "timeframe": "1d",
                "strategy": {
                    "type": "sma_cross_feature_filter",
                    "fast": 5,
                    "slow": 20,
                },
            }
        )


def test_research_backtest_schema_rejects_typeless_feature_filter_strategy() -> None:
    with pytest.raises(ValidationError):
        ResearchBacktestRequest.model_validate(
            {
                "bot_id": "a-share-local",
                "instrument": "600519.SH",
                "timeframe": "1d",
                "strategy": {
                    "fast": 5,
                    "slow": 20,
                    "feature_filter": {
                        "dataset": "fund_flow_daily",
                        "field": "main_net_inflow",
                        "operator": ">",
                        "value": 0,
                        "missing": "block",
                    },
                },
            }
        )


def test_research_backtest_schema_accepts_explicit_sma_cross_without_type() -> None:
    request = ResearchBacktestRequest.model_validate(
        {
            "bot_id": "a-share-local",
            "instrument": "600519.SH",
            "timeframe": "1d",
            "strategy": {
                "fast": 5,
                "slow": 20,
            },
        }
    )

    assert request.strategy.type == "sma_cross"
    assert request.strategy.fast == 5
    assert request.strategy.slow == 20


def test_research_backtest_schema_keeps_sma_cross_default_strategy() -> None:
    request = ResearchBacktestRequest.model_validate(
        {
            "bot_id": "a-share-local",
            "instrument": "600519.SH",
            "timeframe": "1d",
        }
    )

    assert request.strategy.type == "sma_cross"
    assert request.strategy.fast == 20
    assert request.strategy.slow == 60


def test_research_backtest_schema_accepts_explicit_raw_adjustment() -> None:
    request = ResearchBacktestRequest.model_validate(
        {
            "bot_id": "a-share-local",
            "instrument": "600519.SH",
            "timeframe": "1d",
            "adjustment": "raw",
        }
    )

    assert request.adjustment == "raw"


def test_research_datasets_lists_local_side_data(default_conf, tmp_path, mocker) -> None:
    side_root = tmp_path / "research_data" / "a_share_meta"
    (side_root / "features" / "fund_flow_daily").mkdir(parents=True)
    (side_root / "features" / "fund_flow_daily" / "600519.SH.csv").write_text(
        "date,instrument,main_net_inflow,large_net_inflow,medium_net_inflow,"
        "small_net_inflow,source,publish_time,ingest_time\n"
        "2026-07-07,600519.SH,1000,800,100,100,eastmoney,"
        "2026-07-07T15:30:00+08:00,2026-07-07T16:00:00+08:00\n",
        encoding="utf-8",
    )
    with make_research_client(
        default_conf,
        tmp_path,
        mocker,
        research_bots=[
            {
                "id": "a-share-local",
                "label": "A Share Local",
                "market": "a_share",
                "data_source": {"type": "local_csv", "root": "research_data/a_share"},
                "side_data": {
                    "root": "research_data/a_share_meta",
                    "enabled_datasets": ["fund_flow_daily"],
                },
            }
        ],
    ) as client:
        response = client_get(
            client,
            f"{BASE_URI}/research/datasets?bot_id=a-share-local&instrument=600519.SH",
        )

    assert response.status_code == 200
    body = response.json()
    assert body["datasets"][0]["dataset_id"] == "fund_flow_daily"
    assert body["datasets"][0]["kind"] == "feature"
    assert body["datasets"][0]["available"] is True


def test_research_datasets_returns_empty_without_side_data_config(research_client) -> None:
    response = client_get(
        research_client,
        f"{BASE_URI}/research/datasets?bot_id=a-share-local&instrument=600519.SH",
    )

    assert response.status_code == 200
    assert response.json() == {"datasets": []}


def test_research_datasets_rejects_invalid_instrument_without_leaking_paths(
    default_conf,
    tmp_path,
    mocker,
) -> None:
    with make_research_client(
        default_conf,
        tmp_path,
        mocker,
        research_bots=[
            {
                "id": "a-share-local",
                "label": "A Share Local",
                "market": "a_share",
                "data_source": {"type": "local_csv", "root": "research_data/a_share"},
                "side_data": {
                    "root": "research_data/a_share_meta",
                    "enabled_datasets": ["fund_flow_daily"],
                },
            }
        ],
    ) as client:
        response = client_get(
            client,
            f"{BASE_URI}/research/datasets?bot_id=a-share-local&instrument=bad",
        )

    response_text = response.text
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid research dataset request"
    assert str(tmp_path) not in response_text
    assert "research_data" not in response_text
    assert "side_data_root" not in response_text
    assert "\\" not in response_text
    assert re.search(r"(?<![A-Za-z])[A-Za-z]:[\\/]", response_text) is None


def test_research_chart_returns_data_provenance(research_client) -> None:
    response = client_post(
        research_client,
        f"{BASE_URI}/research/chart_candles",
        data={
            "bot_id": "a-share-local",
            "instrument": "600519.SH",
            "timeframe": "1d",
            "limit": 10,
        },
    )

    assert response.status_code == 200
    provenance = response.json()["meta"]["data_provenance"]
    assert provenance["source_type"] == "local_csv"
    assert provenance["artifact_path"] == "600519.SH-1d.csv"


def test_research_chart_returns_requested_side_layers(default_conf, tmp_path, mocker) -> None:
    side_root = tmp_path / "research_data" / "a_share_meta"
    (side_root / "features" / "fund_flow_daily").mkdir(parents=True)
    (side_root / "events" / "limit_pool").mkdir(parents=True)
    (side_root / "features" / "fund_flow_daily" / "600519.SH.csv").write_text(
        "date,instrument,main_net_inflow,large_net_inflow,medium_net_inflow,"
        "small_net_inflow,source,publish_time,ingest_time\n"
        "2026-07-07,600519.SH,1000,800,100,100,eastmoney,"
        "2026-07-07T15:30:00+08:00,2026-07-07T16:00:00+08:00\n",
        encoding="utf-8",
    )
    (side_root / "events" / "limit_pool" / "2026-07-07.jsonl").write_text(
        '{"schema_version":1,"event_id":"limit:2026-07-07:600519.SH",'
        '"dataset":"limit_pool","market":"a_share","instrument":"600519.SH",'
        '"event_type":"limit_up","event_time":"2026-07-07T15:00:00+08:00",'
        '"publish_time":"2026-07-07T15:05:00+08:00",'
        '"ingest_time":"2026-07-07T16:00:00+08:00",'
        '"effective_candle_time":"2026-07-07 00:00:00+00:00",'
        '"title":"Limit up","payload":{"reason":"theme"},"source":"eastmoney"}\n',
        encoding="utf-8",
    )
    with make_research_client(
        default_conf,
        tmp_path,
        mocker,
        research_bots=[
            {
                "id": "a-share-local",
                "label": "A Share Local",
                "market": "a_share",
                "data_source": {"type": "local_csv", "root": "research_data/a_share"},
                "side_data": {
                    "root": "research_data/a_share_meta",
                    "enabled_datasets": ["fund_flow_daily", "limit_pool"],
                },
            }
        ],
    ) as client:
        response = client_post(
            client,
            f"{BASE_URI}/research/chart_candles",
            data={
                "bot_id": "a-share-local",
                "instrument": "600519.SH",
                "timeframe": "1d",
                "limit": 10,
                "side_layers": {"features": ["fund_flow_daily"], "events": ["limit_pool"]},
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert "feature_fund_flow_daily_main_net_inflow" in body["columns"]
    assert body["plot_config"]["subplots"]["Fund Flow"][
        "feature_fund_flow_daily_main_net_inflow"
    ]["type"] == "bar"
    sources = [layer["source"] for layer in body["meta"]["layers"]]
    assert "feature" in sources
    assert "event" in sources


def test_research_chart_rejects_side_layers_on_minute_timeframe(
    research_client,
    tmp_path,
    mocker,
) -> None:
    data_root = tmp_path / "research_data" / "a_share"
    (data_root / "688017.SH-1m.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2026-07-07T01:30:00Z,10,10.5,9.5,10,1000\n",
        encoding="utf-8",
    )
    mocker.patch(
        "freqtrade.research.chart.LocalResearchSideDataStore",
        side_effect=AssertionError("minute side-layer rejection should happen before store access"),
    )
    mocker.patch(
        "freqtrade.research.chart.apply_side_data_chart_layers",
        side_effect=AssertionError(
            "minute side-layer rejection should happen before side-data load"
        ),
    )

    response = client_post(
        research_client,
        f"{BASE_URI}/research/chart_candles",
        data={
            "bot_id": "a-share-local",
            "instrument": "688017.SH",
            "timeframe": "1m",
            "side_layers": {"features": ["fund_flow_daily"], "events": [], "documents": []},
        },
    )

    assert response.status_code == 501
    assert "Research side layers support 1d only" in response.json()["detail"]


def test_research_chart_rejects_unsupported_adjustment(research_client) -> None:
    response = client_post(
        research_client,
        f"{BASE_URI}/research/chart_candles",
        data={
            "bot_id": "a-share-local",
            "instrument": "600519.SH",
            "timeframe": "1d",
            "adjustment": "qfq",
        },
    )

    assert response.status_code == 501
    assert response.json()["detail"] == "Research adjustment qfq is not supported yet."


@pytest.mark.parametrize("timeframe", ["1w", "1M"])
def test_research_chart_rejects_unsupported_timeframe_even_if_csv_exists(
    research_client,
    tmp_path,
    timeframe,
) -> None:
    data_root = tmp_path / "research_data" / "a_share"
    (data_root / f"600519.SH-{timeframe}.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2026-07-06,1700,1710,1690,1705,100000\n",
        encoding="utf-8",
    )

    response = client_post(
        research_client,
        f"{BASE_URI}/research/chart_candles",
        data={
            "bot_id": "a-share-local",
            "instrument": "600519.SH",
            "timeframe": timeframe,
        },
    )

    assert response.status_code == 501
    assert response.json()["detail"] == f"Research timeframe {timeframe} is not supported yet."


def test_research_chart_applies_timerange_before_limit(research_client) -> None:
    response = client_post(
        research_client,
        f"{BASE_URI}/research/chart_candles",
        data={
            "bot_id": "a-share-local",
            "instrument": "600519.SH",
            "timeframe": "1d",
            "timerange": "20260707-20260707",
            "limit": 1,
        },
    )

    assert response.status_code == 200
    body = response.json()
    date_index = body["columns"].index("date")
    assert body["length"] == 1
    assert body["data"][0][date_index].startswith("2026-07-07")


def test_research_chart_rejects_invalid_timerange(research_client) -> None:
    response = client_post(
        research_client,
        f"{BASE_URI}/research/chart_candles",
        data={
            "bot_id": "a-share-local",
            "instrument": "600519.SH",
            "timeframe": "1d",
            "timerange": "not-a-timerange",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid research timerange: not-a-timerange"


def test_research_chart_candles_missing_ohlcv_does_not_leak_local_path(
    research_client,
    tmp_path,
) -> None:
    response = client_post(
        research_client,
        f"{BASE_URI}/research/chart_candles",
        data={
            "bot_id": "a-share-local",
            "instrument": "000002.SZ",
            "timeframe": "1d",
        },
    )

    response_text = response.text
    assert response.status_code == 404
    assert response.json()["detail"] == "Research OHLCV not found for 000002.SZ 1d"
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
    assert "Research chart data unavailable: error_type=RuntimeError" in logs_text
    assert "bot_id=a-share-local" in logs_text
    assert "instrument=600519.SH" in logs_text
    assert "timeframe=1d" in logs_text
    assert _PRIVATE_PATH_TOKEN not in logs_text
    assert "research_data" not in logs_text
    assert "data_root" not in logs_text
    assert "\\" not in logs_text
    assert re.search(r"(?<![A-Za-z])[A-Za-z]:[\\/]", logs_text) is None


def test_research_chart_and_backtest_consume_generated_a_share_csv(
    research_client,
    tmp_path,
) -> None:
    data_root = tmp_path / "research_data" / "a_share"
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "000001.SZ-1d.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2026-07-06,10,11,9,10.5,100000\n"
        "2026-07-07,10.5,11.5,10,11,200000\n"
        "2026-07-08,11,12,10.8,11.5,300000\n",
        encoding="utf-8",
    )

    chart_response = client_post(
        research_client,
        f"{BASE_URI}/research/chart_candles",
        data={
            "bot_id": "a-share-local",
            "instrument": "000001.SZ",
            "timeframe": "1d",
            "limit": 10,
        },
    )

    assert chart_response.status_code == 200
    chart_body = chart_response.json()
    assert chart_body["pair"] == "000001.SZ"
    assert chart_body["length"] == 3
    chart_close_index = chart_body["columns"].index("close")
    assert [row[chart_close_index] for row in chart_body["data"]] == [10.5, 11.0, 11.5]

    backtest_response = client_post(
        research_client,
        f"{BASE_URI}/research/backtest",
        data={
            "bot_id": "a-share-local",
            "instrument": "000001.SZ",
            "timeframe": "1d",
            "initial_cash": 100000,
            "strategy": {
                "type": "sma_cross",
                "fast": 1,
                "slow": 2,
            },
        },
    )

    assert backtest_response.status_code == 200
    backtest_body = backtest_response.json()
    assert backtest_body["metrics"]["initial_cash"] == 100000
    assert [point["close"] for point in backtest_body["equity_curve"]] == [10.5, 11.0, 11.5]


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


def test_research_backtest_runs_plain_sma_on_minute_local_ohlcv(
    research_client,
    tmp_path,
) -> None:
    data_root = tmp_path / "research_data" / "a_share"
    (data_root / "688017.SH-1m.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2026-07-07T01:30:00Z,10,10.5,9.5,10,1000\n"
        "2026-07-07T01:31:00Z,9,9.5,8.5,9,1000\n"
        "2026-07-07T01:32:00Z,11,11.5,10.5,11,1000\n"
        "2026-07-07T01:33:00Z,12,12.5,11.5,12,1000\n"
        "2026-07-08T01:30:00Z,10,10.5,9.5,10,1000\n"
        "2026-07-08T01:31:00Z,8,8.5,7.5,8,1000\n",
        encoding="utf-8",
    )

    response = client_post(
        research_client,
        f"{BASE_URI}/research/backtest",
        data={
            "bot_id": "a-share-local",
            "instrument": "688017.SH",
            "timeframe": "1m",
            "initial_cash": 100000,
            "strategy": {"type": "sma_cross", "fast": 1, "slow": 2},
        },
    )

    assert response.status_code == 200
    assert response.json()["strategy"] == "sma_cross"
    assert "return_ratio" in response.json()["metrics"]


def test_research_backtest_returns_data_provenance(research_client) -> None:
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
    provenance = response.json()["data_provenance"]
    assert provenance["source_type"] == "local_csv"
    assert provenance["artifact_path"] == "600519.SH-1d.csv"
    assert "features" not in provenance


@pytest.mark.parametrize("adjustment", ["qfq", "hfq"])
def test_research_backtest_rejects_unsupported_adjustment(
    research_client,
    adjustment,
) -> None:
    response = client_post(
        research_client,
        f"{BASE_URI}/research/backtest",
        data={
            "bot_id": "a-share-local",
            "instrument": "600519.SH",
            "timeframe": "1d",
            "adjustment": adjustment,
            "strategy": {
                "type": "sma_cross",
                "fast": 1,
                "slow": 2,
            },
        },
    )

    assert response.status_code == 501
    assert (
        response.json()["detail"]
        == f"Research adjustment {adjustment} is not supported yet."
    )


def test_research_backtest_passes_adjustment_to_data_source_and_provenance(
    default_conf,
    tmp_path,
    mocker,
) -> None:
    captured = {}
    from freqtrade.research.data_source import LocalCsvResearchDataSource

    original_load_ohlcv = LocalCsvResearchDataSource.load_ohlcv
    original_get_ohlcv_provenance = LocalCsvResearchDataSource.get_ohlcv_provenance

    def capture_load_ohlcv(self, instrument_key, timeframe, adjustment="raw"):
        captured["load_ohlcv"] = {
            "instrument_key": instrument_key,
            "timeframe": timeframe,
            "adjustment": adjustment,
        }
        return original_load_ohlcv(
            self,
            instrument_key,
            timeframe,
            adjustment,
        )

    def capture_get_ohlcv_provenance(self, instrument_key, timeframe, adjustment="raw"):
        captured["get_ohlcv_provenance"] = {
            "instrument_key": instrument_key,
            "timeframe": timeframe,
            "adjustment": adjustment,
        }
        return original_get_ohlcv_provenance(
            self,
            instrument_key,
            timeframe,
            adjustment,
        )

    with make_research_client(default_conf, tmp_path, mocker) as client:
        mocker.patch(
            "freqtrade.research.data_source.LocalCsvResearchDataSource.load_ohlcv",
            autospec=True,
            side_effect=capture_load_ohlcv,
        )
        mocker.patch(
            "freqtrade.research.data_source.LocalCsvResearchDataSource.get_ohlcv_provenance",
            autospec=True,
            side_effect=capture_get_ohlcv_provenance,
        )
        response = client_post(
            client,
            f"{BASE_URI}/research/backtest",
            data={
                "bot_id": "a-share-local",
                "instrument": "600519.SH",
                "timeframe": "1d",
                "adjustment": "raw",
                "strategy": {
                    "type": "sma_cross",
                    "fast": 1,
                    "slow": 2,
                },
            },
        )

    assert response.status_code == 200
    assert captured["load_ohlcv"]["adjustment"] == "raw"
    assert captured["get_ohlcv_provenance"]["adjustment"] == "raw"


def test_research_backtest_accepts_feature_filter_strategy(
    default_conf,
    tmp_path,
    mocker,
) -> None:
    _write_feature_backtest_side_data(tmp_path)
    with make_research_client(
        default_conf,
        tmp_path,
        mocker,
        research_bots=[
            {
                "id": "a-share-local",
                "label": "A Share Local",
                "market": "a_share",
                "data_source": {"type": "local_csv", "root": "research_data/a_share"},
                "market_data": {"meta_root": "research_data/a_share_meta"},
                "side_data": {
                    "root": "research_data/a_share_meta",
                    "enabled_datasets": ["fund_flow_daily"],
                },
            }
        ],
    ) as client:
        response = client_post(
            client,
            f"{BASE_URI}/research/backtest",
            data={
                "bot_id": "a-share-local",
                "instrument": "600519.SH",
                "timeframe": "1d",
                "initial_cash": 100000,
                "strategy": {
                    "type": "sma_cross_feature_filter",
                    "fast": 1,
                    "slow": 2,
                    "feature_filter": {
                        "dataset": "fund_flow_daily",
                        "field": "main_net_inflow",
                        "operator": ">",
                        "value": 0,
                    },
                },
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["strategy"] == "sma_cross_feature_filter"
    assert body["metrics"]["initial_cash"] == 100000
    assert body["data_provenance"]["source_type"] == "local_csv"
    assert body["data_provenance"]["features"]["fund_flow_daily"]["provider"] == "akshare"
    assert (
        body["data_provenance"]["features"]["fund_flow_daily"]["manifest_run_id"]
        == "phase3b-api-fixture"
    )


def test_research_backtest_rejects_feature_filter_on_minute_timeframe(
    research_client,
    tmp_path,
    mocker,
) -> None:
    data_root = tmp_path / "research_data" / "a_share"
    (data_root / "688017.SH-1m.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2026-07-07T01:30:00Z,10,10.5,9.5,10,1000\n"
        "2026-07-07T01:31:00Z,11,11.5,10.5,11,1000\n",
        encoding="utf-8",
    )
    mocker.patch(
        "freqtrade.rpc.api_server.api_research.create_research_feature_context",
        side_effect=AssertionError(
            "minute feature-aware backtest should reject before feature-context loading"
        ),
    )

    response = client_post(
        research_client,
        f"{BASE_URI}/research/backtest",
        data={
            "bot_id": "a-share-local",
            "instrument": "688017.SH",
            "timeframe": "1m",
            "initial_cash": 100000,
            "strategy": {
                "type": "sma_cross_feature_filter",
                "fast": 1,
                "slow": 2,
                "feature_filter": {
                    "dataset": "fund_flow_daily",
                    "field": "main_net_inflow",
                    "operator": ">",
                    "value": 0,
                    "missing": "block",
                },
            },
        },
    )

    assert response.status_code == 501
    assert "Feature-aware research backtest supports 1d only" in response.json()["detail"]


def test_research_backtest_feature_strategy_requires_side_data_config(
    default_conf,
    tmp_path,
    mocker,
) -> None:
    _write_feature_backtest_side_data(tmp_path)
    with make_research_client(
        default_conf,
        tmp_path,
        mocker,
        research_bots=[
            {
                "id": "a-share-local",
                "label": "A Share Local",
                "market": "a_share",
                "data_source": {"type": "local_csv", "root": "research_data/a_share"},
                "market_data": {"meta_root": "research_data/a_share_meta"},
            }
        ],
    ) as client:
        response = client_post(
            client,
            f"{BASE_URI}/research/backtest",
            data={
                "bot_id": "a-share-local",
                "instrument": "600519.SH",
                "timeframe": "1d",
                "strategy": {
                    "type": "sma_cross_feature_filter",
                    "fast": 1,
                    "slow": 2,
                    "feature_filter": {
                        "dataset": "fund_flow_daily",
                        "field": "main_net_inflow",
                        "operator": ">",
                        "value": 0,
                    },
                },
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Feature-aware research backtest requires side_data config."


def test_research_backtest_feature_strategy_missing_artifact_returns_404(
    default_conf,
    tmp_path,
    mocker,
) -> None:
    meta_root = tmp_path / "research_data" / "a_share_meta"
    (meta_root / "calendar").mkdir(parents=True)
    (meta_root / "features" / "fund_flow_daily").mkdir(parents=True)
    (meta_root / "calendar" / "trade_dates.csv").write_text(
        "date,is_open,source\n"
        "2026-07-06,1,test\n"
        "2026-07-07,1,test\n"
        "2026-07-08,1,test\n",
        encoding="utf-8",
    )
    with make_research_client(
        default_conf,
        tmp_path,
        mocker,
        research_bots=[
            {
                "id": "a-share-local",
                "label": "A Share Local",
                "market": "a_share",
                "data_source": {"type": "local_csv", "root": "research_data/a_share"},
                "market_data": {"meta_root": "research_data/a_share_meta"},
                "side_data": {
                    "root": "research_data/a_share_meta",
                    "enabled_datasets": ["fund_flow_daily"],
                },
            }
        ],
    ) as client:
        response = client_post(
            client,
            f"{BASE_URI}/research/backtest",
            data={
                "bot_id": "a-share-local",
                "instrument": "600519.SH",
                "timeframe": "1d",
                "strategy": {
                    "type": "sma_cross_feature_filter",
                    "fast": 1,
                    "slow": 2,
                    "feature_filter": {
                        "dataset": "fund_flow_daily",
                        "field": "main_net_inflow",
                        "operator": ">",
                        "value": 0,
                    },
                },
            },
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Research side data not found for 600519.SH fund_flow_daily"


def test_research_backtest_feature_strategy_does_not_import_provider_modules(
    default_conf,
    tmp_path,
    mocker,
) -> None:
    import sys

    sys.modules.pop("akshare", None)
    sys.modules.pop("freqtrade.research.side_data.providers.akshare_side_data", None)
    _write_feature_backtest_side_data(tmp_path)
    with make_research_client(
        default_conf,
        tmp_path,
        mocker,
        research_bots=[
            {
                "id": "a-share-local",
                "label": "A Share Local",
                "market": "a_share",
                "data_source": {"type": "local_csv", "root": "research_data/a_share"},
                "market_data": {"meta_root": "research_data/a_share_meta"},
                "side_data": {
                    "root": "research_data/a_share_meta",
                    "enabled_datasets": ["fund_flow_daily"],
                },
            }
        ],
    ) as client:
        response = client_post(
            client,
            f"{BASE_URI}/research/backtest",
            data={
                "bot_id": "a-share-local",
                "instrument": "600519.SH",
                "timeframe": "1d",
                "strategy": {
                    "type": "sma_cross_feature_filter",
                    "fast": 1,
                    "slow": 2,
                    "feature_filter": {
                        "dataset": "fund_flow_daily",
                        "field": "main_net_inflow",
                        "operator": ">",
                        "value": 0,
                    },
                },
            },
        )

    assert response.status_code == 200
    assert "akshare" not in sys.modules
    assert "freqtrade.research.side_data.providers.akshare_side_data" not in sys.modules


def test_research_backtest_passes_market_context_when_configured(
    default_conf,
    tmp_path,
    mocker,
) -> None:
    meta_root = tmp_path / "research_data" / "a_share_meta"
    (meta_root / "calendar").mkdir(parents=True)
    (meta_root / "status").mkdir(parents=True)
    (meta_root / "calendar" / "trade_dates.csv").write_text(
        "date,is_open,source\n2026-07-06,1,test\n2026-07-07,1,test\n",
        encoding="utf-8",
    )
    (meta_root / "status" / "daily_status.csv").write_text(
        "date,instrument,suspended,limit_up,limit_down,volume,listed_date,delisted_date,source\n"
        "2026-07-07,600519.SH,0,1800,1600,100000,2001-08-27,,test\n",
        encoding="utf-8",
    )
    captured = {}

    def capture_backtest(
        instrument,
        dataframe,
        config,
        market_context=None,
        feature_context=None,
        feature_filter=None,
    ):
        captured["market_context"] = market_context
        from freqtrade.research.backtesting import run_research_backtest

        return run_research_backtest(
            instrument,
            dataframe,
            config,
            market_context=market_context,
            feature_context=feature_context,
            feature_filter=feature_filter,
        )

    with make_research_client(
        default_conf,
        tmp_path,
        mocker,
        research_bots=[
            {
                "id": "a-share-local",
                "label": "A Share Local",
                "market": "a_share",
                "data_source": {"type": "local_csv", "root": "research_data/a_share"},
                "market_data": {"meta_root": "research_data/a_share_meta"},
            }
        ],
    ) as client:
        mocker.patch(
            "freqtrade.rpc.api_server.api_research.run_research_backtest",
            side_effect=capture_backtest,
        )
        response = client_post(
            client,
            f"{BASE_URI}/research/backtest",
            data={
                "bot_id": "a-share-local",
                "instrument": "600519.SH",
                "timeframe": "1d",
                "strategy": {"type": "sma_cross", "fast": 1, "slow": 2},
            },
        )

    assert response.status_code == 200
    assert captured["market_context"] is not None
    assert captured["market_context"].calendar is not None
    assert captured["market_context"].status_store is not None


@pytest.mark.parametrize("timeframe", ["1w", "1M"])
def test_research_backtest_rejects_unsupported_timeframe_even_if_csv_exists(
    research_client,
    tmp_path,
    timeframe,
) -> None:
    data_root = tmp_path / "research_data" / "a_share"
    (data_root / f"600519.SH-{timeframe}.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2026-07-06,1700,1710,1690,1705,100000\n",
        encoding="utf-8",
    )

    response = client_post(
        research_client,
        f"{BASE_URI}/research/backtest",
        data={
            "bot_id": "a-share-local",
            "instrument": "600519.SH",
            "timeframe": timeframe,
            "initial_cash": 100000,
            "strategy": {
                "type": "sma_cross",
                "fast": 1,
                "slow": 2,
            },
        },
    )

    assert response.status_code == 501
    assert response.json()["detail"] == f"Research timeframe {timeframe} is not supported yet."


def test_research_backtest_applies_timerange(research_client) -> None:
    response = client_post(
        research_client,
        f"{BASE_URI}/research/backtest",
        data={
            "bot_id": "a-share-local",
            "instrument": "600519.SH",
            "timeframe": "1d",
            "timerange": "19900101-19900131",
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
    assert body["metrics"]["trade_count"] == 0
    assert body["metrics"]["final_equity"] == 100000
    assert body["equity_curve"] == []


def test_research_backtest_rejects_more_than_5000_rows_after_timerange(
    research_client,
    mocker,
) -> None:
    mocker.patch(
        "freqtrade.research.data_source.LocalCsvResearchDataSource.load_ohlcv",
        return_value=pd.DataFrame(
            {
                "date": pd.date_range("2026-01-01", periods=5001, freq="1min", tz="UTC"),
                "open": [1.0] * 5001,
                "high": [1.0] * 5001,
                "low": [1.0] * 5001,
                "close": [1.0] * 5001,
                "volume": [1000.0] * 5001,
            }
        ),
    )

    response = client_post(
        research_client,
        f"{BASE_URI}/research/backtest",
        data={
            "bot_id": "a-share-local",
            "instrument": "600519.SH",
            "timeframe": "1d",
            "timerange": "20260101-",
            "strategy": {
                "type": "sma_cross",
                "fast": 1,
                "slow": 2,
            },
        },
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "Research backtest input exceeds 5000 rows."


def test_research_backtest_rejects_invalid_timerange(research_client) -> None:
    response = client_post(
        research_client,
        f"{BASE_URI}/research/backtest",
        data={
            "bot_id": "a-share-local",
            "instrument": "600519.SH",
            "timeframe": "1d",
            "timerange": "not-a-timerange",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid research timerange: not-a-timerange"


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


def test_research_backtest_rejects_non_finite_price_without_leaking_path(
    research_client,
    tmp_path,
) -> None:
    data_root = tmp_path / "research_data" / "a_share"
    (data_root / "000001.SZ-1d.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2026-07-06,10,10.5,9.5,10,1000\n"
        "2026-07-07,9,9.5,8.5,9,1100\n"
        "2026-07-08,inf,11.5,10.5,11,1200\n",
        encoding="utf-8",
    )

    response = client_post(
        research_client,
        f"{BASE_URI}/research/backtest",
        data={
            "bot_id": "a-share-local",
            "instrument": "000001.SZ",
            "timeframe": "1d",
            "strategy": {
                "type": "sma_cross",
                "fast": 1,
                "slow": 2,
            },
        },
    )

    response_text = response.text
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid research backtest request"
    assert str(tmp_path) not in response_text
    assert "research_data" not in response_text
    assert "data_root" not in response_text
    assert "\\" not in response_text
    assert re.search(r"[A-Za-z]:", response_text) is None


def test_research_backtest_rejects_infinite_initial_cash_without_null_metrics(
    research_client,
) -> None:
    response = client_post_raw_json(
        research_client,
        f"{BASE_URI}/research/backtest",
        content=(
            '{"bot_id":"a-share-local","instrument":"600519.SH","timeframe":"1d",'
            '"initial_cash":Infinity,'
            '"strategy":{"type":"sma_cross","fast":1,"slow":2}}'
        ),
    )

    response_text = response.text
    assert response.status_code in {400, 422}
    assert response.status_code != 200
    assert '"metrics"' not in response_text
    assert '"initial_cash":null' not in response_text
    assert '"final_equity":null' not in response_text
    assert '"return_ratio":null' not in response_text
    assert '"total_return":null' not in response_text
    assert '"cash":null' not in response_text


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
    assert "Research backtest unavailable: error_type=RuntimeError" in logs_text
    assert "bot_id=a-share-local" in logs_text
    assert "instrument=600519.SH" in logs_text
    assert "timeframe=1d" in logs_text
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
