from freqtrade.research import load_research_profiles
from freqtrade.research.chart import build_research_chart_candles_response
from freqtrade.rpc.api_server.api_schemas import ResearchChartCandlesRequest


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
    assert response["length"] == 2
    assert response["meta"]["layers"][0]["source"] == "market"
    assert response["plot_config"]
