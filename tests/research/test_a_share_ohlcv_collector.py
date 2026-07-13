import json

import numpy as np
import pandas as pd
import pytest

from freqtrade.research.collectors.a_share_ohlcv import (
    RESEARCH_OHLCV_COLUMNS,
    AShareOhlcvCollectionError,
    AShareOhlcvCollector,
    AShareOhlcvRequest,
    normalize_provider_ohlcv,
    provider_period_for_timeframe,
)
from freqtrade.research.exceptions import ResearchUnsupportedFeatureError


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
                "date": ["2026-07-02", "2026-07-01"],
                "open": [1705, 1700],
                "high": [1715, 1710],
                "low": [1700, 1690],
                "close": [1710, 1705],
                "volume": [200000, 100000],
            }
        )

    def source_timestamp_semantics(self, timeframe: str) -> str:
        return "candle_open"

    def provider_endpoint(self, timeframe: str) -> str:
        return "fake_ohlcv"

    def history_depth_metadata(self, timeframe: str) -> dict[str, object]:
        return {}


class EmptyProvider:
    provider_name = "empty"
    provider_version = "2026.7"

    def fetch_ohlcv(
        self,
        instrument_key: str,
        timeframe: str,
        start_date: str | None,
        end_date: str | None,
        adjustment: str,
    ) -> pd.DataFrame:
        return pd.DataFrame(columns=RESEARCH_OHLCV_COLUMNS)


class CountingProvider(FakeOhlcvProvider):
    def __init__(self) -> None:
        self.calls = 0

    def fetch_ohlcv(
        self,
        instrument_key: str,
        timeframe: str,
        start_date: str | None,
        end_date: str | None,
        adjustment: str,
    ) -> pd.DataFrame:
        self.calls += 1
        return super().fetch_ohlcv(
            instrument_key,
            timeframe,
            start_date,
            end_date,
            adjustment,
        )


class UnsafeNameProvider(FakeOhlcvProvider):
    provider_name = "fake/provider:bad"


class MixedResultProvider(FakeOhlcvProvider):
    provider_name = "mixed"

    def fetch_ohlcv(
        self,
        instrument_key: str,
        timeframe: str,
        start_date: str | None,
        end_date: str | None,
        adjustment: str,
    ) -> pd.DataFrame:
        if instrument_key == "600519.SH":
            raise RuntimeError("provider failed for 600519.SH")

        return super().fetch_ohlcv(
            instrument_key,
            timeframe,
            start_date,
            end_date,
            adjustment,
        )


class FakeMinuteOhlcvProvider(FakeOhlcvProvider):
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
                "day": ["2026-07-07 09:31:00", "2026-07-07 09:32:00"],
                "open": [460, 461],
                "high": [461, 462],
                "low": [459, 460],
                "close": [460.5, 461.5],
                "volume": [1000, 1200],
            }
        )

    def source_timestamp_semantics(self, timeframe: str) -> str:
        return "candle_close"

    def provider_endpoint(self, timeframe: str) -> str:
        return "stock_zh_a_minute"

    def history_depth_metadata(self, timeframe: str) -> dict[str, object]:
        return {"history_depth_policy": "provider_latest_bars", "provider_row_limit": 1970}


@pytest.mark.parametrize(
    ("timeframe", "provider_period"),
    [
        ("1m", "1"),
        ("5m", "5"),
        ("15m", "15"),
        ("30m", "30"),
        ("60m", "60"),
        ("1d", "daily"),
    ],
)
def test_provider_period_for_supported_timeframes(timeframe: str, provider_period: str) -> None:
    assert provider_period_for_timeframe(timeframe) == provider_period


@pytest.mark.parametrize("timeframe", ["3m", "1h", "1w", "1M"])
def test_provider_period_rejects_unsupported_timeframe(timeframe: str) -> None:
    with pytest.raises(
        AShareOhlcvCollectionError,
        match=f"Unsupported A-share OHLCV timeframe: {timeframe}",
    ):
        provider_period_for_timeframe(timeframe)


def test_collector_writes_normalized_csv_and_manifest(tmp_path) -> None:
    collector = AShareOhlcvCollector(tmp_path, FakeOhlcvProvider())

    summary = collector.collect(
        AShareOhlcvRequest(
            instruments=["600519.SH", "000001.SZ"],
            timeframes=["1d"],
            start_date="20260701",
            end_date="20260731",
        )
    )

    assert summary.failed == 0
    assert summary.succeeded == 2
    csv_path = tmp_path / "600519.SH-1d.csv"
    assert csv_path.exists()
    csv_text = csv_path.read_text()
    assert csv_text.splitlines()[0] == "date,open,high,low,close,volume"
    assert "2026-07-01,1700.0,1710.0,1690.0,1705.0,100000.0" in csv_text

    manifests = list((tmp_path / ".manifests").glob("*.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text())
    assert manifest["market"] == "a_share"
    assert manifest["provider"] == "fake"
    assert manifest["provider_version"] == "2026.7"
    assert manifest["instruments"] == ["600519.SH", "000001.SZ"]
    assert manifest["timeframes"] == ["1d"]
    assert manifest["adjustment"] == "raw"
    assert manifest["timerange"] == {"start": "20260701", "end": "20260731"}
    assert manifest["files"][0]["status"] == "ok"
    assert summary.files[0].path == "600519.SH-1d.csv"
    assert str(tmp_path) not in summary.files[0].path
    assert manifest["files"][0]["path"] == "600519.SH-1d.csv"
    assert str(tmp_path) not in manifest["files"][0]["path"]


def test_collector_writes_minute_csv_and_manifest_timestamp_metadata(tmp_path) -> None:
    collector = AShareOhlcvCollector(tmp_path, FakeMinuteOhlcvProvider())

    summary = collector.collect(
        AShareOhlcvRequest(instruments=["688017.SH"], timeframes=["1m"])
    )

    assert summary.failed == 0
    csv_text = (tmp_path / "688017.SH-1m.csv").read_text(encoding="utf-8")
    assert "2026-07-07T01:30:00Z,460.0,461.0,459.0,460.5,1000.0" in csv_text

    manifest_path = next((tmp_path / ".manifests").glob("*.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["files"][0]["path"] == "688017.SH-1m.csv"
    assert manifest["timeframe_registry_version"] == "a_share_ohlcv_v1b"
    assert manifest["provider_endpoint"] == "stock_zh_a_minute"
    assert manifest["session_filter"] == "a_share_regular_session"
    assert manifest["timestamp_semantics"] == {
        "source_timezone": "Asia/Shanghai",
        "source_timestamp_semantics": "candle_close",
        "canonical_timezone": "UTC",
        "canonical_timestamp_semantics": "candle_open",
    }
    assert manifest["history_depth_policy"] == "provider_latest_bars"
    assert manifest["provider_row_limit"] == 1970


def test_collector_sanitizes_run_id_and_avoids_same_second_manifest_collision(tmp_path) -> None:
    collector = AShareOhlcvCollector(tmp_path, UnsafeNameProvider())
    request = AShareOhlcvRequest(instruments=["600519.SH"], timeframes=["1d"])

    first_summary = collector.collect(request)
    second_summary = collector.collect(request)

    assert first_summary.run_id != second_summary.run_id
    assert "fake-provider-bad" in first_summary.run_id
    assert "fake/provider:bad" not in first_summary.run_id
    manifests = sorted((tmp_path / ".manifests").glob("*.json"))
    assert len(manifests) == 2
    assert all(
        manifest.name.endswith("-fake-provider-bad-a-share-ohlcv.json")
        for manifest in manifests
    )

    manifest = json.loads(manifests[0].read_text())
    assert manifest["provider"] == "fake/provider:bad"


def test_collector_records_per_file_error_and_continues_to_later_file(tmp_path) -> None:
    collector = AShareOhlcvCollector(tmp_path, MixedResultProvider())

    summary = collector.collect(
        AShareOhlcvRequest(instruments=["600519.SH", "000001.SZ"], timeframes=["1d"])
    )

    assert summary.succeeded == 1
    assert summary.failed == 1
    assert [(file.path, file.status) for file in summary.files] == [
        ("600519.SH-1d.csv", "error"),
        ("000001.SZ-1d.csv", "ok"),
    ]
    assert summary.files[0].error == "provider failed for 600519.SH"
    assert not (tmp_path / "600519.SH-1d.csv").exists()
    assert (tmp_path / "000001.SZ-1d.csv").exists()

    manifest_path = next((tmp_path / ".manifests").glob("*.json"))
    manifest = json.loads(manifest_path.read_text())
    assert [(file["path"], file["status"]) for file in manifest["files"]] == [
        ("600519.SH-1d.csv", "error"),
        ("000001.SZ-1d.csv", "ok"),
    ]


def test_collector_does_not_overwrite_existing_file_on_empty_provider_data(tmp_path) -> None:
    csv_path = tmp_path / "600519.SH-1d.csv"
    existing_text = "date,open,high,low,close,volume\nexisting\n"
    csv_path.write_text(existing_text)
    collector = AShareOhlcvCollector(tmp_path, EmptyProvider())

    summary = collector.collect(AShareOhlcvRequest(instruments=["600519.SH"], timeframes=["1d"]))

    assert summary.succeeded == 0
    assert summary.failed == 1
    assert summary.files[0].status == "error"
    assert summary.files[0].rows == 0
    assert summary.files[0].error == "Provider returned empty OHLCV data."
    assert csv_path.read_text() == existing_text


@pytest.mark.parametrize("adjustment", ["qfq", "hfq"])
def test_collector_rejects_non_raw_adjustment(tmp_path, adjustment: str) -> None:
    provider = CountingProvider()
    collector = AShareOhlcvCollector(tmp_path, provider)

    with pytest.raises(
        AShareOhlcvCollectionError,
        match=f"Unsupported A-share OHLCV adjustment: {adjustment}",
    ):
        collector.collect(
            AShareOhlcvRequest(
                instruments=["600519.SH"],
                timeframes=["1d"],
                adjustment=adjustment,
            )
        )

    assert provider.calls == 0


@pytest.mark.parametrize(
    ("timeframe", "error_type", "message"),
    [
        ("3m", ResearchUnsupportedFeatureError, "Research timeframe 3m is not supported yet."),
        ("1h", ResearchUnsupportedFeatureError, "Research timeframe 1h is not supported yet."),
        ("1w", ResearchUnsupportedFeatureError, "Research timeframe 1w is not supported yet."),
        ("1M", ResearchUnsupportedFeatureError, "Research timeframe 1M is not supported yet."),
    ],
)
def test_collector_rejects_unsupported_timeframe_before_provider_call(
    tmp_path,
    timeframe: str,
    error_type: type[ValueError],
    message: str,
) -> None:
    provider = CountingProvider()
    collector = AShareOhlcvCollector(tmp_path, provider)

    with pytest.raises(error_type, match=message):
        collector.collect(AShareOhlcvRequest(instruments=["600519.SH"], timeframes=[timeframe]))

    assert provider.calls == 0
    assert list(tmp_path.iterdir()) == []


def test_collector_rejects_invalid_instrument_before_provider_call(tmp_path) -> None:
    provider = CountingProvider()
    collector = AShareOhlcvCollector(tmp_path, provider)

    with pytest.raises(
        AShareOhlcvCollectionError,
        match="Invalid A-share instrument key: 600519",
    ):
        collector.collect(AShareOhlcvRequest(instruments=["600519"], timeframes=["1d"]))

    assert provider.calls == 0
    assert list(tmp_path.iterdir()) == []


def test_normalize_provider_ohlcv_maps_provider_columns() -> None:
    provider_dataframe = pd.DataFrame(
        {
            "date": ["2026-07-07", "2026-07-06"],
            "open": ["1705", "1700"],
            "high": ["1715", "1710"],
            "low": ["1700", "1690"],
            "close": ["1710", "1705"],
            "volume": ["200000", "100000"],
            "ignored": ["x", "y"],
        }
    )

    dataframe, warnings = normalize_provider_ohlcv(provider_dataframe)

    assert warnings == []
    assert list(dataframe.columns) == RESEARCH_OHLCV_COLUMNS
    assert dataframe.to_dict("records") == [
        {
            "date": "2026-07-06",
            "open": 1700.0,
            "high": 1710.0,
            "low": 1690.0,
            "close": 1705.0,
            "volume": 100000.0,
        },
        {
            "date": "2026-07-07",
            "open": 1705.0,
            "high": 1715.0,
            "low": 1700.0,
            "close": 1710.0,
            "volume": 200000.0,
        },
    ]


def test_normalize_provider_ohlcv_maps_akshare_unicode_columns() -> None:
    provider_dataframe = pd.DataFrame(
        {
            "\u65e5\u671f": ["2026-07-06"],
            "\u5f00\u76d8": [1700],
            "\u6700\u9ad8": [1710],
            "\u6700\u4f4e": [1690],
            "\u6536\u76d8": [1705],
            "\u6210\u4ea4\u91cf": [100000],
        }
    )

    dataframe, warnings = normalize_provider_ohlcv(provider_dataframe)

    assert warnings == []
    assert list(dataframe.columns) == RESEARCH_OHLCV_COLUMNS
    assert dataframe.to_dict("records") == [
        {
            "date": "2026-07-06",
            "open": 1700.0,
            "high": 1710.0,
            "low": 1690.0,
            "close": 1705.0,
            "volume": 100000.0,
        }
    ]


def test_normalize_provider_ohlcv_preserves_minute_timestamps_as_utc_candle_open() -> None:
    provider_dataframe = pd.DataFrame(
        {
            "day": ["2026-07-07 09:31:00", "2026-07-07 09:32:00"],
            "open": [460, 461],
            "high": [461, 462],
            "low": [459, 460],
            "close": [460.5, 461.5],
            "volume": [1000, 1200],
            "amount": [460500, 553800],
        }
    )

    dataframe, warnings = normalize_provider_ohlcv(
        provider_dataframe,
        timeframe="1m",
        source_timestamp_semantics="candle_close",
    )

    assert warnings == []
    assert dataframe["date"].tolist() == [
        "2026-07-07T01:30:00Z",
        "2026-07-07T01:31:00Z",
    ]
    assert list(dataframe.columns) == RESEARCH_OHLCV_COLUMNS


def test_normalize_provider_ohlcv_subtracts_actual_non_1m_duration_for_candle_close() -> None:
    provider_dataframe = pd.DataFrame(
        {
            "day": ["2026-07-07 09:35:00", "2026-07-07 09:40:00"],
            "open": [460, 461],
            "high": [461, 462],
            "low": [459, 460],
            "close": [460.5, 461.5],
            "volume": [1000, 1200],
        }
    )

    dataframe, warnings = normalize_provider_ohlcv(
        provider_dataframe,
        timeframe="5m",
        source_timestamp_semantics="candle_close",
    )

    assert warnings == []
    assert dataframe["date"].tolist() == [
        "2026-07-07T01:30:00Z",
        "2026-07-07T01:35:00Z",
    ]


def test_normalize_provider_ohlcv_rejects_missing_columns() -> None:
    provider_dataframe = pd.DataFrame(
        {
            "date": ["2026-07-06"],
            "open": [1700],
            "high": [1710],
            "low": [1690],
            "close": [1705],
        }
    )

    with pytest.raises(
        AShareOhlcvCollectionError,
        match=r"Missing provider OHLCV columns: \['volume'\]",
    ):
        normalize_provider_ohlcv(provider_dataframe)


def test_normalize_provider_ohlcv_rejects_empty_dataframe() -> None:
    provider_dataframe = pd.DataFrame(columns=RESEARCH_OHLCV_COLUMNS)

    with pytest.raises(
        AShareOhlcvCollectionError,
        match=r"Provider returned empty OHLCV data\.",
    ):
        normalize_provider_ohlcv(provider_dataframe)


def test_normalize_provider_ohlcv_rejects_invalid_ohlc_relationship() -> None:
    provider_dataframe = pd.DataFrame(
        {
            "date": ["2026-07-06"],
            "open": [1700],
            "high": [1704],
            "low": [1690],
            "close": [1705],
            "volume": [100000],
        }
    )

    with pytest.raises(
        AShareOhlcvCollectionError,
        match=r"Invalid OHLC relationship\.",
    ):
        normalize_provider_ohlcv(provider_dataframe)


def test_normalize_provider_ohlcv_rejects_non_finite_values() -> None:
    provider_dataframe = pd.DataFrame(
        {
            "date": ["2026-07-06"],
            "open": [1700],
            "high": [np.inf],
            "low": [1690],
            "close": [1705],
            "volume": [100000],
        }
    )

    with pytest.raises(
        AShareOhlcvCollectionError,
        match=r"OHLCV values must be finite\.",
    ):
        normalize_provider_ohlcv(provider_dataframe)


@pytest.mark.parametrize("missing_date", [None, pd.NaT, np.nan])
def test_normalize_provider_ohlcv_rejects_missing_date_values(missing_date) -> None:
    provider_dataframe = pd.DataFrame(
        {
            "date": [missing_date],
            "open": [1700],
            "high": [1710],
            "low": [1690],
            "close": [1705],
            "volume": [100000],
        }
    )

    with pytest.raises(
        AShareOhlcvCollectionError,
        match=r"OHLCV dates must be valid\.",
    ):
        normalize_provider_ohlcv(provider_dataframe)


def test_normalize_provider_ohlcv_rejects_invalid_date_string() -> None:
    provider_dataframe = pd.DataFrame(
        {
            "date": ["not-a-date"],
            "open": [1700],
            "high": [1710],
            "low": [1690],
            "close": [1705],
            "volume": [100000],
        }
    )

    with pytest.raises(
        AShareOhlcvCollectionError,
        match=r"OHLCV dates must be valid\.",
    ):
        normalize_provider_ohlcv(provider_dataframe)


def test_normalize_provider_ohlcv_rejects_duplicate_missing_dates_before_deduplication() -> None:
    provider_dataframe = pd.DataFrame(
        {
            "date": [None, None],
            "open": [1700, 1700],
            "high": [1710, 1710],
            "low": [1690, 1690],
            "close": [1705, 1705],
            "volume": [100000, 100000],
        }
    )

    with pytest.raises(
        AShareOhlcvCollectionError,
        match=r"OHLCV dates must be valid\.",
    ):
        normalize_provider_ohlcv(provider_dataframe)


def test_normalize_provider_ohlcv_rejects_non_numeric_value_string() -> None:
    provider_dataframe = pd.DataFrame(
        {
            "date": ["2026-07-06"],
            "open": ["bad"],
            "high": [1710],
            "low": [1690],
            "close": [1705],
            "volume": [100000],
        }
    )

    with pytest.raises(
        AShareOhlcvCollectionError,
        match=r"OHLCV numeric values must be parseable\.",
    ):
        normalize_provider_ohlcv(provider_dataframe)


def test_normalize_provider_ohlcv_deduplicates_identical_dates_with_warning() -> None:
    provider_dataframe = pd.DataFrame(
        {
            "date": ["2026-07-06", "2026-07-06", "2026-07-07"],
            "open": [1700, 1700, 1705],
            "high": [1710, 1710, 1715],
            "low": [1690, 1690, 1700],
            "close": [1705, 1705, 1710],
            "volume": [100000, 100000, 200000],
        }
    )

    dataframe, warnings = normalize_provider_ohlcv(provider_dataframe)

    assert warnings == ["Dropped 1 identical duplicate OHLCV rows."]
    assert dataframe["date"].tolist() == ["2026-07-06", "2026-07-07"]


def test_normalize_provider_ohlcv_rejects_conflicting_duplicate_dates() -> None:
    provider_dataframe = pd.DataFrame(
        {
            "date": ["2026-07-06", "2026-07-06"],
            "open": [1700, 1701],
            "high": [1710, 1710],
            "low": [1690, 1690],
            "close": [1705, 1705],
            "volume": [100000, 100000],
        }
    )

    with pytest.raises(
        AShareOhlcvCollectionError,
        match=r"Conflicting duplicate OHLCV timestamps\.",
    ):
        normalize_provider_ohlcv(provider_dataframe)


def test_normalize_provider_ohlcv_rejects_conflicting_duplicate_minute_timestamps() -> None:
    provider_dataframe = pd.DataFrame(
        {
            "day": ["2026-07-07 09:31:00", "2026-07-07 09:31:00"],
            "open": [460, 461],
            "high": [461, 462],
            "low": [459, 460],
            "close": [460.5, 461.5],
            "volume": [1000, 1200],
        }
    )

    with pytest.raises(AShareOhlcvCollectionError, match="Conflicting duplicate OHLCV timestamps"):
        normalize_provider_ohlcv(
            provider_dataframe,
            timeframe="1m",
            source_timestamp_semantics="candle_close",
        )


def test_normalize_provider_ohlcv_rejects_out_of_session_minute_timestamp() -> None:
    provider_dataframe = pd.DataFrame(
        {
            "day": ["2026-07-07 11:31:00"],
            "open": [460],
            "high": [461],
            "low": [459],
            "close": [460.5],
            "volume": [1000],
        }
    )

    with pytest.raises(ValueError, match="A-share minute OHLCV contains out-of-session rows"):
        normalize_provider_ohlcv(
            provider_dataframe,
            timeframe="1m",
            source_timestamp_semantics="candle_close",
        )
