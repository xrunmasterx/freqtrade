from __future__ import annotations

import math
from numbers import Integral, Real

import pandas as pd
import talib.abstract as ta
from pandas import DataFrame


def add_supertrend(
    dataframe: DataFrame, period: int = 10, multiplier: float = 3.0, prefix: str = "supertrend"
) -> DataFrame:
    """Add Supertrend line columns to a dataframe copy."""
    _validate_supertrend_input(dataframe)
    _validate_supertrend_parameters(period, multiplier)

    result = dataframe.copy()
    supertrend, direction = calculate_supertrend(result, period, multiplier)
    result[f"{prefix}_up"] = supertrend.where(direction == 1)
    result[f"{prefix}_down"] = supertrend.where(direction == -1)
    result[f"{prefix}_price"] = result["close"]
    return result


def calculate_supertrend(
    dataframe: DataFrame, period: int, multiplier: float
) -> tuple[pd.Series, pd.Series]:
    """Calculate Supertrend values and their directions."""
    _validate_supertrend_input(dataframe)
    _validate_supertrend_parameters(period, multiplier)

    atr = ta.ATR(dataframe, timeperiod=period)
    hl2 = (dataframe["high"] + dataframe["low"]) / 2
    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr
    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()
    supertrend = pd.Series(float("nan"), index=dataframe.index, dtype="float64")
    direction = pd.Series(0, index=dataframe.index, dtype="int64")

    for index in range(len(dataframe)):
        if pd.isna(atr.iloc[index]):
            continue

        if index == 0 or direction.iloc[index - 1] == 0:
            final_upper.iloc[index] = basic_upper.iloc[index]
            final_lower.iloc[index] = basic_lower.iloc[index]
            if dataframe["close"].iloc[index] <= final_upper.iloc[index]:
                direction.iloc[index] = -1
                supertrend.iloc[index] = final_upper.iloc[index]
            else:
                direction.iloc[index] = 1
                supertrend.iloc[index] = final_lower.iloc[index]
            continue

        prev_final_upper = final_upper.iloc[index - 1]
        prev_final_lower = final_lower.iloc[index - 1]
        prev_close = dataframe["close"].iloc[index - 1]

        if pd.isna(prev_final_upper) or (
            basic_upper.iloc[index] < prev_final_upper or prev_close > prev_final_upper
        ):
            final_upper.iloc[index] = basic_upper.iloc[index]
        else:
            final_upper.iloc[index] = prev_final_upper

        if pd.isna(prev_final_lower) or (
            basic_lower.iloc[index] > prev_final_lower or prev_close < prev_final_lower
        ):
            final_lower.iloc[index] = basic_lower.iloc[index]
        else:
            final_lower.iloc[index] = prev_final_lower

        close = dataframe["close"].iloc[index]
        if direction.iloc[index - 1] == -1:
            if close > final_upper.iloc[index]:
                direction.iloc[index] = 1
                supertrend.iloc[index] = final_lower.iloc[index]
            else:
                direction.iloc[index] = -1
                supertrend.iloc[index] = final_upper.iloc[index]
        else:
            if close < final_lower.iloc[index]:
                direction.iloc[index] = -1
                supertrend.iloc[index] = final_upper.iloc[index]
            else:
                direction.iloc[index] = 1
                supertrend.iloc[index] = final_lower.iloc[index]

    return supertrend, direction


def _validate_supertrend_input(dataframe: DataFrame) -> None:
    required_columns = ("high", "low", "close")
    missing_columns = [col for col in required_columns if col not in dataframe.columns]
    if missing_columns:
        raise ValueError(f"Missing required column(s): {', '.join(missing_columns)}")


def _validate_supertrend_parameters(period: int, multiplier: float) -> None:
    if isinstance(period, bool) or not isinstance(period, Integral) or period < 1:
        raise ValueError("period must be an integer >= 1")

    if (
        isinstance(multiplier, bool)
        or not isinstance(multiplier, Real)
        or not math.isfinite(multiplier)
        or multiplier <= 0
    ):
        raise ValueError("multiplier must be a finite number > 0")

