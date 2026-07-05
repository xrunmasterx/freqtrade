from pandas import DataFrame


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
