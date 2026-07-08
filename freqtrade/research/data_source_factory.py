from freqtrade.research.data_source import LocalCsvResearchDataSource, ResearchMarketDataSource
from freqtrade.research.exceptions import ResearchConfigError
from freqtrade.research.profiles import ResearchBotProfile


def create_research_data_source(profile: ResearchBotProfile) -> ResearchMarketDataSource:
    if profile.data_source.type == "local_csv":
        return LocalCsvResearchDataSource(profile.data_root)
    raise ResearchConfigError(f"Unsupported research data source: {profile.data_source.type}")
