from pathlib import Path

import pytest

from freqtrade.research.exceptions import ResearchConfigError
from freqtrade.research.market_context import create_research_market_context
from freqtrade.research.profiles import load_research_profiles


def _profile(
    tmp_path,
    *,
    with_calendar: bool = True,
    with_status: bool = True,
    calendar: str = "calendar/trade_dates.csv",
    daily_status: str = "status/daily_status.csv",
):
    meta_root = tmp_path / "research_data" / "a_share_meta"
    (meta_root / "calendar").mkdir(parents=True)
    (meta_root / "status").mkdir(parents=True)
    if with_calendar:
        (meta_root / "calendar" / "trade_dates.csv").write_text(
            "date,is_open,source\n"
            "2026-07-06,1,test\n"
            "2026-07-07,1,test\n",
            encoding="utf-8",
        )
    if with_status:
        (meta_root / "status" / "daily_status.csv").write_text(
            "date,instrument,suspended,limit_up,limit_down,volume,listed_date,delisted_date,source\n"
            "2026-07-07,600519.SH,0,1800,1600,100000,2001-08-27,,test\n",
            encoding="utf-8",
        )
    config = {
        "user_data_dir": tmp_path,
        "research_bots": [
            {
                "id": "a-share-local",
                "label": "A Share Local",
                "market": "a_share",
                "data_source": {"type": "local_csv", "root": "research_data/a_share"},
                "market_data": {
                    "meta_root": "research_data/a_share_meta",
                    "calendar": calendar,
                    "daily_status": daily_status,
                },
            }
        ],
    }
    return load_research_profiles(config)[0]


def test_create_research_market_context_loads_configured_cache_files(tmp_path) -> None:
    context = create_research_market_context(_profile(tmp_path))

    assert context is not None
    assert context.calendar is not None
    assert context.calendar.is_trading_day("2026-07-07")
    assert context.status_store is not None
    assert context.status_store.get_status("600519.SH", "2026-07-07").limit_up == 1800


def test_create_research_market_context_returns_none_without_market_data(tmp_path) -> None:
    config = {
        "user_data_dir": tmp_path,
        "research_bots": [
            {
                "id": "a-share-local",
                "label": "A Share Local",
                "market": "a_share",
                "data_source": {"type": "local_csv", "root": "research_data/a_share"},
            }
        ],
    }
    profile = load_research_profiles(config)[0]

    assert create_research_market_context(profile) is None


def test_create_research_market_context_returns_none_when_cache_files_are_missing(tmp_path) -> None:
    context = create_research_market_context(
        _profile(tmp_path, with_calendar=False, with_status=False)
    )

    assert context is None


def test_create_research_market_context_loads_calendar_without_status_store(tmp_path) -> None:
    context = create_research_market_context(
        _profile(tmp_path, with_calendar=True, with_status=False)
    )

    assert context is not None
    assert context.calendar is not None
    assert context.calendar.is_trading_day("2026-07-07")
    assert context.status_store is None


def test_create_research_market_context_loads_status_store_without_calendar(tmp_path) -> None:
    context = create_research_market_context(
        _profile(tmp_path, with_calendar=False, with_status=True)
    )

    assert context is not None
    assert context.calendar is None
    assert context.status_store is not None
    assert context.status_store.get_status("600519.SH", "2026-07-07").limit_up == 1800


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("calendar", "../calendar/trade_dates.csv"),
        ("calendar", str((Path.cwd() / "calendar" / "trade_dates.csv").resolve())),
        ("daily_status", "../status/daily_status.csv"),
        ("daily_status", str((Path.cwd() / "status" / "daily_status.csv").resolve())),
    ],
)
def test_create_research_market_context_rejects_out_of_bounds_configured_paths(
    tmp_path,
    field_name,
    field_value,
) -> None:
    profile = _profile(tmp_path, **{field_name: field_value})

    with pytest.raises(
        ResearchConfigError,
        match=rf"Invalid market_data\.{field_name}",
    ):
        create_research_market_context(profile)
