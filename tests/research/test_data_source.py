import pandas as pd
import pytest

from freqtrade.research import LocalCsvResearchDataSource
from freqtrade.research.data_source import RESEARCH_OHLCV_COLUMNS


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


def test_local_csv_research_data_source_lists_instruments_with_hyphen_timeframes(tmp_path) -> None:
    (tmp_path / "600519.SH-1-day.csv").write_text(
        "date,open,high,low,close,volume\n2026-07-06,1700,1710,1690,1705,100000\n",
        encoding="utf-8",
    )

    data_source = LocalCsvResearchDataSource(tmp_path)

    assert [instrument.key for instrument in data_source.list_instruments()] == ["600519.SH"]


def test_local_csv_research_data_source_loads_normalized_ohlcv(tmp_path) -> None:
    (tmp_path / "600519.SH-1d.csv").write_text(
        "date,open,high,low,close,volume,ignored\n"
        "2026-07-07,1705,1715,1700,1710,200000,x\n"
        "2026-07-06,1700,1710,1690,1705,100000,y\n",
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
