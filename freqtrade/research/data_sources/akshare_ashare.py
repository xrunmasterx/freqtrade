from importlib import import_module, metadata

import pandas as pd

from freqtrade.markets import MarketType, parse_instrument_key
from freqtrade.research.a_share_timeframes import is_a_share_minute_timeframe
from freqtrade.research.collectors.a_share_ohlcv import (
    AShareOhlcvCollectionError,
    provider_period_for_timeframe,
)


_AKSHARE_INSTALL_MESSAGE = (
    "Install optional dependency with `pip install -e .[research_ashare]` before using the "
    "akshare A-share collector."
)


class AkshareAshareOhlcvProvider:
    provider_name = "akshare"

    @property
    def provider_version(self) -> str:
        try:
            return metadata.version("akshare")
        except metadata.PackageNotFoundError:
            return "not-installed"

    def fetch_ohlcv(
        self,
        instrument_key: str,
        timeframe: str,
        start_date: str | None,
        end_date: str | None,
        adjustment: str,
    ) -> pd.DataFrame:
        if adjustment != "raw":
            raise AShareOhlcvCollectionError(
                f"Unsupported A-share OHLCV adjustment: {adjustment}"
            )

        instrument = parse_instrument_key(instrument_key, market=MarketType.A_SHARE)
        period = provider_period_for_timeframe(timeframe)
        akshare = _import_akshare()

        if is_a_share_minute_timeframe(timeframe):
            dataframe = akshare.stock_zh_a_minute(
                symbol=_to_sina_symbol(instrument),
                period=period,
                adjust="",
            )
            return _filter_minute_timerange(dataframe, start_date, end_date)

        return akshare.stock_zh_a_hist(
            symbol=instrument.symbol,
            period=period,
            start_date=start_date or "19700101",
            end_date=end_date or "22220101",
            adjust="",
        )

    def source_timestamp_semantics(self, timeframe: str) -> str:
        if is_a_share_minute_timeframe(timeframe):
            return "candle_close"
        return "candle_open"

    def provider_endpoint(self, timeframe: str) -> str:
        if is_a_share_minute_timeframe(timeframe):
            return "stock_zh_a_minute"
        return "stock_zh_a_hist"

    def history_depth_metadata(self, timeframe: str) -> dict[str, object]:
        if is_a_share_minute_timeframe(timeframe):
            return {
                "history_depth_policy": "provider_latest_bars",
                "provider_row_limit": 1970,
            }
        return {}


def _import_akshare():
    try:
        akshare = import_module("akshare")
    except ImportError as exc:
        raise AShareOhlcvCollectionError(_AKSHARE_INSTALL_MESSAGE) from exc

    if akshare is None:
        raise AShareOhlcvCollectionError(_AKSHARE_INSTALL_MESSAGE)

    return akshare


def _to_sina_symbol(instrument) -> str:
    if instrument.venue == "SSE":
        return f"sh{instrument.symbol}"
    if instrument.venue == "SZSE":
        return f"sz{instrument.symbol}"
    raise AShareOhlcvCollectionError(f"Unsupported A-share venue: {instrument.venue}")


def _filter_minute_timerange(
    dataframe: pd.DataFrame,
    start_date: str | None,
    end_date: str | None,
) -> pd.DataFrame:
    if dataframe.empty or (start_date is None and end_date is None):
        return dataframe

    timestamp_column = "day" if "day" in dataframe.columns else "时间"
    dates = pd.to_datetime(dataframe[timestamp_column], errors="raise").dt.strftime("%Y%m%d")
    mask = pd.Series(True, index=dataframe.index)
    if start_date is not None:
        mask &= dates >= start_date
    if end_date is not None:
        mask &= dates <= end_date
    return dataframe.loc[mask].reset_index(drop=True)
