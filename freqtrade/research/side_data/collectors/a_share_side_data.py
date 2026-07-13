from __future__ import annotations

import json
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from freqtrade.markets import CachedAShareCalendar, MarketType, parse_instrument_key
from freqtrade.research.side_data.models import ResearchDocument, ResearchEvent


_SUPPORTED_DATASETS = {"fund_flow_daily", "limit_pool", "announcements"}
_FUND_FLOW_COLUMNS = [
    "date",
    "instrument",
    "main_net_inflow",
    "large_net_inflow",
    "medium_net_inflow",
    "small_net_inflow",
    "source",
    "publish_time",
    "ingest_time",
]
_FUND_FLOW_VALUE_COLUMNS = [
    "main_net_inflow",
    "large_net_inflow",
    "medium_net_inflow",
    "small_net_inflow",
]
_WINDOWS_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]")


class AShareSideDataCollectionError(ValueError):
    pass


@dataclass(frozen=True)
class AShareSideDataRequest:
    instruments: list[str]
    datasets: list[str]
    start_date: str | None = None
    end_date: str | None = None
    trade_dates: list[str] | None = None


@dataclass(frozen=True)
class AShareSideDataFileSummary:
    path: str
    dataset: str
    kind: str
    rows: int
    start: str | None
    stop: str | None
    status: str
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class AShareSideDataRunSummary:
    run_id: str
    provider: str
    succeeded: int
    failed: int
    files: list[AShareSideDataFileSummary]
    warnings: list[str]


class AShareSideDataProvider(Protocol):
    provider_name: str
    provider_version: str

    def fetch_fund_flow_daily(
        self,
        instrument_key: str,
        start_date: str | None,
        end_date: str | None,
    ) -> pd.DataFrame:
        ...

    def fetch_limit_pool(self, trade_date: str) -> list[dict[str, Any]]:
        ...

    def fetch_announcements(
        self,
        instrument_key: str,
        start_date: str | None,
        end_date: str | None,
    ) -> list[dict[str, Any]]:
        ...


class AShareSideDataCollector:
    def __init__(
        self,
        root: Path,
        provider: AShareSideDataProvider,
        calendar: CachedAShareCalendar | None = None,
    ) -> None:
        self._root = root
        self._provider = provider
        self._calendar = calendar

    def collect(self, request: AShareSideDataRequest) -> AShareSideDataRunSummary:
        instruments = [_normalize_instrument(instrument) for instrument in request.instruments]
        datasets = _normalize_datasets(request.datasets)

        created_at = datetime.now(UTC)
        timestamp = created_at.strftime("%Y%m%dT%H%M%S%fZ")
        run_id = (
            f"{timestamp}-{_slug_provider_name(self._provider.provider_name)}-a-share-side-data"
        )
        self._root.mkdir(parents=True, exist_ok=True)

        files: list[AShareSideDataFileSummary] = []
        warnings: list[str] = []
        for dataset in datasets:
            if dataset == "fund_flow_daily":
                summaries = self._collect_fund_flow(instruments, request)
            elif dataset == "limit_pool":
                summaries = self._collect_limit_pool(request)
            else:
                summaries = self._collect_announcements(instruments, request)

            files.extend(summaries)
            warnings.extend(
                warning
                for file_summary in summaries
                for warning in file_summary.warnings
            )

        succeeded = sum(file_summary.status == "ok" for file_summary in files)
        failed = len(files) - succeeded
        summary = AShareSideDataRunSummary(
            run_id=run_id,
            provider=self._provider.provider_name,
            succeeded=succeeded,
            failed=failed,
            files=files,
            warnings=warnings,
        )
        self._write_manifest(run_id, created_at, request, instruments, datasets, summary)
        return summary

    def _collect_fund_flow(
        self,
        instruments: list[str],
        request: AShareSideDataRequest,
    ) -> list[AShareSideDataFileSummary]:
        summaries: list[AShareSideDataFileSummary] = []
        for instrument in instruments:
            artifact_path = f"features/fund_flow_daily/{instrument}.csv"
            try:
                frame = self._provider.fetch_fund_flow_daily(
                    instrument,
                    request.start_date,
                    request.end_date,
                )
                normalized = _normalize_fund_flow_frame(frame, instrument)
                _write_csv_atomic(normalized, self._root / artifact_path)
                summaries.append(
                    _ok_frame_summary(
                        artifact_path,
                        dataset="fund_flow_daily",
                        kind="feature",
                        frame=normalized,
                    )
                )
            except Exception as exc:
                summaries.append(
                    _error_file_summary(
                        artifact_path,
                        dataset="fund_flow_daily",
                        kind="feature",
                        exc=exc,
                    )
                )
        return summaries

    def _collect_limit_pool(
        self,
        request: AShareSideDataRequest,
    ) -> list[AShareSideDataFileSummary]:
        trade_dates = _requested_trade_dates(
            request.start_date,
            request.end_date,
            calendar=self._calendar,
            requested_trade_dates=request.trade_dates,
        )
        summaries: list[AShareSideDataFileSummary] = []
        for trade_date in trade_dates:
            artifact_path = f"events/limit_pool/{trade_date}.jsonl"
            try:
                records = self._provider.fetch_limit_pool(trade_date)
                normalized_records = _normalize_event_records("limit_pool", records)
                _write_jsonl_atomic(normalized_records, self._root / artifact_path)
                summaries.append(
                    _ok_records_summary(
                        artifact_path,
                        dataset="limit_pool",
                        kind="event",
                        records=normalized_records,
                    )
                )
            except Exception as exc:
                summaries.append(
                    _error_file_summary(
                        artifact_path,
                        dataset="limit_pool",
                        kind="event",
                        exc=exc,
                    )
                )
        return summaries

    def _collect_announcements(
        self,
        instruments: list[str],
        request: AShareSideDataRequest,
    ) -> list[AShareSideDataFileSummary]:
        summaries: list[AShareSideDataFileSummary] = []
        for instrument in instruments:
            artifact_path = f"documents/announcements/{instrument}.jsonl"
            try:
                records = self._provider.fetch_announcements(
                    instrument,
                    request.start_date,
                    request.end_date,
                )
                normalized_records = _normalize_document_records("announcements", records)
                _write_jsonl_atomic(normalized_records, self._root / artifact_path)
                summaries.append(
                    _ok_records_summary(
                        artifact_path,
                        dataset="announcements",
                        kind="document",
                        records=normalized_records,
                    )
                )
            except Exception as exc:
                summaries.append(
                    _error_file_summary(
                        artifact_path,
                        dataset="announcements",
                        kind="document",
                        exc=exc,
                    )
                )
        return summaries

    def _write_manifest(
        self,
        run_id: str,
        created_at: datetime,
        request: AShareSideDataRequest,
        instruments: list[str],
        datasets: list[str],
        summary: AShareSideDataRunSummary,
    ) -> None:
        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "market": "a_share",
            "provider": self._provider.provider_name,
            "provider_version": self._provider.provider_version,
            "created_at": created_at.isoformat(),
            "datasets": datasets,
            "instruments": instruments,
            "timerange": {"start": request.start_date, "end": request.end_date},
            "files": [asdict(file_summary) for file_summary in summary.files],
            "warnings": summary.warnings,
        }
        manifest_path = self._root / ".manifests" / f"{run_id}.json"
        _write_json_atomic(manifest, manifest_path)


def _normalize_instrument(instrument_key: str) -> str:
    try:
        return parse_instrument_key(instrument_key, market=MarketType.A_SHARE).key
    except ValueError as exc:
        raise AShareSideDataCollectionError(str(exc)) from exc


def _normalize_datasets(datasets: list[str]) -> list[str]:
    unsupported = [dataset for dataset in datasets if dataset not in _SUPPORTED_DATASETS]
    if unsupported:
        raise AShareSideDataCollectionError(
            f"Unsupported A-share side dataset: {unsupported[0]}"
        )
    return datasets


def _requested_trade_dates(
    start_date: str | None,
    end_date: str | None,
    *,
    calendar: CachedAShareCalendar | None = None,
    requested_trade_dates: list[str] | None = None,
) -> list[str]:
    if requested_trade_dates is not None:
        return [
            _parse_calendar_date(trade_date).isoformat()
            for trade_date in requested_trade_dates
        ]

    if start_date is None and end_date is None:
        raise AShareSideDataCollectionError("limit_pool requires start_date or end_date")

    parsed_start = _parse_calendar_date(start_date) or _parse_calendar_date(end_date)
    parsed_end = _parse_calendar_date(end_date) or _parse_calendar_date(start_date)
    if parsed_start is None or parsed_end is None:
        raise AShareSideDataCollectionError("limit_pool requires start_date or end_date")
    if parsed_start > parsed_end:
        raise AShareSideDataCollectionError(
            "limit_pool requires start_date to be on or before end_date"
        )

    trade_dates: list[str] = []
    current = parsed_start
    while current <= parsed_end:
        if calendar is None or calendar.is_trading_day(current):
            trade_dates.append(current.isoformat())
        current += timedelta(days=1)
    return trade_dates


def _parse_calendar_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return pd.Timestamp(value).date()
    except ValueError as exc:
        raise AShareSideDataCollectionError(f"Invalid trade date: {value}") from exc


def _normalize_fund_flow_frame(frame: pd.DataFrame, expected_instrument: str) -> pd.DataFrame:
    missing = [column for column in _FUND_FLOW_COLUMNS if column not in frame.columns]
    if missing:
        raise AShareSideDataCollectionError(f"Missing fund flow columns: {missing}")

    normalized = frame.loc[:, _FUND_FLOW_COLUMNS].copy()
    normalized["date"] = pd.to_datetime(normalized["date"], errors="raise").dt.date.astype(str)
    normalized["instrument"] = normalized["instrument"].map(_normalize_instrument)
    if not normalized["instrument"].eq(expected_instrument).all():
        raise AShareSideDataCollectionError(
            f"Provider returned fund flow rows for unexpected instrument: {expected_instrument}"
        )

    for column in _FUND_FLOW_VALUE_COLUMNS:
        normalized[column] = pd.to_numeric(normalized[column], errors="raise").astype(float)

    return normalized


def _normalize_event_records(dataset: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_records: list[dict[str, Any]] = []
    for record in records:
        event = ResearchEvent(**record)
        if event.dataset != dataset:
            raise AShareSideDataCollectionError(
                f"Provider returned event for wrong dataset: {event.dataset}"
            )
        normalized_records.append(event.model_dump(mode="json"))
    return normalized_records


def _normalize_document_records(
    dataset: str,
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized_records: list[dict[str, Any]] = []
    for record in records:
        document = ResearchDocument(**record)
        if document.dataset != dataset:
            raise AShareSideDataCollectionError(
                f"Provider returned document for wrong dataset: {document.dataset}"
            )
        normalized_records.append(document.model_dump(mode="json"))
    return normalized_records


def _slug_provider_name(provider_name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", provider_name).strip("-").lower()
    return slug or "provider"


def _write_csv_atomic(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(
        delete=False,
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_file.name)
    temp_file.close()
    try:
        frame.to_csv(temp_path, index=False)
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _write_jsonl_atomic(records: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(
        delete=False,
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_file.name)
    temp_file.close()
    try:
        temp_path.write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
            encoding="utf-8",
        )
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _write_json_atomic(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(
        delete=False,
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_file.name)
    temp_file.close()
    try:
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _ok_frame_summary(
    artifact_path: str,
    dataset: str,
    kind: str,
    frame: pd.DataFrame,
) -> AShareSideDataFileSummary:
    return AShareSideDataFileSummary(
        path=artifact_path,
        dataset=dataset,
        kind=kind,
        rows=len(frame),
        start=str(frame["date"].iloc[0]) if not frame.empty else None,
        stop=str(frame["date"].iloc[-1]) if not frame.empty else None,
        status="ok",
    )


def _ok_records_summary(
    artifact_path: str,
    dataset: str,
    kind: str,
    records: list[dict[str, Any]],
) -> AShareSideDataFileSummary:
    effective_times = [
        str(record["effective_candle_time"])
        for record in records
        if record.get("effective_candle_time")
    ]
    return AShareSideDataFileSummary(
        path=artifact_path,
        dataset=dataset,
        kind=kind,
        rows=len(records),
        start=min(effective_times) if effective_times else None,
        stop=max(effective_times) if effective_times else None,
        status="ok",
    )


def _error_file_summary(
    artifact_path: str,
    dataset: str,
    kind: str,
    exc: Exception,
) -> AShareSideDataFileSummary:
    return AShareSideDataFileSummary(
        path=artifact_path,
        dataset=dataset,
        kind=kind,
        rows=0,
        start=None,
        stop=None,
        status="error",
        error=_sanitize_error_message(str(exc)),
    )


def _sanitize_error_message(message: str) -> str:
    if _WINDOWS_ABSOLUTE_PATH.search(message):
        return "failed"
    return message
