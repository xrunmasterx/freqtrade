from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from freqtrade.markets import BotCapabilities, MarketType


class ResearchDataSourceConfig(BaseModel):
    type: Literal["local_csv"]
    root: str


class ResearchBotProfile(BaseModel):
    id: str
    label: str
    market: MarketType
    data_source: ResearchDataSourceConfig
    capabilities: BotCapabilities = Field(default_factory=BotCapabilities.research)
    data_root: Path


def load_research_profiles(config: dict[str, Any]) -> list[ResearchBotProfile]:
    user_data_dir = Path(config["user_data_dir"])
    profiles = []

    for raw_profile in config.get("research_bots", []):
        data_source = ResearchDataSourceConfig(**raw_profile["data_source"])
        profiles.append(
            ResearchBotProfile(
                id=raw_profile["id"],
                label=raw_profile["label"],
                market=MarketType(raw_profile["market"]),
                data_source=data_source,
                data_root=user_data_dir / data_source.root,
            )
        )

    return profiles
