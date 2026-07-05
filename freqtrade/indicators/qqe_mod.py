from __future__ import annotations

import numpy as np
import pandas as pd
import talib
from pandas import DataFrame, Series


def add_qqe_mod(
    dataframe: DataFrame,
    rsi_length: int = 6,
    rsi_smoothing: int = 5,
    qqe_factor: float = 3.0,
    bollinger_length: int = 50,
    bollinger_multiplier: float = 0.35,
    secondary_rsi_length: int = 6,
    secondary_rsi_smoothing: int = 5,
    secondary_qqe_factor: float = 1.61,
    threshold: float = 3.0,
    source: str = "close",
    prefix: str = "qqe_mod",
) -> DataFrame:
    _validate_qqe_mod_input(
        dataframe=dataframe,
        rsi_length=rsi_length,
        rsi_smoothing=rsi_smoothing,
        qqe_factor=qqe_factor,
        bollinger_length=bollinger_length,
        bollinger_multiplier=bollinger_multiplier,
        secondary_rsi_length=secondary_rsi_length,
        secondary_rsi_smoothing=secondary_rsi_smoothing,
        secondary_qqe_factor=secondary_qqe_factor,
        threshold=threshold,
        source=source,
    )

    result = dataframe.copy()
    source_series = pd.to_numeric(result[source], errors="coerce").astype("float64")

    primary = _qqe_pass(source_series, rsi_length, rsi_smoothing, qqe_factor)
    primary_trail_offset = primary["trail"] - 50.0
    bb_basis = _sma(primary_trail_offset, bollinger_length)
    bb_dev = _stddev(primary_trail_offset, bollinger_length) * bollinger_multiplier
    upper = bb_basis + bb_dev
    lower = bb_basis - bb_dev

    secondary = _qqe_pass(
        source_series,
        secondary_rsi_length,
        secondary_rsi_smoothing,
        secondary_qqe_factor,
    )
    trend = secondary["trail"] - 50.0
    hist = secondary["rsi_ma"] - 50.0

    primary_rsi_offset = primary["rsi_ma"] - 50.0
    up_state = (hist > threshold) & (primary_rsi_offset > upper)
    down_state = (hist < -threshold) & (primary_rsi_offset < lower)
    up_state = up_state.fillna(False).astype(bool)
    down_state = down_state.fillna(False).astype(bool)
    up_event = up_state & ~up_state.shift(1, fill_value=False)
    down_event = down_state & ~down_state.shift(1, fill_value=False)

    result[f"{prefix}_trend"] = trend
    result[f"{prefix}_hist"] = hist
    result[f"{prefix}_up"] = hist.where(up_state)
    result[f"{prefix}_down"] = hist.where(down_state)
    result[f"{prefix}_up_state"] = up_state
    result[f"{prefix}_down_state"] = down_state
    result[f"{prefix}_up_event"] = up_event
    result[f"{prefix}_down_event"] = down_event
    return result


def _qqe_pass(source: Series, rsi_length: int, rsi_smoothing: int, qqe_factor: float) -> DataFrame:
    rsi = _rsi(source, rsi_length)
    rsi_ma = _ema(rsi, rsi_smoothing)
    wilders_length = rsi_length * 2 - 1
    atr_rsi = (rsi_ma.shift(1) - rsi_ma).abs()
    ma_atr_rsi = _ema(atr_rsi, wilders_length)
    dar = _ema(ma_atr_rsi, wilders_length) * qqe_factor

    longband = pd.Series(np.nan, index=source.index, dtype="float64")
    shortband = pd.Series(np.nan, index=source.index, dtype="float64")
    trend_direction = pd.Series(np.nan, index=source.index, dtype="float64")
    trail = pd.Series(np.nan, index=source.index, dtype="float64")

    for index in range(len(source)):
        rsi_value = rsi_ma.iloc[index]
        dar_value = dar.iloc[index]
        if pd.isna(rsi_value) or pd.isna(dar_value):
            continue

        new_longband = rsi_value - dar_value
        new_shortband = rsi_value + dar_value

        prev_rsi_value = rsi_ma.iloc[index - 1] if index >= 1 else np.nan
        prev_longband = longband.iloc[index - 1] if index >= 1 else np.nan
        prev_shortband = shortband.iloc[index - 1] if index >= 1 else np.nan

        if (
            not pd.isna(prev_rsi_value)
            and not pd.isna(prev_longband)
            and prev_rsi_value > prev_longband
            and rsi_value > prev_longband
        ):
            longband.iloc[index] = max(prev_longband, new_longband)
        else:
            longband.iloc[index] = new_longband

        if (
            not pd.isna(prev_rsi_value)
            and not pd.isna(prev_shortband)
            and prev_rsi_value < prev_shortband
            and rsi_value < prev_shortband
        ):
            shortband.iloc[index] = min(prev_shortband, new_shortband)
        else:
            shortband.iloc[index] = new_shortband

        crossed_above_short = False
        crossed_below_long = False
        if index >= 2:
            crossed_above_short = _crossed(
                rsi_ma.iloc[index - 1],
                rsi_value,
                shortband.iloc[index - 2],
                shortband.iloc[index - 1],
            )
            crossed_below_long = _crossed(
                longband.iloc[index - 2],
                longband.iloc[index - 1],
                rsi_ma.iloc[index - 1],
                rsi_value,
            )

        if crossed_above_short:
            trend_direction.iloc[index] = 1.0
        elif crossed_below_long:
            trend_direction.iloc[index] = -1.0
        elif index >= 1 and not pd.isna(trend_direction.iloc[index - 1]):
            trend_direction.iloc[index] = trend_direction.iloc[index - 1]
        else:
            trend_direction.iloc[index] = 1.0

        if trend_direction.iloc[index] == 1.0:
            trail.iloc[index] = longband.iloc[index]
        else:
            trail.iloc[index] = shortband.iloc[index]

    return DataFrame(
        {
            "rsi": rsi,
            "rsi_ma": rsi_ma,
            "longband": longband,
            "shortband": shortband,
            "trail": trail,
        }
    )


def _rsi(series: Series, length: int) -> Series:
    return _talib_series(talib.RSI(series.to_numpy(dtype="float64"), timeperiod=length), series)


def _ema(series: Series, length: int) -> Series:
    return _talib_series(talib.EMA(series.to_numpy(dtype="float64"), timeperiod=length), series)


def _sma(series: Series, length: int) -> Series:
    return _talib_series(talib.SMA(series.to_numpy(dtype="float64"), timeperiod=length), series)


def _stddev(series: Series, length: int) -> Series:
    return _talib_series(
        talib.STDDEV(series.to_numpy(dtype="float64"), timeperiod=length, nbdev=1.0),
        series,
    )


def _talib_series(values: np.ndarray, template: Series) -> Series:
    return pd.Series(values, index=template.index, dtype="float64")


def _crossed(
    previous_left: float,
    current_left: float,
    previous_right: float,
    current_right: float,
) -> bool:
    values = (previous_left, current_left, previous_right, current_right)
    if any(pd.isna(value) for value in values):
        return False

    previous_diff = previous_left - previous_right
    current_diff = current_left - current_right
    return (previous_diff <= 0.0 < current_diff) or (previous_diff >= 0.0 > current_diff)


def _validate_qqe_mod_input(
    dataframe: DataFrame,
    rsi_length: int,
    rsi_smoothing: int,
    qqe_factor: float,
    bollinger_length: int,
    bollinger_multiplier: float,
    secondary_rsi_length: int,
    secondary_rsi_smoothing: int,
    secondary_qqe_factor: float,
    threshold: float,
    source: str,
) -> None:
    if source not in dataframe.columns:
        raise ValueError(f"Missing required source column: {source}")

    integer_params = {
        "rsi_length": rsi_length,
        "rsi_smoothing": rsi_smoothing,
        "bollinger_length": bollinger_length,
        "secondary_rsi_length": secondary_rsi_length,
        "secondary_rsi_smoothing": secondary_rsi_smoothing,
    }
    for name, value in integer_params.items():
        if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value < 1:
            raise ValueError(f"{name} must be an integer >= 1")

    positive_float_params = {
        "qqe_factor": qqe_factor,
        "bollinger_multiplier": bollinger_multiplier,
        "secondary_qqe_factor": secondary_qqe_factor,
        "threshold": threshold,
    }
    for name, value in positive_float_params.items():
        if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)):
            raise ValueError(f"{name} must be a finite number > 0")
        if not np.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be a finite number > 0")
