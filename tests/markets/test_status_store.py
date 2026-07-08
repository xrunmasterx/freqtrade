import pytest
from pandas import Timestamp

from freqtrade.markets.status_store import AShareStatusStore


def test_status_store_reads_daily_status(tmp_path) -> None:
    path = tmp_path / "a_share_daily_status.csv"
    path.write_text(
        "date,instrument,suspended,limit_up,limit_down,volume,listed_date,delisted_date,source\n"
        "2024-01-02,600519.SH,0,1853.51,1516.51,32156,2001-08-27,,snapshot\n",
        encoding="utf-8",
    )

    store = AShareStatusStore.from_csv(path)
    status = store.get_status("600519.SH", "2024-01-02")

    assert status is not None
    assert status.instrument == "600519.SH"
    assert status.suspended is False
    assert status.limit_up == 1853.51
    assert status.limit_down == 1516.51
    assert status.volume == 32156
    assert status.listed_date == "2001-08-27"
    assert status.delisted_date is None


def test_status_store_returns_none_for_missing_row(tmp_path) -> None:
    path = tmp_path / "a_share_daily_status.csv"
    path.write_text(
        "date,instrument,suspended,limit_up,limit_down,volume,listed_date,delisted_date,source\n",
        encoding="utf-8",
    )

    store = AShareStatusStore.from_csv(path)

    assert store.get_status("600519.SH", "2024-01-02") is None


def test_status_store_rejects_missing_columns(tmp_path) -> None:
    path = tmp_path / "a_share_daily_status.csv"
    path.write_text(
        "date,instrument,suspended,limit_up,limit_down,volume,listed_date,delisted_date\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Missing A-share status columns"):
        AShareStatusStore.from_csv(path)


def test_status_store_treats_empty_optional_numeric_and_date_fields_as_none(tmp_path) -> None:
    path = tmp_path / "a_share_daily_status.csv"
    path.write_text(
        "date,instrument,suspended,limit_up,limit_down,volume,listed_date,delisted_date,source\n"
        "2024-01-02,600519.SH,1,,,,,,snapshot\n",
        encoding="utf-8",
    )

    store = AShareStatusStore.from_csv(path)
    status = store.get_status("600519.SH", "2024-01-02")

    assert status is not None
    assert status.suspended is True
    assert status.limit_up is None
    assert status.limit_down is None
    assert status.volume is None
    assert status.listed_date is None
    assert status.delisted_date is None


def test_status_store_normalizes_instrument_keys(tmp_path) -> None:
    path = tmp_path / "a_share_daily_status.csv"
    path.write_text(
        "date,instrument,suspended,limit_up,limit_down,volume,listed_date,delisted_date,source\n"
        "2024-01-02,600519.sh,0,1853.51,1516.51,32156,2001-08-27,,snapshot\n",
        encoding="utf-8",
    )

    store = AShareStatusStore.from_csv(path)
    status = store.get_status("600519.sh", "2024-01-02")

    assert status is not None
    assert status.instrument == "600519.SH"


def test_status_store_normalizes_aware_lookup_to_shanghai_date(tmp_path) -> None:
    path = tmp_path / "a_share_daily_status.csv"
    path.write_text(
        "date,instrument,suspended,limit_up,limit_down,volume,listed_date,delisted_date,source\n"
        "2024-01-02,600519.SH,0,1853.51,1516.51,32156,2001-08-27,,snapshot\n",
        encoding="utf-8",
    )

    store = AShareStatusStore.from_csv(path)
    status = store.get_status("600519.SH", Timestamp("2024-01-01T16:30:00Z"))

    assert status is not None
    assert status.date == "2024-01-02"
