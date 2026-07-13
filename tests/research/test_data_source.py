from fnmatch import fnmatchcase

import pandas as pd
import pytest

from freqtrade.research import LocalCsvResearchDataSource
from freqtrade.research.data_source import RESEARCH_OHLCV_COLUMNS
from freqtrade.research.exceptions import ResearchUnsupportedFeatureError


class CaseSensitiveGlobRoot:
    def __init__(self, root) -> None:
        self.root = root

    def glob(self, pattern: str):
        return [path for path in self.root.iterdir() if fnmatchcase(path.name, pattern)]


def test_local_csv_research_data_source_lists_a_share_instruments(tmp_path) -> None:
    (tmp_path / "600519.SH-1d.csv").write_text(
        "date,open,high,low,close,volume\n2026-07-06,1700,1710,1690,1705,100000\n",
        encoding="utf-8",
    )

    data_source = LocalCsvResearchDataSource(tmp_path)
    instruments = data_source.list_instruments()

    assert [instrument.key for instrument in instruments] == ["600519.SH"]
    assert instruments[0].venue == "SSE"
    assert instruments[0].currency == "CNY"


def test_local_csv_research_data_source_skips_invalid_timeframe_suffix(tmp_path, caplog) -> None:
    (tmp_path / "600519.SH-1-day.csv").write_text(
        "date,open,high,low,close,volume\n2026-07-06,1700,1710,1690,1705,100000\n",
        encoding="utf-8",
    )

    data_source = LocalCsvResearchDataSource(tmp_path)

    assert data_source.list_instruments() == []
    assert "Skipping invalid research data filename: 600519.SH-1-day.csv" in caplog.text


def test_local_csv_research_data_source_skips_invalid_csv_names(tmp_path, caplog) -> None:
    (tmp_path / "600519.SH-1d.csv").write_text(
        "date,open,high,low,close,volume\n2026-07-06,1700,1710,1690,1705,100000\n",
        encoding="utf-8",
    )
    for filename in ["notes.csv", "bad-name.csv", "secret-1d.csv", "600519.SH-bad.csv"]:
        (tmp_path / filename).write_text(
            "date,open,high,low,close,volume\n2026-07-06,1,1,1,1,1\n",
            encoding="utf-8",
        )
    data_source = LocalCsvResearchDataSource(tmp_path)

    instruments = data_source.list_instruments()

    assert [instrument.key for instrument in instruments] == ["600519.SH"]
    assert "Skipping invalid research data filename: notes.csv" in caplog.text
    assert "Skipping invalid research data filename: bad-name.csv" in caplog.text
    assert "Skipping invalid research data filename: secret-1d.csv" in caplog.text
    assert "Skipping invalid research data filename: 600519.SH-bad.csv" in caplog.text


def test_local_csv_research_data_source_lists_available_timeframes(tmp_path) -> None:
    for filename in [
        "600519.SH-60m.csv",
        "600519.SH-30m.csv",
        "600519.SH-15m.csv",
        "600519.SH-1m.csv",
        "600519.SH-5m.csv",
        "600519.SH-1d.csv",
        "600519.SH-bad.csv",
        "000001.SZ-1d.csv",
    ]:
        (tmp_path / filename).write_text(
            "date,open,high,low,close,volume\n2026-07-07T01:30:00Z,1,1,1,1,1\n",
            encoding="utf-8",
        )
    data_source = LocalCsvResearchDataSource(tmp_path)

    assert data_source.available_timeframes("600519.SH") == [
        "1m",
        "5m",
        "15m",
        "30m",
        "60m",
        "1d",
    ]


def test_local_csv_research_data_source_lists_supported_minute_timeframe_files(tmp_path) -> None:
    (tmp_path / "600519.SH-5m.csv").write_text(
        "date,open,high,low,close,volume\n2026-07-07T01:30:00Z,1700,1710,1690,1705,100000\n",
        encoding="utf-8",
    )

    data_source = LocalCsvResearchDataSource(tmp_path)

    assert [instrument.key for instrument in data_source.list_instruments()] == ["600519.SH"]
    assert data_source.available_timeframes("600519.SH") == ["5m"]


def test_local_csv_research_data_source_lists_available_timeframes_for_case_normalized_instrument(
    tmp_path,
) -> None:
    (tmp_path / "600519.sh-1d.csv").write_text(
        "date,open,high,low,close,volume\n2026-07-06,1700,1710,1690,1705,100000\n",
        encoding="utf-8",
    )
    data_source = LocalCsvResearchDataSource(CaseSensitiveGlobRoot(tmp_path))

    assert [instrument.key for instrument in data_source.list_instruments()] == ["600519.SH"]
    assert data_source.available_timeframes("600519.SH") == ["1d"]


def test_local_csv_research_data_source_loads_normalized_ohlcv(tmp_path) -> None:
    (tmp_path / "600519.SH-1d.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2026-07-07,1705,1715,1700,1710,200000\n"
        "2026-07-06,1700,1710,1690,1705,100000\n",
        encoding="utf-8",
    )
    data_source = LocalCsvResearchDataSource(tmp_path)

    dataframe = data_source.load_ohlcv("600519.SH", "1d")

    assert list(dataframe.columns) == RESEARCH_OHLCV_COLUMNS
    assert pd.api.types.is_datetime64_any_dtype(dataframe["date"])
    assert dataframe["date"].tolist() == [
        pd.Timestamp("2026-07-06", tz="UTC"),
        pd.Timestamp("2026-07-07", tz="UTC"),
    ]
    for column in ["open", "high", "low", "close", "volume"]:
        assert pd.api.types.is_float_dtype(dataframe[column])


def test_local_csv_research_data_source_rejects_extra_ohlcv_columns(tmp_path) -> None:
    (tmp_path / "600519.SH-1d.csv").write_text(
        "date,open,high,low,close,volume,suspended\n"
        "2026-07-06,1700,1710,1690,1705,100000,0\n",
        encoding="utf-8",
    )
    data_source = LocalCsvResearchDataSource(tmp_path)

    with pytest.raises(ValueError, match="Invalid OHLCV columns"):
        data_source.load_ohlcv("600519.SH", "1d")


def test_local_csv_research_data_source_rejects_unsupported_adjustment(tmp_path) -> None:
    data_source = LocalCsvResearchDataSource(tmp_path)

    with pytest.raises(ValueError, match="Unsupported research adjustment: qfq"):
        data_source.load_ohlcv("600519.SH", "1d", adjustment="qfq")


def test_local_csv_research_data_source_returns_ohlcv_provenance(tmp_path) -> None:
    (tmp_path / "600519.SH-1d.csv").write_text(
        "date,open,high,low,close,volume\n2026-07-06,1700,1710,1690,1705,100000\n",
        encoding="utf-8",
    )
    data_source = LocalCsvResearchDataSource(tmp_path)

    provenance = data_source.get_ohlcv_provenance("600519.SH", "1d")

    assert provenance.source_type == "local_csv"
    assert provenance.artifact_path == "600519.SH-1d.csv"


@pytest.mark.parametrize("timeframe", ["3m", "1w", "1M"])
def test_local_csv_research_data_source_rejects_unsupported_timeframe_even_if_file_exists(
    tmp_path,
    timeframe,
) -> None:
    (tmp_path / f"600519.SH-{timeframe}.csv").write_text(
        "date,open,high,low,close,volume\n2026-07-06,1700,1710,1690,1705,100000\n",
        encoding="utf-8",
    )
    data_source = LocalCsvResearchDataSource(tmp_path)

    with pytest.raises(
        ResearchUnsupportedFeatureError,
        match=f"Research timeframe {timeframe} is not supported yet.",
    ):
        data_source.load_ohlcv("600519.SH", timeframe)


def test_local_csv_research_data_source_loads_minute_ohlcv_with_utc_timestamps(tmp_path) -> None:
    (tmp_path / "688017.SH-1m.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2026-07-07T01:31:00Z,461,462,460,461.5,1200\n"
        "2026-07-07T01:30:00Z,460,461,459,460.5,1000\n",
        encoding="utf-8",
    )
    data_source = LocalCsvResearchDataSource(tmp_path)

    dataframe = data_source.load_ohlcv("688017.SH", "1m")

    assert dataframe["date"].tolist() == [
        pd.Timestamp("2026-07-07T01:30:00Z"),
        pd.Timestamp("2026-07-07T01:31:00Z"),
    ]
    assert pd.api.types.is_float_dtype(dataframe["open"])


def test_local_csv_research_data_source_rejects_out_of_session_minute_ohlcv(tmp_path) -> None:
    (tmp_path / "688017.SH-1m.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2026-07-07T03:30:00Z,460,461,459,460.5,1000\n",
        encoding="utf-8",
    )
    data_source = LocalCsvResearchDataSource(tmp_path)

    with pytest.raises(ValueError, match="A-share minute OHLCV contains out-of-session rows"):
        data_source.load_ohlcv("688017.SH", "1m")


def test_local_csv_research_data_source_rejects_instrument_path_traversal(tmp_path) -> None:
    data_root = tmp_path / "research_data" / "a_share"
    data_root.mkdir(parents=True)
    (tmp_path / "secret-1d.csv").write_text(
        "date,open,high,low,close,volume\n2026-07-08,424242,424242,424242,424242,424242\n",
        encoding="utf-8",
    )
    data_source = LocalCsvResearchDataSource(data_root)

    with pytest.raises(ValueError):
        data_source.load_ohlcv("../../secret", "1d")


@pytest.mark.parametrize("timeframe", ["../1d", r"..\1d"])
def test_local_csv_research_data_source_rejects_timeframe_path_traversal(
    tmp_path,
    timeframe,
) -> None:
    (tmp_path / "600519.SH-1d.csv").write_text(
        "date,open,high,low,close,volume\n2026-07-06,1700,1710,1690,1705,100000\n",
        encoding="utf-8",
    )
    data_source = LocalCsvResearchDataSource(tmp_path)

    with pytest.raises(ValueError):
        data_source.load_ohlcv("600519.SH", timeframe)
