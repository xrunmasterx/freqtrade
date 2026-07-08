from pathlib import Path

import pandas as pd

from freqtrade.research import load_research_profiles
from freqtrade.research.chart import build_research_chart_candles_response
from freqtrade.rpc.api_server.api_schemas import ResearchChartCandlesRequest


def _write_side_data_fixture(root) -> None:
    (root / "features" / "fund_flow_daily").mkdir(parents=True)
    (root / "events" / "limit_pool").mkdir(parents=True)
    (root / "documents" / "announcements").mkdir(parents=True)
    (root / "features" / "fund_flow_daily" / "600519.SH.csv").write_text(
        "date,instrument,main_net_inflow,large_net_inflow,medium_net_inflow,"
        "small_net_inflow,source,publish_time,ingest_time\n"
        "2026-07-07,600519.SH,1000,800,100,100,eastmoney,"
        "2026-07-07T15:30:00+08:00,2026-07-07T16:00:00+08:00\n",
        encoding="utf-8",
    )
    (root / "events" / "limit_pool" / "2026-07-07.jsonl").write_text(
        '{"schema_version":1,"event_id":"limit:2026-07-07:600519.SH",'
        '"dataset":"limit_pool","market":"a_share","instrument":"600519.SH",'
        '"event_type":"limit_up","event_time":"2026-07-07T15:00:00+08:00",'
        '"publish_time":"2026-07-07T15:05:00+08:00",'
        '"ingest_time":"2026-07-07T16:00:00+08:00",'
        '"effective_candle_time":"2026-07-07 00:00:00+00:00",'
        '"title":"Limit up","payload":{"reason":"theme"},"source":"eastmoney"}\n',
        encoding="utf-8",
    )
    (root / "documents" / "announcements" / "600519.SH.jsonl").write_text(
        '{"schema_version":1,"document_id":"cninfo:600519.SH:1",'
        '"dataset":"announcements","market":"a_share","instrument":"600519.SH",'
        '"document_type":"announcement","title":"Announcement",'
        '"publish_time":"2026-07-07T19:30:00+08:00",'
        '"ingest_time":"2026-07-07T20:00:00+08:00",'
        '"effective_candle_time":"2026-07-08 00:00:00+00:00",'
        '"url":"https://example.invalid/a.pdf","source":"cninfo",'
        '"payload":{"category":"notice"}}\n',
        encoding="utf-8",
    )


def test_build_research_chart_candles_response_uses_local_csv_profile(tmp_path) -> None:
    data_root = tmp_path / "research_data" / "a_share"
    data_root.mkdir(parents=True)
    (data_root / "600519.SH-1d.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2026-07-06,1700,1710,1690,1705,100000\n"
        "2026-07-07,1705,1715,1700,1710,200000\n",
        encoding="utf-8",
    )
    config = {
        "user_data_dir": tmp_path,
        "research_bots": [
            {
                "id": "a-share-local",
                "label": "A Share Local",
                "market": "a_share",
                "data_source": {
                    "type": "local_csv",
                    "root": "research_data/a_share",
                },
            }
        ],
    }
    profile = load_research_profiles(config)[0]
    payload = ResearchChartCandlesRequest(
        bot_id="a-share-local",
        instrument="600519.SH",
        timeframe="1d",
        limit=100,
    )

    response = build_research_chart_candles_response(profile, payload)

    assert response["pair"] == "600519.SH"
    assert response["timeframe"] == "1d"
    assert response["chart_timeframe"] == "1d"
    assert response["columns"][:6] == ["date", "open", "high", "low", "close", "volume"]
    assert "__date_ts" in response["columns"]
    assert response["timeframe_ms"] == 86400000
    assert response["length"] == 2
    assert response["meta"]["layers"][0]["source"] == "market"
    assert response["meta"]["data_provenance"]["source_type"] == "local_csv"
    assert response["meta"]["data_provenance"]["artifact_path"] == "600519.SH-1d.csv"
    assert response["plot_config"]


def test_build_research_chart_candles_response_applies_side_layers(tmp_path) -> None:
    data_root = tmp_path / "research_data" / "a_share"
    side_root = tmp_path / "research_data" / "a_share_meta"
    data_root.mkdir(parents=True)
    _write_side_data_fixture(side_root)
    (data_root / "600519.SH-1d.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2026-07-06,1700,1710,1690,1705,100000\n"
        "2026-07-07,1705,1715,1700,1710,200000\n"
        "2026-07-08,1710,1720,1705,1715,300000\n",
        encoding="utf-8",
    )
    config = {
        "user_data_dir": tmp_path,
        "research_bots": [
            {
                "id": "a-share-local",
                "label": "A Share Local",
                "market": "a_share",
                "data_source": {
                    "type": "local_csv",
                    "root": "research_data/a_share",
                },
                "side_data": {
                    "root": "research_data/a_share_meta",
                    "enabled_datasets": ["fund_flow_daily", "limit_pool", "announcements"],
                },
            }
        ],
    }
    profile = load_research_profiles(config)[0]
    payload = ResearchChartCandlesRequest(
        bot_id="a-share-local",
        instrument="600519.SH",
        timeframe="1d",
        limit=100,
        side_layers={
            "features": ["fund_flow_daily"],
            "events": ["limit_pool"],
            "documents": ["announcements"],
        },
    )

    response = build_research_chart_candles_response(profile, payload)

    feature_column = "feature_fund_flow_daily_main_net_inflow"
    assert feature_column in response["columns"]
    assert response["plot_config"]["subplots"]["Fund Flow"][feature_column]["type"] == "bar"
    sources = [layer["source"] for layer in response["meta"]["layers"]]
    assert sources == ["market", "watch", "feature", "event", "document"]
    feature_index = response["columns"].index(feature_column)
    assert response["data"][1][feature_index] == 1000.0
    assert response["meta"]["layers"][3]["points"][0]["payload"]["reason"] == "theme"
    assert response["meta"]["layers"][4]["points"][0]["payload"]["url"] == (
        "https://example.invalid/a.pdf"
    )


def test_build_research_chart_candles_response_passes_adjustment(tmp_path, mocker) -> None:
    config = {
        "user_data_dir": tmp_path,
        "research_bots": [
            {
                "id": "a-share-local",
                "label": "A Share Local",
                "market": "a_share",
                "data_source": {
                    "type": "local_csv",
                    "root": "research_data/a_share",
                },
            }
        ],
    }
    profile = load_research_profiles(config)[0]
    payload = ResearchChartCandlesRequest(
        bot_id="a-share-local",
        instrument="600519.SH",
        timeframe="1d",
        adjustment="qfq",
    )
    data_source = mocker.Mock()
    data_source.load_ohlcv.return_value = pd.DataFrame(
        {
            "date": [pd.Timestamp("2026-07-06", tz="UTC")],
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "volume": [1000.0],
        }
    )
    data_source.get_ohlcv_provenance.return_value.model_dump.return_value = {
        "source_type": "local_csv",
        "artifact_path": "600519.SH-1d.csv",
    }
    mocker.patch(
        "freqtrade.research.chart.create_research_data_source",
        return_value=data_source,
    )

    build_research_chart_candles_response(profile, payload)

    data_source.load_ohlcv.assert_called_once_with("600519.SH", "1d", "qfq")


def test_research_chart_module_does_not_import_trading_chain() -> None:
    source = Path("freqtrade/research/chart.py").read_text(encoding="utf-8")

    assert "from freqtrade.rpc import RPC" not in source
    assert "freqtrade.rpc.rpc" not in source
    assert "Exchange" not in source
    assert "Trade" not in source
    assert "Wallet" not in source
