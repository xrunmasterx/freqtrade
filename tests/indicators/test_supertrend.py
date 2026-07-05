import pandas as pd
import pytest
from pandas.testing import assert_frame_equal, assert_series_equal

from freqtrade.indicators.supertrend import add_supertrend, calculate_supertrend
from tests.conftest import generate_test_data


def test_add_supertrend_returns_copied_dataframe_and_direction_output():
    dataframe = generate_test_data("15m", 80, "2024-01-01 00:00:00+00:00")
    original = dataframe.copy()

    result = add_supertrend(dataframe)

    assert_frame_equal(dataframe, original)
    assert "supertrend_up" in result.columns
    assert "supertrend_down" in result.columns
    assert "supertrend_price" in result.columns
    assert_series_equal(result["supertrend_price"], dataframe["close"], check_names=False)

    populated_direction_values = result[["supertrend_up", "supertrend_down"]].notna().sum(axis=1)
    assert populated_direction_values.max() <= 1
    assert populated_direction_values.sum() > 0


def test_add_supertrend_supports_custom_prefix():
    dataframe = generate_test_data("15m", 80, "2024-01-01 00:00:00+00:00")

    result = add_supertrend(dataframe, period=7, multiplier=2.5, prefix="custom_st")

    assert "custom_st_up" in result.columns
    assert "custom_st_down" in result.columns
    assert "custom_st_price" in result.columns
    assert_series_equal(result["custom_st_price"], dataframe["close"], check_names=False)


def test_supertrend_matches_expected_bands_after_direction_transition():
    dataframe = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=5, freq="15min", tz="UTC"),
            "open": [10.0, 10.0, 10.0, 12.0, 13.0],
            "high": [10.0, 10.0, 10.0, 12.0, 13.0],
            "low": [10.0, 10.0, 10.0, 12.0, 13.0],
            "close": [10.0, 10.0, 10.0, 12.0, 13.0],
            "volume": [100.0, 100.0, 100.0, 100.0, 100.0],
        }
    )

    result = add_supertrend(dataframe, period=2, multiplier=1.0, prefix="st")
    expected_up = pd.Series([float("nan"), float("nan"), float("nan"), 11.0, 12.0])
    expected_down = pd.Series([float("nan"), float("nan"), 10.0, float("nan"), float("nan")])

    assert_series_equal(result["st_up"], expected_up, check_names=False)
    assert_series_equal(result["st_down"], expected_down, check_names=False)


@pytest.mark.parametrize("missing_column", ["high", "low", "close"])
@pytest.mark.parametrize("entrypoint", [add_supertrend, calculate_supertrend])
def test_supertrend_entrypoints_raise_error_on_missing_required_column(
    entrypoint, missing_column
):
    dataframe = generate_test_data("15m", 80, "2024-01-01 00:00:00+00:00").drop(
        columns=[missing_column]
    )

    with pytest.raises(ValueError, match=missing_column) as error:
        entrypoint(dataframe, period=10, multiplier=3.0)
    assert missing_column in str(error.value)


@pytest.mark.parametrize("entrypoint", [add_supertrend, calculate_supertrend])
@pytest.mark.parametrize("period", [0, -1, 1.5, True, "10"])
def test_supertrend_entrypoints_reject_invalid_period(entrypoint, period):
    dataframe = generate_test_data("15m", 80, "2024-01-01 00:00:00+00:00")

    with pytest.raises(ValueError, match="period"):
        entrypoint(dataframe, period=period, multiplier=3.0)


@pytest.mark.parametrize("entrypoint", [add_supertrend, calculate_supertrend])
@pytest.mark.parametrize("multiplier", [0, -1, True, float("nan"), float("inf"), "3.0"])
def test_supertrend_entrypoints_reject_invalid_multiplier(entrypoint, multiplier):
    dataframe = generate_test_data("15m", 80, "2024-01-01 00:00:00+00:00")

    with pytest.raises(ValueError, match="multiplier"):
        entrypoint(dataframe, period=10, multiplier=multiplier)
