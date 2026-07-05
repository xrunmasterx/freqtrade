import numpy as np
import pandas as pd
import pytest
from pandas.api.types import is_bool_dtype
from pandas.testing import assert_frame_equal, assert_series_equal

from freqtrade.indicators.qqe_mod import add_qqe_mod
from tests.conftest import generate_test_data


def _generate_constant_price_dataframe(size: int = 200, price: float = 100.0) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=size, freq="15min", tz="UTC")
    return pd.DataFrame(
        {
            "date": dates,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": 100.0,
        }
    )


def test_add_qqe_mod_returns_copied_dataframe_and_default_columns():
    dataframe = generate_test_data("15m", 250, "2024-01-01 00:00:00+00:00")
    original = dataframe.copy()

    result = add_qqe_mod(dataframe)

    assert_frame_equal(dataframe, original)

    expected_columns = {
        "qqe_mod_trend",
        "qqe_mod_hist",
        "qqe_mod_up",
        "qqe_mod_down",
        "qqe_mod_up_state",
        "qqe_mod_down_state",
        "qqe_mod_up_event",
        "qqe_mod_down_event",
    }
    assert expected_columns.issubset(result.columns)
    assert result["qqe_mod_hist"].notna().any()
    assert result["qqe_mod_trend"].notna().any()
    assert is_bool_dtype(result["qqe_mod_up_state"])
    assert is_bool_dtype(result["qqe_mod_down_state"])
    assert is_bool_dtype(result["qqe_mod_up_event"])
    assert is_bool_dtype(result["qqe_mod_down_event"])


def test_add_qqe_mod_supports_custom_prefix():
    dataframe = generate_test_data("15m", 250, "2024-01-01 00:00:00+00:00")

    result = add_qqe_mod(dataframe, prefix="custom_qqe")

    assert "custom_qqe_trend" in result.columns
    assert "custom_qqe_hist" in result.columns
    assert "custom_qqe_up" in result.columns
    assert "custom_qqe_down" in result.columns


def test_add_qqe_mod_preserves_nan_warmup_area():
    dataframe = generate_test_data("15m", 250, "2024-01-01 00:00:00+00:00")

    result = add_qqe_mod(dataframe)

    warmup_hist = result["qqe_mod_hist"].head(60)
    assert warmup_hist.isna().any()
    assert not warmup_hist.fillna(0.0).eq(0.0).all()

    trend = result["qqe_mod_trend"]
    first_valid_trend_index = trend.first_valid_index()
    assert first_valid_trend_index is not None
    assert trend.loc[: first_valid_trend_index - 1].isna().all()
    assert not trend.head(60).eq(-50.0).any()


def test_add_qqe_mod_signal_columns_match_state_columns():
    dataframe = generate_test_data("15m", 250, "2024-01-01 00:00:00+00:00")

    result = add_qqe_mod(dataframe)

    up_state = result["qqe_mod_up_state"]
    down_state = result["qqe_mod_down_state"]
    hist = result["qqe_mod_hist"]

    assert result.loc[~up_state, "qqe_mod_up"].isna().all()
    assert_series_equal(
        result.loc[up_state, "qqe_mod_up"],
        hist.loc[up_state],
        check_names=False,
    )
    assert result.loc[~down_state, "qqe_mod_down"].isna().all()
    assert_series_equal(
        result.loc[down_state, "qqe_mod_down"],
        hist.loc[down_state],
        check_names=False,
    )


def test_add_qqe_mod_event_columns_fire_only_on_state_transitions():
    dataframe = generate_test_data("15m", 250, "2024-01-01 00:00:00+00:00")

    result = add_qqe_mod(dataframe)

    expected_up_event = result["qqe_mod_up_state"] & ~result["qqe_mod_up_state"].shift(
        1, fill_value=False
    )
    expected_down_event = result["qqe_mod_down_state"] & ~result["qqe_mod_down_state"].shift(
        1, fill_value=False
    )

    assert_series_equal(result["qqe_mod_up_event"], expected_up_event, check_names=False)
    assert_series_equal(result["qqe_mod_down_event"], expected_down_event, check_names=False)


def test_add_qqe_mod_matches_fixed_reference_output():
    dataframe = generate_test_data("15m", 250, "2024-01-01 00:00:00+00:00")

    result = add_qqe_mod(dataframe)
    selected = result.loc[
        [80, 86, 100, 101, 106, 107],
        [
            "qqe_mod_trend",
            "qqe_mod_hist",
            "qqe_mod_up_state",
            "qqe_mod_down_state",
            "qqe_mod_up_event",
            "qqe_mod_down_event",
        ],
    ]

    # Captured from Mihkel00/TA-Lib Pine-style reference parity, not production output.
    expected = pd.DataFrame(
        {
            "qqe_mod_trend": [
                -1.214739,
                -0.342461,
                -1.130563,
                -1.130563,
                3.613775,
                3.613775,
            ],
            "qqe_mod_hist": [
                -4.885820,
                3.288231,
                -4.749565,
                -4.153164,
                7.166346,
                5.754193,
            ],
            "qqe_mod_up_state": [False, True, False, False, True, True],
            "qqe_mod_down_state": [True, False, True, True, False, False],
            "qqe_mod_up_event": [False, True, False, False, True, False],
            "qqe_mod_down_event": [True, False, True, False, False, False],
        },
        index=[80, 86, 100, 101, 106, 107],
    )

    np.testing.assert_allclose(
        selected["qqe_mod_trend"].to_numpy(),
        expected["qqe_mod_trend"].to_numpy(),
        rtol=1e-6,
        atol=1e-6,
    )
    np.testing.assert_allclose(
        selected["qqe_mod_hist"].to_numpy(),
        expected["qqe_mod_hist"].to_numpy(),
        rtol=1e-6,
        atol=1e-6,
    )
    assert_series_equal(
        selected["qqe_mod_up_state"], expected["qqe_mod_up_state"], check_names=False
    )
    assert_series_equal(
        selected["qqe_mod_down_state"], expected["qqe_mod_down_state"], check_names=False
    )
    assert_series_equal(
        selected["qqe_mod_up_event"], expected["qqe_mod_up_event"], check_names=False
    )
    assert_series_equal(
        selected["qqe_mod_down_event"], expected["qqe_mod_down_event"], check_names=False
    )


def test_add_qqe_mod_constant_price_is_neutral_after_warmup():
    dataframe = _generate_constant_price_dataframe()

    result = add_qqe_mod(dataframe)

    valid_hist = result["qqe_mod_hist"].dropna()
    assert not valid_hist.empty
    assert not result.loc[valid_hist.index, "qqe_mod_up_state"].any()
    assert not result.loc[valid_hist.index, "qqe_mod_down_state"].any()
    assert result.loc[valid_hist.index, "qqe_mod_up"].isna().all()
    assert result.loc[valid_hist.index, "qqe_mod_down"].isna().all()


def test_add_qqe_mod_raises_error_on_missing_source_column():
    dataframe = generate_test_data("15m", 250, "2024-01-01 00:00:00+00:00")

    with pytest.raises(ValueError, match="typical_price") as error:
        add_qqe_mod(dataframe, source="typical_price")

    assert "typical_price" in str(error.value)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"rsi_length": 0}, "rsi_length"),
        ({"rsi_length": True}, "rsi_length"),
        ({"qqe_factor": 0.0}, "qqe_factor"),
        ({"bollinger_multiplier": np.inf}, "bollinger_multiplier"),
    ],
)
def test_add_qqe_mod_rejects_invalid_numeric_parameters(kwargs, match):
    dataframe = generate_test_data("15m", 250, "2024-01-01 00:00:00+00:00")

    with pytest.raises(ValueError, match=match):
        add_qqe_mod(dataframe, **kwargs)


def test_add_qqe_mod_rejects_nan_threshold():
    dataframe = generate_test_data("15m", 250, "2024-01-01 00:00:00+00:00")

    with pytest.raises(ValueError, match="threshold"):
        add_qqe_mod(dataframe, threshold=np.nan)
