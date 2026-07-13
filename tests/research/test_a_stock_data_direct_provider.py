import pandas as pd
import pytest

from freqtrade.markets import CachedAShareCalendar
from freqtrade.research.side_data.providers.a_stock_data_direct import (
    AStockDataDirectSideDataProvider,
)


@pytest.fixture
def calendar() -> CachedAShareCalendar:
    return CachedAShareCalendar(
        open_dates={
            pd.Timestamp("2026-07-07").date(),
            pd.Timestamp("2026-07-08").date(),
        },
        known_dates={
            pd.Timestamp("2026-07-07").date(),
            pd.Timestamp("2026-07-08").date(),
        },
    )


def test_a_stock_data_direct_provider_normalizes_sector_membership(
    mocker,
    calendar,
) -> None:
    response = mocker.Mock()
    response.json.return_value = {
        "data": {
            "diff": [
                {"f12": "BK0420", "f14": "白酒", "f3": 1.2, "f128": "600519"},
                {"f12": "BK1000", "f14": "贵州板块", "f3": 0.5, "f128": "600519"},
            ]
        }
    }
    response.raise_for_status.return_value = None
    mocker.patch("requests.Session.get", return_value=response)

    records = AStockDataDirectSideDataProvider(
        calendar,
        clock=lambda: pd.Timestamp("2026-07-08 06:00:00+00:00"),
    ).fetch_sector_membership("600519.SH")

    assert records[0]["dataset"] == "sector_membership"
    assert records[0]["instrument"] == "600519.SH"
    assert records[0]["payload"]["sector_name"] == "白酒"
    assert records[0]["effective_candle_time"] == "2026-07-08 00:00:00+00:00"


def test_a_stock_data_direct_provider_requires_calendar() -> None:
    with pytest.raises(ValueError, match="requires a trading calendar"):
        AStockDataDirectSideDataProvider().fetch_sector_membership("600519.SH")
