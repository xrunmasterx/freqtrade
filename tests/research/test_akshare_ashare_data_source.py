import sys
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from freqtrade.research.collectors.a_share_ohlcv import AShareOhlcvCollectionError
from freqtrade.research.data_sources.akshare_ashare import AkshareAshareOhlcvProvider


def test_fetch_ohlcv_maps_instrument_timeframe_dates_and_raw_adjustment(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    expected_dataframe = pd.DataFrame({"date": ["2026-07-06"]})

    def stock_zh_a_hist(**kwargs) -> pd.DataFrame:
        calls.append(kwargs)
        return expected_dataframe

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_zh_a_hist=stock_zh_a_hist),
    )

    dataframe = AkshareAshareOhlcvProvider().fetch_ohlcv(
        "600519.SH",
        "1d",
        "20260701",
        "20260731",
        "raw",
    )

    assert dataframe is expected_dataframe
    assert calls == [
        {
            "symbol": "600519",
            "period": "daily",
            "start_date": "20260701",
            "end_date": "20260731",
            "adjust": "",
        }
    ]


def test_fetch_ohlcv_uses_default_akshare_date_bounds(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def stock_zh_a_hist(**kwargs) -> pd.DataFrame:
        calls.append(kwargs)
        return pd.DataFrame({"date": ["2026-07-06"]})

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_zh_a_hist=stock_zh_a_hist),
    )

    AkshareAshareOhlcvProvider().fetch_ohlcv("000001.SZ", "1d", None, None, "raw")

    assert calls == [
        {
            "symbol": "000001",
            "period": "daily",
            "start_date": "19700101",
            "end_date": "22220101",
            "adjust": "",
        }
    ]


def test_akshare_provider_maps_sse_minute_request_to_sina_symbol(monkeypatch) -> None:
    calls = []

    def stock_zh_a_minute(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame(
            {
                "day": ["2026-07-07 09:31:00"],
                "open": [460],
                "high": [461],
                "low": [459],
                "close": [460.5],
                "volume": [1000],
                "amount": [460500],
            }
        )

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_zh_a_minute=stock_zh_a_minute),
    )

    provider = AkshareAshareOhlcvProvider()
    dataframe = provider.fetch_ohlcv("688017.SH", "1m", None, None, "raw")

    assert len(dataframe) == 1
    assert calls == [{"symbol": "sh688017", "period": "1", "adjust": ""}]
    assert provider.source_timestamp_semantics("1m") == "candle_close"
    assert provider.provider_endpoint("1m") == "stock_zh_a_minute"
    assert provider.history_depth_metadata("1m") == {
        "history_depth_policy": "provider_latest_bars",
        "provider_row_limit": 1970,
    }


def test_akshare_provider_maps_szse_minute_request_to_sina_symbol(monkeypatch) -> None:
    calls = []

    def stock_zh_a_minute(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame(
            {
                "day": ["2026-07-07 09:31:00"],
                "open": [10],
                "high": [10.1],
                "low": [9.9],
                "close": [10.0],
                "volume": [1000],
            }
        )

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_zh_a_minute=stock_zh_a_minute),
    )

    provider = AkshareAshareOhlcvProvider()
    provider.fetch_ohlcv("000001.SZ", "5m", None, None, "raw")

    assert calls == [{"symbol": "sz000001", "period": "5", "adjust": ""}]


def test_akshare_provider_post_filters_minute_timerange(monkeypatch) -> None:
    def stock_zh_a_minute(**kwargs):
        return pd.DataFrame(
            {
                "day": [
                    "2026-07-01 09:31:00",
                    "2026-07-02 09:31:00",
                    "2026-07-03 09:31:00",
                ],
                "open": [1, 2, 3],
                "high": [1, 2, 3],
                "low": [1, 2, 3],
                "close": [1, 2, 3],
                "volume": [100, 200, 300],
            }
        )

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_zh_a_minute=stock_zh_a_minute),
    )

    provider = AkshareAshareOhlcvProvider()
    dataframe = provider.fetch_ohlcv("688017.SH", "1m", "20260702", "20260702", "raw")

    assert dataframe["day"].tolist() == ["2026-07-02 09:31:00"]


def test_fetch_ohlcv_missing_dependency_when_import_fails(monkeypatch) -> None:
    def import_module_raises(_name: str) -> None:
        raise ImportError("missing")

    monkeypatch.setattr(
        "freqtrade.research.data_sources.akshare_ashare.import_module",
        import_module_raises,
    )

    with pytest.raises(
        AShareOhlcvCollectionError,
        match=r"Install optional dependency with `pip install -e \.\[research_ashare\]`",
    ):
        AkshareAshareOhlcvProvider().fetch_ohlcv("600519.SH", "1d", None, None, "raw")


def test_fetch_ohlcv_missing_dependency_when_sys_modules_entry_is_none(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "akshare", None)

    with pytest.raises(
        AShareOhlcvCollectionError,
        match=r"Install optional dependency with `pip install -e \.\[research_ashare\]`",
    ):
        AkshareAshareOhlcvProvider().fetch_ohlcv("600519.SH", "1d", None, None, "raw")


def test_fetch_ohlcv_rejects_unsupported_adjustment_before_akshare_call(monkeypatch) -> None:
    def stock_zh_a_hist(**_kwargs) -> None:
        pytest.fail("akshare should not be called")

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_zh_a_hist=stock_zh_a_hist),
    )

    with pytest.raises(
        AShareOhlcvCollectionError,
        match="Unsupported A-share OHLCV adjustment: qfq",
    ):
        AkshareAshareOhlcvProvider().fetch_ohlcv("600519.SH", "1d", None, None, "qfq")


def test_fetch_ohlcv_rejects_invalid_instrument_before_akshare_call(monkeypatch) -> None:
    def stock_zh_a_hist(**_kwargs) -> None:
        pytest.fail("akshare should not be called")

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_zh_a_hist=stock_zh_a_hist),
    )

    with pytest.raises(ValueError, match="Invalid A-share instrument key: 600519"):
        AkshareAshareOhlcvProvider().fetch_ohlcv("600519", "1d", None, None, "raw")


@pytest.mark.parametrize("timeframe", ["1w", "1M"])
def test_fetch_ohlcv_rejects_unsupported_timeframe_before_akshare_import(
    monkeypatch,
    timeframe,
) -> None:
    def stock_zh_a_hist(**_kwargs) -> None:
        pytest.fail("akshare should not be called")

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_zh_a_hist=stock_zh_a_hist),
    )

    monkeypatch.setattr(
        "freqtrade.research.data_sources.akshare_ashare.import_module",
        lambda _name: pytest.fail("akshare should not be imported"),
    )

    with pytest.raises(
        AShareOhlcvCollectionError,
        match=f"Unsupported A-share OHLCV timeframe: {timeframe}",
    ):
        AkshareAshareOhlcvProvider().fetch_ohlcv("600519.SH", timeframe, None, None, "raw")
