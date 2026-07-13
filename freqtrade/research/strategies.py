from collections.abc import Callable
from operator import eq, ge, gt, le, lt

import pandas as pd
from pandas import DataFrame, Series

from freqtrade.research.feature_context import (
    FEATURE_FIELD_COLUMNS,
    ResearchFeatureContext,
    ResearchFeatureFilterConfig,
)


_OPERATORS: dict[str, Callable[[Series, float], Series]] = {
    ">": gt,
    ">=": ge,
    "<": lt,
    "<=": le,
    "==": eq,
}


def add_sma_cross_signals(dataframe: DataFrame, fast: int, slow: int) -> DataFrame:
    if fast <= 0 or slow <= 0:
        raise ValueError("SMA periods must be positive")
    if fast >= slow:
        raise ValueError("SMA fast period must be less than slow period")

    result = dataframe.copy()
    result["sma_fast"] = result["close"].rolling(window=fast, min_periods=fast).mean()
    result["sma_slow"] = result["close"].rolling(window=slow, min_periods=slow).mean()

    previous_fast = result["sma_fast"].shift(1)
    previous_slow = result["sma_slow"].shift(1)
    result["enter_long"] = (
        (result["sma_fast"] > result["sma_slow"]) & (previous_fast <= previous_slow)
    ).astype(int)
    result["exit_long"] = (
        (result["sma_fast"] < result["sma_slow"]) & (previous_fast >= previous_slow)
    ).astype(int)
    return result


def apply_feature_filter(
    dataframe: DataFrame,
    feature_context: ResearchFeatureContext,
    filter_config: ResearchFeatureFilterConfig,
) -> tuple[DataFrame, list[str]]:
    if filter_config.dataset != "fund_flow_daily":
        raise ValueError(f"Unsupported feature dataset: {filter_config.dataset}")

    feature_column = FEATURE_FIELD_COLUMNS[filter_config.field]
    if feature_column not in feature_context.frame.columns:
        raise ValueError(f"Missing research feature column: {feature_column}")

    result = dataframe.copy()
    result["date"] = pd.to_datetime(result["date"], utc=True)
    features = feature_context.frame[["date", feature_column]].copy()
    features["date"] = pd.to_datetime(features["date"], utc=True)

    result = result.merge(features, on="date", how="left")
    enter_mask = result["enter_long"].astype(int) == 1
    feature_values = pd.to_numeric(result[feature_column], errors="coerce")
    present_mask = feature_values.notna()
    pass_mask = _OPERATORS[filter_config.operator](feature_values, filter_config.value)
    if filter_config.missing == "allow":
        pass_mask = pass_mask | ~present_mask

    blocked_mask = enter_mask & ~pass_mask
    missing_blocked_mask = enter_mask & ~present_mask & (filter_config.missing == "block")
    blocked_count = int(blocked_mask.sum())
    missing_blocked_count = int(missing_blocked_mask.sum())

    result.loc[blocked_mask, "enter_long"] = 0

    warnings: list[str] = []
    if blocked_count:
        warnings.append(
            f"Feature filter blocked {blocked_count} enter_long signal(s): "
            f"{feature_column} {filter_config.operator} {filter_config.value}"
        )
    if missing_blocked_count:
        warnings.append(
            f"Feature filter blocked {missing_blocked_count} enter_long signal(s) "
            f"with missing feature value: {feature_column}"
        )

    return result, warnings
