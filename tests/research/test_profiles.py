from pathlib import Path

import pytest

from freqtrade.markets import MarketType, ProductType
from freqtrade.research import load_research_profiles, research_profile_scope
from freqtrade.research.exceptions import ResearchConfigError


def test_load_research_profiles_parses_local_csv_profile(tmp_path) -> None:
    config = {
        "user_data_dir": tmp_path,
        "research_bots": [
            {
                "id": "a-share-local",
                "label": "A Share Local",
                "market": "a_share",
                "data_source": {
                    "type": "local_csv",
                    "root": "research_data/a_share",
                },
            }
        ],
    }

    profiles = load_research_profiles(config)

    assert len(profiles) == 1
    profile = profiles[0]
    assert profile.id == "a-share-local"
    assert profile.label == "A Share Local"
    assert profile.market == MarketType.A_SHARE
    assert profile.capabilities.live_trade is False
    assert profile.capabilities.account is False
    assert profile.capabilities.orders is False
    assert profile.data_root == tmp_path / "research_data" / "a_share"


def test_legacy_a_share_profile_maps_to_equity_scope(tmp_path) -> None:
    profile = load_research_profiles(
        {
            "user_data_dir": tmp_path,
            "research_bots": [
                {
                    "id": "a-share-local",
                    "label": "A Share Local",
                    "market": "a_share",
                    "data_source": {
                        "type": "local_csv",
                        "root": "research_data/a_share",
                    },
                }
            ],
        }
    )[0]

    scope = research_profile_scope(profile)

    assert scope.market_id == MarketType.A_SHARE
    assert scope.product_ids == (ProductType.EQUITY,)


@pytest.mark.parametrize(
    "market",
    [
        MarketType.DIGITAL_ASSET,
        MarketType.CONTRACT,
        MarketType.HK_STOCK,
        MarketType.US_STOCK,
    ],
)
def test_research_profile_scope_rejects_unsupported_market(tmp_path, market) -> None:
    profile = load_research_profiles(
        {
            "user_data_dir": tmp_path,
            "research_bots": [
                {
                    "id": "a-share-local",
                    "label": "A Share Local",
                    "market": "a_share",
                    "data_source": {
                        "type": "local_csv",
                        "root": "research_data/a_share",
                    },
                }
            ],
        }
    )[0].model_copy(update={"market": market})

    with pytest.raises(
        ResearchConfigError,
        match=rf"Unsupported research profile market: {market}",
    ):
        research_profile_scope(profile)


def test_load_research_profiles_returns_empty_list_without_research_bots(tmp_path) -> None:
    config = {"user_data_dir": tmp_path}

    assert load_research_profiles(config) == []


def test_load_research_profiles_accepts_optional_market_and_side_data(tmp_path) -> None:
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
                    "calendar": "calendar/trade_dates.csv",
                    "daily_status": "status/daily_status.csv",
                },
                "side_data": {
                    "root": "research_data/a_share_meta",
                    "enabled_datasets": ["fund_flow_daily", "limit_pool"],
                },
            }
        ],
    }

    profile = load_research_profiles(config)[0]

    assert profile.market_data is not None
    assert profile.market_data_root == tmp_path / "research_data" / "a_share_meta"
    assert profile.market_data.calendar == "calendar/trade_dates.csv"
    assert profile.market_data.daily_status == "status/daily_status.csv"
    assert profile.side_data is not None
    assert profile.side_data_root == tmp_path / "research_data" / "a_share_meta"
    assert profile.side_data.enabled_datasets == ["fund_flow_daily", "limit_pool"]


def test_load_research_profiles_resolves_approved_external_input_root(tmp_path) -> None:
    user_data_dir = tmp_path / "state"
    input_root = tmp_path / "research-input"
    input_root.mkdir()
    config = {
        "user_data_dir": user_data_dir,
        "research_input_root": input_root,
        "research_bots": [
            {
                "id": "a-share-local",
                "label": "A Share Local",
                "market": "a_share",
                "data_source": {"type": "local_csv", "root": "a_share"},
                "market_data": {"meta_root": "a_share_meta"},
                "side_data": {"root": "a_share_meta"},
            }
        ],
    }

    profile = load_research_profiles(config)[0]

    assert profile.data_root == input_root / "a_share"
    assert profile.market_data_root == input_root / "a_share_meta"
    assert profile.side_data_root == input_root / "a_share_meta"


def test_load_research_profiles_preserves_legacy_user_data_relative_roots(tmp_path) -> None:
    config = {
        "user_data_dir": tmp_path,
        "research_bots": [
            {
                "id": "a-share-local",
                "label": "A Share Local",
                "market": "a_share",
                "data_source": {"type": "local_csv", "root": "research_data/a_share"},
                "market_data": {"meta_root": "research_data/a_share_meta"},
                "side_data": {"root": "research_data/a_share_meta"},
            }
        ],
    }

    profile = load_research_profiles(config)[0]

    assert profile.data_root == tmp_path / "research_data" / "a_share"
    assert profile.market_data_root == tmp_path / "research_data" / "a_share_meta"
    assert profile.side_data_root == tmp_path / "research_data" / "a_share_meta"


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("data_source", "root"),
        ("market_data", "meta_root"),
        ("side_data", "root"),
    ],
)
def test_load_research_profiles_rejects_input_child_traversal(tmp_path, section, field) -> None:
    input_root = tmp_path / "research-input"
    input_root.mkdir()
    profile = {
        "id": "a-share-local",
        "label": "A Share Local",
        "market": "a_share",
        "data_source": {"type": "local_csv", "root": "a_share"},
        "market_data": {"meta_root": "a_share_meta"},
        "side_data": {"root": "a_share_meta"},
    }
    profile[section][field] = "nested/../escape"
    config = {
        "user_data_dir": tmp_path / "state",
        "research_input_root": input_root,
        "research_bots": [profile],
    }

    with pytest.raises(ResearchConfigError):
        load_research_profiles(config)


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("data_source", "root"),
        ("market_data", "meta_root"),
        ("side_data", "root"),
    ],
)
def test_load_research_profiles_rejects_absolute_child_outside_input_root(
    tmp_path,
    section,
    field,
) -> None:
    input_root = tmp_path / "research-input"
    input_root.mkdir()
    profile = {
        "id": "a-share-local",
        "label": "A Share Local",
        "market": "a_share",
        "data_source": {"type": "local_csv", "root": "a_share"},
        "market_data": {"meta_root": "a_share_meta"},
        "side_data": {"root": "a_share_meta"},
    }
    profile[section][field] = str(tmp_path / "outside")
    config = {
        "user_data_dir": tmp_path / "state",
        "research_input_root": input_root,
        "research_bots": [profile],
    }

    with pytest.raises(ResearchConfigError):
        load_research_profiles(config)


def test_load_research_profiles_rejects_symbolic_link_escape_from_input_root(tmp_path) -> None:
    input_root = tmp_path / "research-input"
    outside = tmp_path / "outside"
    input_root.mkdir()
    outside.mkdir()
    try:
        (input_root / "linked").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")
    config = {
        "user_data_dir": tmp_path / "state",
        "research_input_root": input_root,
        "research_bots": [
            {
                "id": "a-share-local",
                "label": "A Share Local",
                "market": "a_share",
                "data_source": {"type": "local_csv", "root": "linked"},
            }
        ],
    }

    with pytest.raises(ResearchConfigError):
        load_research_profiles(config)


@pytest.mark.parametrize(
    ("config_key", "config_value", "error_location"),
    [
        (
            "market_data",
            {"meta_root": "../a_share_meta"},
            r"Invalid research_bots\[0\]\.market_data\.meta_root",
        ),
        (
            "market_data",
            {"meta_root": str((Path.cwd() / "a_share_meta").resolve())},
            r"Invalid research_bots\[0\]\.market_data\.meta_root",
        ),
        (
            "side_data",
            {"root": "../side_data"},
            r"Invalid research_bots\[0\]\.side_data\.root",
        ),
        (
            "side_data",
            {"root": str((Path.cwd() / "side_data").resolve())},
            r"Invalid research_bots\[0\]\.side_data\.root",
        ),
    ],
)
def test_load_research_profiles_rejects_market_and_side_roots_outside_user_data_dir(
    tmp_path,
    config_key,
    config_value,
    error_location,
) -> None:
    config = {
        "user_data_dir": tmp_path,
        "research_bots": [
            {
                "id": "a-share-local",
                "label": "A Share Local",
                "market": "a_share",
                "data_source": {"type": "local_csv", "root": "research_data/a_share"},
                config_key: config_value,
            }
        ],
    }

    with pytest.raises(ResearchConfigError, match=error_location):
        load_research_profiles(config)


def test_load_research_profiles_reports_missing_data_source_location(tmp_path) -> None:
    config = {
        "user_data_dir": tmp_path,
        "research_bots": [
            {
                "id": "a-share-local",
                "label": "A Share Local",
                "market": "a_share",
            }
        ],
    }

    with pytest.raises(ResearchConfigError, match=r"Missing research_bots\[0\]\.data_source"):
        load_research_profiles(config)


def test_load_research_profiles_reports_missing_data_source_root_location(tmp_path) -> None:
    config = {
        "user_data_dir": tmp_path,
        "research_bots": [
            {
                "id": "a-share-local",
                "label": "A Share Local",
                "market": "a_share",
                "data_source": {
                    "type": "local_csv",
                },
            }
        ],
    }

    with pytest.raises(
        ResearchConfigError,
        match=r"Invalid research_bots\[0\]\.data_source\.root",
    ):
        load_research_profiles(config)


def test_load_research_profiles_rejects_unsupported_market(tmp_path) -> None:
    config = {
        "user_data_dir": tmp_path,
        "research_bots": [
            {
                "id": "hk-local",
                "label": "HK Local",
                "market": "hk_stock",
                "data_source": {
                    "type": "local_csv",
                    "root": "research_data/hk",
                },
            }
        ],
    }

    with pytest.raises(
        ResearchConfigError,
        match=r"Unsupported research_bots\[0\]\.market: hk_stock",
    ):
        load_research_profiles(config)
