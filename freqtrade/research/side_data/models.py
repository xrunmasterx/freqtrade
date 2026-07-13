from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator


ResearchSideDataKind = Literal["feature", "event", "document"]
ResearchSideDataScope = Literal["instrument", "market", "sector"]
ResearchSideDataFormat = Literal["csv", "jsonl"]


class ResearchDatasetDescriptor(BaseModel):
    dataset_id: str
    kind: ResearchSideDataKind
    market: Literal["a_share"] = "a_share"
    scope: ResearchSideDataScope
    storage_format: ResearchSideDataFormat
    timeframe: str | None = None
    available: bool = False
    start: str | None = None
    stop: str | None = None
    provider: str | None = None
    provider_version: str | None = None
    manifest_run_id: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ResearchSideLayerSelection(BaseModel):
    features: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    documents: list[str] = Field(default_factory=list)


class ResearchEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    event_id: str
    dataset: str
    market: Literal["a_share"]
    instrument: str
    event_type: str
    event_time: str
    publish_time: str
    ingest_time: str
    effective_candle_time: str
    title: str
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str

    @field_validator("event_time", "publish_time", "ingest_time", "effective_candle_time")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        pd.to_datetime(value, utc=True)
        return value


class ResearchDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    document_id: str
    dataset: str
    market: Literal["a_share"]
    instrument: str
    document_type: str
    title: str
    publish_time: str
    ingest_time: str
    effective_candle_time: str
    url: str | None = None
    source: str
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("publish_time", "ingest_time", "effective_candle_time")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        pd.to_datetime(value, utc=True)
        return value


ResearchFeatureFrame = pd.DataFrame
