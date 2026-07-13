from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from freqtrade.markets import MarketType, parse_instrument_key
from freqtrade.research.side_data.models import (
    ResearchDatasetDescriptor,
    ResearchDocument,
    ResearchEvent,
)
from freqtrade.research.side_data.provenance import find_side_data_provenance


_FEATURE_DATASETS = {"fund_flow_daily"}
_EVENT_DATASETS = {"limit_pool"}
_DOCUMENT_DATASETS = {"announcements"}
_ALL_DATASETS = _FEATURE_DATASETS | _EVENT_DATASETS | _DOCUMENT_DATASETS
_FEATURE_BASE_COLUMNS = ["date", "instrument", "source", "publish_time", "ingest_time"]


class LocalResearchSideDataStore:
    def __init__(self, root: Path, enabled_datasets: list[str] | None = None) -> None:
        self.root = root.resolve()
        self.enabled_datasets = set(_ALL_DATASETS if enabled_datasets is None else enabled_datasets)

    def list_datasets(
        self,
        instrument_key: str | None = None,
        kind: Literal["feature", "event", "document"] | None = None,
    ) -> list[ResearchDatasetDescriptor]:
        instrument = _normalize_instrument(instrument_key) if instrument_key else None
        descriptors = [
            self._describe_feature_dataset("fund_flow_daily", instrument),
            self._describe_event_dataset("limit_pool", instrument),
            self._describe_document_dataset("announcements", instrument),
        ]
        return [
            descriptor
            for descriptor in descriptors
            if descriptor.dataset_id in self.enabled_datasets
            and (kind is None or descriptor.kind == kind)
        ]

    def load_feature_frame(self, instrument_key: str, datasets: list[str]) -> pd.DataFrame:
        instrument = _normalize_instrument(instrument_key)
        frames: list[pd.DataFrame] = []
        for dataset in datasets:
            self._require_dataset(dataset, _FEATURE_DATASETS)
            path = self._feature_path(dataset, instrument)
            if not path.is_file():
                raise FileNotFoundError(path)
            frames.append(_normalize_feature_frame(dataset, pd.read_csv(path)))

        if not frames:
            return pd.DataFrame(columns=_FEATURE_BASE_COLUMNS[:2])

        result = frames[0]
        for frame in frames[1:]:
            result = result.merge(frame, on=_FEATURE_BASE_COLUMNS, how="outer")
        return result.sort_values(["date", "instrument"]).reset_index(drop=True)

    def load_events(self, instrument_key: str, datasets: list[str]) -> list[ResearchEvent]:
        instrument = _normalize_instrument(instrument_key)
        events: list[ResearchEvent] = []
        for dataset in datasets:
            self._require_dataset(dataset, _EVENT_DATASETS)
            for path in sorted((self.root / "events" / dataset).glob("*.jsonl")):
                for record in _read_jsonl(path):
                    event = ResearchEvent(**record)
                    if event.instrument == instrument:
                        events.append(event)

        return sorted(
            events,
            key=lambda item: (pd.Timestamp(item.effective_candle_time), item.event_id),
        )

    def load_documents(
        self,
        instrument_key: str,
        datasets: list[str],
    ) -> list[ResearchDocument]:
        instrument = _normalize_instrument(instrument_key)
        documents: list[ResearchDocument] = []
        for dataset in datasets:
            self._require_dataset(dataset, _DOCUMENT_DATASETS)
            path = self._document_path(dataset, instrument)
            if not path.is_file():
                raise FileNotFoundError(path)
            for record in _read_jsonl(path):
                document = ResearchDocument(**record)
                if document.instrument == instrument:
                    documents.append(document)

        return sorted(
            documents,
            key=lambda item: (pd.Timestamp(item.effective_candle_time), item.document_id),
        )

    def _describe_feature_dataset(
        self,
        dataset: str,
        instrument: str | None,
    ) -> ResearchDatasetDescriptor:
        path = self._feature_artifact_path(dataset, instrument)
        return self._build_descriptor(
            dataset=dataset,
            kind="feature",
            scope="instrument",
            storage_format="csv",
            timeframe="1d",
            artifact_path=path,
        )

    def _describe_event_dataset(
        self,
        dataset: str,
        instrument: str | None,
    ) -> ResearchDatasetDescriptor:
        path = self._event_artifact_path(dataset, instrument)
        return self._build_descriptor(
            dataset=dataset,
            kind="event",
            scope="market",
            storage_format="jsonl",
            timeframe=None,
            artifact_path=path,
        )

    def _describe_document_dataset(
        self,
        dataset: str,
        instrument: str | None,
    ) -> ResearchDatasetDescriptor:
        path = self._document_artifact_path(dataset, instrument)
        return self._build_descriptor(
            dataset=dataset,
            kind="document",
            scope="instrument",
            storage_format="jsonl",
            timeframe=None,
            artifact_path=path,
        )

    def _build_descriptor(
        self,
        *,
        dataset: str,
        kind: Literal["feature", "event", "document"],
        scope: Literal["instrument", "market", "sector"],
        storage_format: Literal["csv", "jsonl"],
        timeframe: str | None,
        artifact_path: Path | None,
    ) -> ResearchDatasetDescriptor:
        provenance: dict[str, Any] = {}
        if artifact_path is not None and artifact_path.is_file():
            relative_path = artifact_path.relative_to(self.root).as_posix()
            provenance = find_side_data_provenance(self.root, relative_path)

        return ResearchDatasetDescriptor(
            dataset_id=dataset,
            kind=kind,
            scope=scope,
            storage_format=storage_format,
            timeframe=timeframe,
            available=artifact_path is not None and artifact_path.is_file(),
            start=provenance.get("start"),
            stop=provenance.get("stop"),
            provider=provenance.get("provider"),
            provider_version=provenance.get("provider_version"),
            manifest_run_id=provenance.get("manifest_run_id"),
            warnings=provenance.get("warnings") or [],
        )

    def _feature_artifact_path(self, dataset: str, instrument: str | None) -> Path | None:
        if instrument is not None:
            path = self._feature_path(dataset, instrument)
            return path if path.is_file() else None

        return _first_file(self.root / "features" / dataset, "*.csv")

    def _event_artifact_path(self, dataset: str, instrument: str | None) -> Path | None:
        dataset_root = self.root / "events" / dataset
        if instrument is None:
            return _first_file(dataset_root, "*.jsonl")

        for path in sorted(dataset_root.glob("*.jsonl")):
            for record in _read_jsonl(path):
                if record.get("instrument") == instrument:
                    return path
        return None

    def _document_artifact_path(self, dataset: str, instrument: str | None) -> Path | None:
        if instrument is not None:
            path = self._document_path(dataset, instrument)
            return path if path.is_file() else None

        return _first_file(self.root / "documents" / dataset, "*.jsonl")

    def _feature_path(self, dataset: str, instrument: str) -> Path:
        return self.root / "features" / dataset / f"{instrument}.csv"

    def _document_path(self, dataset: str, instrument: str) -> Path:
        return self.root / "documents" / dataset / f"{instrument}.jsonl"

    def _require_dataset(self, dataset: str, allowed: set[str]) -> None:
        if dataset not in _ALL_DATASETS or dataset not in self.enabled_datasets:
            raise ValueError(f"Unknown research side dataset: {dataset}")
        if dataset not in allowed:
            raise ValueError(f"Research side dataset {dataset} has incompatible kind")


def _normalize_instrument(instrument_key: str) -> str:
    return parse_instrument_key(instrument_key, market=MarketType.A_SHARE).key


def _normalize_feature_frame(dataset: str, frame: pd.DataFrame) -> pd.DataFrame:
    missing_columns = [column for column in _FEATURE_BASE_COLUMNS if column not in frame.columns]
    if missing_columns:
        raise ValueError(f"Missing side feature columns: {missing_columns}")

    normalized = frame.copy()
    normalized["date"] = pd.to_datetime(normalized["date"], utc=True)

    value_columns = [
        column for column in normalized.columns if column not in _FEATURE_BASE_COLUMNS
    ]
    rename_map = {
        column: f"feature_{dataset}_{column}"
        for column in value_columns
    }
    normalized = normalized.rename(columns=rename_map)

    for column in rename_map.values():
        normalized[column] = pd.to_numeric(normalized[column], errors="raise").astype(float)

    return normalized


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def _first_file(root: Path, pattern: str) -> Path | None:
    if not root.is_dir():
        return None

    return next(iter(sorted(root.glob(pattern))), None)
