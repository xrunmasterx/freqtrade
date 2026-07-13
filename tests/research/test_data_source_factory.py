import pytest

from freqtrade.markets import MarketType
from freqtrade.research import LocalCsvResearchDataSource
from freqtrade.research.data_source_factory import create_research_data_source
from freqtrade.research.exceptions import ResearchConfigError
from freqtrade.research.profiles import ResearchBotProfile, ResearchDataSourceConfig


def _profile(tmp_path, data_source_type: str = "local_csv") -> ResearchBotProfile:
    data_source = ResearchDataSourceConfig(type="local_csv", root="research_data/a_share")
    if data_source_type != "local_csv":
        data_source.type = data_source_type
    return ResearchBotProfile(
        id="a-share-local",
        label="A Share Local",
        market=MarketType.A_SHARE,
        data_source=data_source,
        data_root=tmp_path / "research_data" / "a_share",
    )


def test_create_research_data_source_returns_local_csv_source(tmp_path) -> None:
    data_source = create_research_data_source(_profile(tmp_path))

    assert isinstance(data_source, LocalCsvResearchDataSource)
    assert data_source.root == tmp_path / "research_data" / "a_share"


def test_create_research_data_source_rejects_unsupported_source(tmp_path) -> None:
    profile = _profile(tmp_path, data_source_type="unknown")

    with pytest.raises(ResearchConfigError, match="Unsupported research data source: unknown"):
        create_research_data_source(profile)
