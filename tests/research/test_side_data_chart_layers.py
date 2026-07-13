import pandas as pd

from freqtrade.research.side_data.chart_layers import apply_side_data_chart_layers
from freqtrade.research.side_data.models import ResearchSideLayerSelection
from freqtrade.research.side_data.store import LocalResearchSideDataStore


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


def test_apply_side_data_chart_layers_adds_feature_columns_and_points(tmp_path) -> None:
    _write_side_data_fixture(tmp_path)
    dataframe = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-07-06", "2026-07-07", "2026-07-08"], utc=True),
            "open": [1.0, 1.0, 1.0],
            "high": [1.0, 1.0, 1.0],
            "low": [1.0, 1.0, 1.0],
            "close": [1.0, 1.0, 1.0],
            "volume": [1000.0, 1000.0, 1000.0],
        }
    )

    result, plot_update, layers = apply_side_data_chart_layers(
        dataframe,
        LocalResearchSideDataStore(tmp_path),
        "600519.SH",
        ResearchSideLayerSelection(
            features=["fund_flow_daily"],
            events=["limit_pool"],
            documents=["announcements"],
        ),
    )

    feature_column = "feature_fund_flow_daily_main_net_inflow"
    assert feature_column in result.columns
    assert pd.isna(result.iloc[0][feature_column])
    assert result.iloc[1][feature_column] == 1000.0
    assert plot_update["subplots"]["Fund Flow"][feature_column]["type"] == "bar"

    assert [layer.source for layer in layers] == ["feature", "event", "document"]
    assert layers[0].status == "partial"
    assert [series.column for series in layers[0].series] == [
        "feature_fund_flow_daily_main_net_inflow",
        "feature_fund_flow_daily_large_net_inflow",
        "feature_fund_flow_daily_medium_net_inflow",
        "feature_fund_flow_daily_small_net_inflow",
    ]
    assert layers[1].points[0].payload["reason"] == "theme"
    assert layers[1].points[0].timestamp == int(
        pd.Timestamp("2026-07-07 00:00:00+00:00").timestamp() * 1000
    )
    assert layers[1].points[0].label == "limit_up"
    assert layers[1].points[0].payload["title"] == "Limit up"
    assert layers[2].points[0].payload["url"] == "https://example.invalid/a.pdf"
    assert layers[2].points[0].timestamp == int(
        pd.Timestamp("2026-07-08 00:00:00+00:00").timestamp() * 1000
    )
    assert layers[2].points[0].label == "announcement"
    assert layers[2].points[0].payload["title"] == "Announcement"


def test_apply_side_data_chart_layers_filters_out_of_window_event_and_document_points(
    tmp_path,
) -> None:
    (tmp_path / "events" / "limit_pool").mkdir(parents=True)
    (tmp_path / "documents" / "announcements").mkdir(parents=True)
    (tmp_path / "events" / "limit_pool" / "2026-07-09.jsonl").write_text(
        '{"schema_version":1,"event_id":"limit:2026-07-09:600519.SH",'
        '"dataset":"limit_pool","market":"a_share","instrument":"600519.SH",'
        '"event_type":"limit_up","event_time":"2026-07-09T15:00:00+08:00",'
        '"publish_time":"2026-07-09T15:05:00+08:00",'
        '"ingest_time":"2026-07-09T16:00:00+08:00",'
        '"effective_candle_time":"2026-07-09 00:00:00+00:00",'
        '"title":"Out of window event","payload":{"reason":"theme"},"source":"eastmoney"}\n',
        encoding="utf-8",
    )
    (tmp_path / "documents" / "announcements" / "600519.SH.jsonl").write_text(
        '{"schema_version":1,"document_id":"cninfo:600519.SH:2",'
        '"dataset":"announcements","market":"a_share","instrument":"600519.SH",'
        '"document_type":"announcement","title":"Out of window document",'
        '"publish_time":"2026-07-09T19:30:00+08:00",'
        '"ingest_time":"2026-07-09T20:00:00+08:00",'
        '"effective_candle_time":"2026-07-09 00:00:00+00:00",'
        '"url":"https://example.invalid/out-of-window.pdf","source":"cninfo",'
        '"payload":{"category":"notice"}}\n',
        encoding="utf-8",
    )
    dataframe = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-07-06", "2026-07-07", "2026-07-08"], utc=True),
            "open": [1.0, 1.0, 1.0],
            "high": [1.0, 1.0, 1.0],
            "low": [1.0, 1.0, 1.0],
            "close": [1.0, 1.0, 1.0],
            "volume": [1000.0, 1000.0, 1000.0],
        }
    )

    _, _, layers = apply_side_data_chart_layers(
        dataframe,
        LocalResearchSideDataStore(tmp_path),
        "600519.SH",
        ResearchSideLayerSelection(events=["limit_pool"], documents=["announcements"]),
    )

    assert [layer.source for layer in layers] == ["event", "document"]
    assert layers[0].status == "unavailable"
    assert layers[0].points == []
    assert layers[1].status == "unavailable"
    assert layers[1].points == []


def test_apply_side_data_chart_layers_marks_missing_feature_and_document_layers_unavailable(
    tmp_path,
) -> None:
    dataframe = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-07-06", "2026-07-07"], utc=True),
            "open": [1.0, 1.0],
            "high": [1.0, 1.0],
            "low": [1.0, 1.0],
            "close": [1.0, 1.0],
            "volume": [1000.0, 1000.0],
        }
    )

    result, plot_update, layers = apply_side_data_chart_layers(
        dataframe,
        LocalResearchSideDataStore(tmp_path),
        "600519.SH",
        ResearchSideLayerSelection(
            features=["fund_flow_daily"],
            documents=["announcements"],
        ),
    )

    assert list(result.columns) == list(dataframe.columns)
    assert plot_update["subplots"] == {}
    assert [layer.source for layer in layers] == ["feature", "document"]
    assert layers[0].status == "unavailable"
    assert layers[0].series == []
    assert layers[1].status == "unavailable"
    assert layers[1].points == []
