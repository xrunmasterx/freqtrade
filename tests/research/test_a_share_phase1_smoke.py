import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from freqtrade.markets import MarketType
from freqtrade.research import LocalCsvResearchDataSource
from freqtrade.research.backtesting import ResearchBacktestConfig, run_research_backtest
from freqtrade.research.chart import build_research_chart_candles_response
from freqtrade.research.collectors.a_share_ohlcv import (
    AShareOhlcvCollector,
    AShareOhlcvRequest,
)
from freqtrade.research.profiles import ResearchBotProfile, ResearchDataSourceConfig
from freqtrade.rpc.api_server.api_schemas import ResearchChartCandlesRequest


PARENT_REPO_ROOT = Path(__file__).resolve().parents[3]
DOWNLOAD_SCRIPT_PATH = PARENT_REPO_ROOT / "tools" / "download_a_share_research_data.py"


class FakeOhlcvProvider:
    provider_name = "fake"
    provider_version = "2026.7"

    def fetch_ohlcv(
        self,
        instrument_key: str,
        timeframe: str,
        start_date: str | None,
        end_date: str | None,
        adjustment: str,
    ) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "date": ["2026-07-01", "2026-07-02", "2026-07-03"],
                "open": [1700, 1705, 1710],
                "high": [1710, 1715, 1720],
                "low": [1690, 1700, 1705],
                "close": [1705, 1710, 1715],
                "volume": [100000, 200000, 300000],
            }
        )


class FakeMinuteOhlcvProvider:
    provider_name = "fake-minute"
    provider_version = "test"

    def fetch_ohlcv(self, *args, **kwargs) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "day": [
                    "2026-07-07 09:31:00",
                    "2026-07-07 09:32:00",
                    "2026-07-07 09:33:00",
                    "2026-07-07 09:34:00",
                    "2026-07-08 09:31:00",
                    "2026-07-08 09:32:00",
                ],
                "open": [10, 9, 11, 12, 10, 8],
                "high": [10.5, 9.5, 11.5, 12.5, 10.5, 8.5],
                "low": [9.5, 8.5, 10.5, 11.5, 9.5, 7.5],
                "close": [10, 9, 11, 12, 10, 8],
                "volume": [1000, 1000, 1000, 1000, 1000, 1000],
            }
        )

    def source_timestamp_semantics(self, timeframe: str) -> str:
        return "candle_close"

    def provider_endpoint(self, timeframe: str) -> str:
        return "stock_zh_a_minute"

    def history_depth_metadata(self, timeframe: str) -> dict[str, object]:
        return {"history_depth_policy": "provider_latest_bars", "provider_row_limit": 1970}


def test_a_share_phase1_collector_chart_and_backtest_smoke(tmp_path) -> None:
    collector = AShareOhlcvCollector(root=tmp_path, provider=FakeOhlcvProvider())

    summary = collector.collect(
        AShareOhlcvRequest(instruments=["600519.SH"], timeframes=["1d"])
    )

    assert summary.failed == 0
    profile = ResearchBotProfile(
        id="a-share-local",
        label="A Share Local",
        market=MarketType.A_SHARE,
        data_source=ResearchDataSourceConfig(type="local_csv", root="research_data/a_share"),
        data_root=tmp_path,
    )
    response = build_research_chart_candles_response(
        profile,
        ResearchChartCandlesRequest(
            bot_id="a-share-local",
            instrument="600519.SH",
            timeframe="1d",
        ),
    )

    assert response["pair"] == "600519.SH"
    assert response["length"] == 3

    loaded = pd.read_csv(tmp_path / "600519.SH-1d.csv")
    backtest = run_research_backtest(
        "600519.SH",
        loaded,
        ResearchBacktestConfig(initial_cash=100000, fast=1, slow=2),
    )

    assert "initial_cash" in backtest.metrics
    assert "return_ratio" in backtest.metrics


def test_minute_collector_output_feeds_research_chart_and_backtest(tmp_path) -> None:
    collector = AShareOhlcvCollector(root=tmp_path, provider=FakeMinuteOhlcvProvider())
    collector.collect(AShareOhlcvRequest(instruments=["688017.SH"], timeframes=["1m"]))
    profile = ResearchBotProfile(
        id="a-share-local",
        label="A Share Local",
        market=MarketType.A_SHARE,
        data_source=ResearchDataSourceConfig(type="local_csv", root="research_data/a_share"),
        data_root=tmp_path,
    )

    chart = build_research_chart_candles_response(
        profile,
        ResearchChartCandlesRequest(
            bot_id="a-share-local",
            instrument="688017.SH",
            timeframe="1m",
            limit=10,
        ),
    )
    assert chart["pair"] == "688017.SH"
    assert chart["chart_timeframe"] == "1m"
    assert chart["length"] == 6

    data_source = LocalCsvResearchDataSource(tmp_path)
    dataframe = data_source.load_ohlcv("688017.SH", "1m")
    result = run_research_backtest(
        "688017.SH",
        dataframe,
        ResearchBacktestConfig(initial_cash=100000, fast=1, slow=2),
    )
    assert result.metrics["initial_cash"] == 100000
    assert "return_ratio" in result.metrics


@pytest.mark.parametrize(
    ("timerange", "expected"),
    [
        ("20260701-20260731", ("20260701", "20260731")),
        ("20260701-", ("20260701", None)),
        ("-20260731", (None, "20260731")),
    ],
)
def test_download_script_parse_timerange_accepts_supported_forms(timerange, expected) -> None:
    script = _load_download_script()

    assert script._parse_timerange(timerange) == expected


@pytest.mark.parametrize(
    "timerange",
    [
        "bad",
        "abc-def",
        "2026-07-01",
        "-",
        "20260230-20260301",
        "20260301-20260228",
    ],
)
def test_download_script_parse_timerange_rejects_invalid_forms(timerange) -> None:
    script = _load_download_script()

    with pytest.raises(ValueError, match=r"Timerange must use YYYYMMDD-YYYYMMDD format\."):
        script._parse_timerange(timerange)


def test_download_script_reports_invalid_research_config_without_traceback(
    tmp_path,
    capsys,
) -> None:
    script = _load_download_script()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "user_data_dir": str(tmp_path),
                "research_bots": [
                    {
                        "id": "a-share-local",
                        "label": "A Share Local",
                        "market": "a_share",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = script.main(
        [
            "--config",
            str(config_path),
            "--bot-id",
            "a-share-local",
            "--instruments",
            "600519.SH",
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "Missing research_bots[0].data_source" in captured.err
    assert "Traceback" not in captured.err
    assert str(PARENT_REPO_ROOT) not in captured.err


def test_download_script_reports_missing_config_without_traceback_or_path(
    tmp_path,
    capsys,
) -> None:
    script = _load_download_script()
    config_path = tmp_path / "missing.json"

    result = script.main(
        [
            "--config",
            str(config_path),
            "--bot-id",
            "a-share-local",
            "--instruments",
            "600519.SH",
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.err.strip() == "Unable to read config file."
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
    assert str(tmp_path) not in captured.err
    assert str(tmp_path) not in captured.out


def test_download_script_reports_invalid_instrument_without_traceback_or_path(
    tmp_path,
    capsys,
) -> None:
    script = _load_download_script()
    config_path = tmp_path / "config.json"
    data_root = tmp_path / "research_data" / "a_share"
    config_path.write_text(
        json.dumps(
            {
                "user_data_dir": str(tmp_path),
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
        ),
        encoding="utf-8",
    )

    result = script.main(
        [
            "--config",
            str(config_path),
            "--bot-id",
            "a-share-local",
            "--instruments",
            "600519",
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "Invalid A-share instrument key: 600519" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
    assert "G:\\AI_Trading" not in captured.err
    assert "G:\\AI_Trading" not in captured.out
    assert str(PARENT_REPO_ROOT) not in captured.err
    assert str(PARENT_REPO_ROOT) not in captured.out
    assert not data_root.exists()


def test_download_script_reports_collection_io_error_without_traceback_or_path(
    tmp_path,
    capsys,
) -> None:
    script = _load_download_script()
    config_path = tmp_path / "config.json"
    data_root = tmp_path / "research_data" / "a_share"
    data_root.parent.mkdir(parents=True)
    data_root.write_text("not a directory", encoding="utf-8")
    config_path.write_text(
        json.dumps(
            {
                "user_data_dir": str(tmp_path),
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
        ),
        encoding="utf-8",
    )

    result = script.main(
        [
            "--config",
            str(config_path),
            "--bot-id",
            "a-share-local",
            "--instruments",
            "600519.SH",
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.err.strip() == "Unable to write A-share OHLCV output."
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
    assert str(tmp_path) not in captured.err
    assert str(tmp_path) not in captured.out


def test_download_script_public_file_error_hides_local_paths(tmp_path) -> None:
    script = _load_download_script()

    assert script._public_file_error(f"failed to write {tmp_path}\\600519.SH-1d.csv") == "failed"
    assert script._public_file_error("Install optional dependency") == "Install optional dependency"


def _load_download_script():
    spec = importlib.util.spec_from_file_location(
        "download_a_share_research_data",
        DOWNLOAD_SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
