from freqtrade.markets import MarketType
from freqtrade.research import load_research_profiles


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


def test_load_research_profiles_returns_empty_list_without_research_bots(tmp_path) -> None:
    config = {"user_data_dir": tmp_path}

    assert load_research_profiles(config) == []
