from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import pandas as pd
from pandas import DataFrame
from pydantic import BaseModel, ConfigDict, Field

from freqtrade.research.exceptions import ResearchConfigError
from freqtrade.research.profiles import ResearchBotProfile
from freqtrade.research.side_data.alignment import effective_candle_time_for_publish_time
from freqtrade.research.side_data.store import LocalResearchSideDataStore


if TYPE_CHECKING:
    from freqtrade.research.backtesting import ResearchMarketContext


SUPPORTED_FEATURE_DATASETS = {"fund_flow_daily"}
FEATURE_FIELD_COLUMNS = {
    "main_net_inflow": "feature_fund_flow_daily_main_net_inflow",
    "large_net_inflow": "feature_fund_flow_daily_large_net_inflow",
    "medium_net_inflow": "feature_fund_flow_daily_medium_net_inflow",
    "small_net_inflow": "feature_fund_flow_daily_small_net_inflow",
}


class ResearchFeatureContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    instrument: str
    datasets: list[str]
    frame: DataFrame
    provenance: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ResearchFeatureFilterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: Literal["fund_flow_daily"]
    field: Literal[
        "main_net_inflow",
        "large_net_inflow",
        "medium_net_inflow",
        "small_net_inflow",
    ]
    operator: Literal[">", ">=", "<", "<=", "=="]
    value: float = Field(allow_inf_nan=False)
    missing: Literal["block", "allow"] = "block"


def create_research_feature_context(
    profile: ResearchBotProfile,
    instrument: str,
    datasets: list[str],
    candle_frame: DataFrame,
    market_context: ResearchMarketContext | None,
) -> ResearchFeatureContext:
    if profile.side_data is None or profile.side_data_root is None:
        raise ResearchConfigError("Feature-aware research backtest requires side_data config.")
    if market_context is None or market_context.calendar is None:
        raise ResearchConfigError("Feature-aware research backtest requires market_data calendar.")

    unsupported = [dataset for dataset in datasets if dataset not in SUPPORTED_FEATURE_DATASETS]
    if unsupported:
        raise ValueError(f"Unknown research side dataset: {unsupported[0]}")

    store = LocalResearchSideDataStore(
        profile.side_data_root,
        enabled_datasets=profile.side_data.enabled_datasets,
    )
    raw_features = store.load_feature_frame(instrument, datasets)
    aligned_frame = _align_features_to_candles(
        raw_features,
        candle_frame,
        market_context.calendar,
    )
    provenance = _feature_provenance(store, instrument, datasets)
    warnings = [
        warning
        for dataset_provenance in provenance.values()
        for warning in dataset_provenance.get("warnings", [])
    ]

    return ResearchFeatureContext(
        instrument=instrument,
        datasets=list(datasets),
        frame=aligned_frame,
        provenance=provenance,
        warnings=warnings,
    )


def _align_features_to_candles(
    raw_features: DataFrame,
    candle_frame: DataFrame,
    calendar: Any,
) -> DataFrame:
    candle_dates = pd.to_datetime(candle_frame["date"], utc=True)
    value_columns = [
        column for column in raw_features.columns if column.startswith("feature_")
    ]
    aligned = pd.DataFrame({"date": candle_dates})
    for column in value_columns:
        aligned[column] = pd.NA

    if raw_features.empty or not value_columns:
        return aligned

    features = raw_features.copy()
    features["effective_candle_time"] = pd.to_datetime(
        features["publish_time"].map(
            lambda value: effective_candle_time_for_publish_time(value, calendar)
        ),
        utc=True,
    )
    by_effective_time = (
        features[["effective_candle_time", *value_columns]]
        .sort_values("effective_candle_time")
        .drop_duplicates("effective_candle_time", keep="last")
        .set_index("effective_candle_time")
    )
    reindexed = by_effective_time.reindex(candle_dates)
    reindexed.index.name = "date"
    return reindexed.reset_index()


def _feature_provenance(
    store: LocalResearchSideDataStore,
    instrument: str,
    datasets: list[str],
) -> dict[str, Any]:
    descriptors = {
        descriptor.dataset_id: descriptor
        for descriptor in store.list_datasets(instrument_key=instrument, kind="feature")
    }
    return {
        dataset: descriptors[dataset].model_dump(mode="json")
        for dataset in datasets
        if dataset in descriptors
    }
