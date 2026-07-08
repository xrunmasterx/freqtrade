from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from freqtrade.markets import BotCapabilities, MarketType
from freqtrade.research.exceptions import ResearchConfigError


class ResearchDataSourceConfig(BaseModel):
    type: Literal["local_csv"]
    root: str


class ResearchMarketDataConfig(BaseModel):
    meta_root: str
    calendar: str = "calendar/trade_dates.csv"
    daily_status: str = "status/daily_status.csv"


class ResearchSideDataConfig(BaseModel):
    root: str
    enabled_datasets: list[str] = Field(default_factory=list)


class ResearchBotProfile(BaseModel):
    id: str
    label: str
    market: MarketType
    data_source: ResearchDataSourceConfig
    market_data: ResearchMarketDataConfig | None = None
    side_data: ResearchSideDataConfig | None = None
    capabilities: BotCapabilities = Field(default_factory=BotCapabilities.research)
    data_root: Path
    market_data_root: Path | None = None
    side_data_root: Path | None = None


def load_research_profiles(config: dict[str, Any]) -> list[ResearchBotProfile]:
    user_data_dir = Path(config["user_data_dir"])
    resolved_user_data_dir = user_data_dir.resolve()
    profiles = []

    for index, raw_profile in enumerate(config.get("research_bots", [])):
        profile_location = f"research_bots[{index}]"
        profile_id = _get_required_profile_value(raw_profile, index, "id")
        label = _get_required_profile_value(raw_profile, index, "label")
        raw_market = _get_required_profile_value(raw_profile, index, "market")
        raw_data_source = _get_required_profile_value(raw_profile, index, "data_source")

        try:
            data_source = ResearchDataSourceConfig(**raw_data_source)
        except (TypeError, ValidationError) as e:
            raise ResearchConfigError(
                _invalid_config_message(f"{profile_location}.data_source", e)
            ) from e

        raw_market_data = raw_profile.get("market_data")
        try:
            market_data = (
                ResearchMarketDataConfig(**raw_market_data)
                if raw_market_data is not None
                else None
            )
        except (TypeError, ValidationError) as e:
            raise ResearchConfigError(
                _invalid_config_message(f"{profile_location}.market_data", e)
            ) from e

        raw_side_data = raw_profile.get("side_data")
        try:
            side_data = (
                ResearchSideDataConfig(**raw_side_data)
                if raw_side_data is not None
                else None
            )
        except (TypeError, ValidationError) as e:
            raise ResearchConfigError(
                _invalid_config_message(f"{profile_location}.side_data", e)
            ) from e

        try:
            market = MarketType(raw_market)
        except ValueError as e:
            raise ResearchConfigError(f"Invalid {profile_location}.market") from e

        if market != MarketType.A_SHARE:
            raise ResearchConfigError(f"Unsupported {profile_location}.market: {raw_market}")

        try:
            profiles.append(
                ResearchBotProfile(
                    id=profile_id,
                    label=label,
                    market=market,
                    data_source=data_source,
                    market_data=market_data,
                    side_data=side_data,
                    data_root=user_data_dir / data_source.root,
                    market_data_root=(
                        _resolve_profile_root(
                            resolved_user_data_dir,
                            market_data.meta_root,
                            f"{profile_location}.market_data.meta_root",
                        )
                        if market_data is not None
                        else None
                    ),
                    side_data_root=(
                        _resolve_profile_root(
                            resolved_user_data_dir,
                            side_data.root,
                            f"{profile_location}.side_data.root",
                        )
                        if side_data is not None
                        else None
                    ),
                )
            )
        except ValidationError as e:
            raise ResearchConfigError(_invalid_config_message(profile_location, e)) from e

    return profiles


def _get_required_profile_value(raw_profile: dict[str, Any], index: int, field: str) -> Any:
    try:
        return raw_profile[field]
    except KeyError:
        raise ResearchConfigError(f"Missing research_bots[{index}].{field}") from None
    except TypeError as e:
        raise ResearchConfigError(f"Invalid research_bots[{index}]") from e


def _invalid_config_message(location: str, error: Exception) -> str:
    if isinstance(error, ValidationError):
        errors = error.errors()
        if errors:
            loc = errors[0].get("loc", ())
            if loc:
                field_location = ".".join(str(part) for part in loc)
                return f"Invalid {location}.{field_location}"
    return f"Invalid {location}"


def _resolve_profile_root(user_data_dir: Path, configured_root: str, location: str) -> Path:
    candidate_root = Path(configured_root)
    if candidate_root.is_absolute():
        raise ResearchConfigError(f"Invalid {location}")

    resolved_root = (user_data_dir / candidate_root).resolve()
    if not resolved_root.is_relative_to(user_data_dir):
        raise ResearchConfigError(f"Invalid {location}")

    return resolved_root
