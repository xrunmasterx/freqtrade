from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


PARENT_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_script(name: str):
    path = PARENT_REPO_ROOT / "tools" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_calendar_download_script_help_does_not_import_akshare(capsys) -> None:
    script = _load_script("download_a_share_market_calendar.py")

    result = script.main(["--help"])

    captured = capsys.readouterr()
    assert result == 0
    assert "Download A-share trading calendar" in captured.out


def test_daily_status_download_script_help_does_not_import_akshare(capsys) -> None:
    script = _load_script("download_a_share_daily_status.py")

    result = script.main(["--help"])

    captured = capsys.readouterr()
    assert result == 0
    assert "Download A-share daily status snapshot" in captured.out


def test_normalize_calendar_output_columns() -> None:
    script = _load_script("download_a_share_market_calendar.py")
    dataframe = pd.DataFrame({"trade_date": ["2024-01-02", "2024-01-03"]})

    normalized = script.normalize_calendar(dataframe)

    assert list(normalized.columns) == ["date", "is_open", "source"]
    assert normalized.to_dict("records") == [
        {
            "date": "2024-01-02",
            "is_open": 1,
            "source": "akshare.tool_trade_date_hist_sina",
        },
        {
            "date": "2024-01-03",
            "is_open": 1,
            "source": "akshare.tool_trade_date_hist_sina",
        },
    ]


def test_normalize_daily_status_output_columns_and_suffix_mapping() -> None:
    script = _load_script("download_a_share_daily_status.py")
    dataframe = pd.DataFrame(
        {
            "date": ["2024-01-02", "2024-01-02"],
            "code": ["600519", "000001"],
            "volume": [32156, 99887],
            "limit_up": [1853.51, 12.34],
            "limit_down": [1516.51, 10.11],
            "listed_date": ["2001-08-27", ""],
            "delisted_date": ["", ""],
            "suspended": [0, 1],
        }
    )

    normalized = script.normalize_daily_status(dataframe)

    assert list(normalized.columns) == [
        "date",
        "instrument",
        "suspended",
        "limit_up",
        "limit_down",
        "volume",
        "listed_date",
        "delisted_date",
        "source",
    ]
    assert normalized.to_dict("records") == [
        {
            "date": "2024-01-02",
            "instrument": "600519.SH",
            "suspended": 0,
            "limit_up": 1853.51,
            "limit_down": 1516.51,
            "volume": 32156.0,
            "listed_date": "2001-08-27",
            "delisted_date": "",
            "source": "akshare.stock_zh_a_spot_em",
        },
        {
            "date": "2024-01-02",
            "instrument": "000001.SZ",
            "suspended": 1,
            "limit_up": 12.34,
            "limit_down": 10.11,
            "volume": 99887.0,
            "listed_date": "",
            "delisted_date": "",
            "source": "akshare.stock_zh_a_spot_em",
        },
    ]


def test_normalize_daily_status_missing_limit_columns_yields_empty_values() -> None:
    script = _load_script("download_a_share_daily_status.py")
    dataframe = pd.DataFrame(
        {
            "date": ["2024-01-02"],
            "code": ["300750"],
            "volume": [123456],
        }
    )

    normalized = script.normalize_daily_status(dataframe)
    record = normalized.to_dict("records")[0]

    assert record["instrument"] == "300750.SZ"
    assert pd.isna(record["limit_up"])
    assert pd.isna(record["limit_down"])
    assert record["source"] == "akshare.stock_zh_a_spot_em"


def test_normalize_daily_status_invalid_optional_dates_are_empty() -> None:
    script = _load_script("download_a_share_daily_status.py")
    dataframe = pd.DataFrame(
        {
            "date": ["2024-01-02"],
            "code": ["600519"],
            "volume": [123456],
            "listed_date": ["not-a-date"],
            "delisted_date": ["not-a-date"],
        }
    )

    normalized = script.normalize_daily_status(dataframe)
    record = normalized.to_dict("records")[0]

    assert record["listed_date"] == ""
    assert record["delisted_date"] == ""


def test_instrument_suffix_mapping() -> None:
    script = _load_script("download_a_share_daily_status.py")

    assert script.normalize_instrument("500001") == "500001.SH"
    assert script.normalize_instrument("600519") == "600519.SH"
    assert script.normalize_instrument("900901") == "900901.SH"
    assert script.normalize_instrument("000001") == "000001.SZ"
    assert script.normalize_instrument("300750") == "300750.SZ"
