import json
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from freqtrade.markets import MarketType, parse_instrument_key
from freqtrade.research.a_share_sessions import validate_a_share_regular_session_frame
from freqtrade.research.a_share_timeframes import (
    is_a_share_minute_timeframe,
    timeframe_to_minutes,
    validate_a_share_ohlcv_timeframe,
)


RESEARCH_OHLCV_COLUMNS = ["date", "open", "high", "low", "close", "volume"]

_PROVIDER_PERIOD_BY_TIMEFRAME = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "60m": "60",
    "1d": "daily",
}

_PROVIDER_COLUMN_ALIASES = {
    "date": ("date", "\u65e5\u671f", "day", "\u65f6\u95f4"),
    "open": ("open", "\u5f00\u76d8"),
    "high": ("high", "\u6700\u9ad8"),
    "low": ("low", "\u6700\u4f4e"),
    "close": ("close", "\u6536\u76d8"),
    "volume": ("volume", "\u6210\u4ea4\u91cf"),
}

_PRICE_COLUMNS = ["open", "high", "low", "close"]
_NUMERIC_COLUMNS = [*_PRICE_COLUMNS, "volume"]


class AShareOhlcvCollectionError(ValueError):
    pass


@dataclass(frozen=True)
class AShareOhlcvRequest:
    instruments: list[str]
    timeframes: list[str]
    start_date: str | None = None
    end_date: str | None = None
    adjustment: str = "raw"


@dataclass(frozen=True)
class AShareOhlcvFileSummary:
    path: str
    rows: int
    start: str | None
    stop: str | None
    status: str
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class AShareOhlcvRunSummary:
    run_id: str
    provider: str
    succeeded: int
    failed: int
    files: list[AShareOhlcvFileSummary]
    warnings: list[str]


class AShareOhlcvProvider(Protocol):
    provider_name: str
    provider_version: str

    def fetch_ohlcv(
        self,
        instrument_key: str,
        timeframe: str,
        start_date: str | None,
        end_date: str | None,
        adjustment: str,
    ) -> pd.DataFrame:
        ...


class AShareOhlcvCollector:
    def __init__(self, root: Path, provider: AShareOhlcvProvider) -> None:
        self._root = root
        self._provider = provider

    def collect(self, request: AShareOhlcvRequest) -> AShareOhlcvRunSummary:
        if request.adjustment != "raw":
            raise AShareOhlcvCollectionError(
                f"Unsupported A-share OHLCV adjustment: {request.adjustment}"
            )

        instruments = []
        for instrument_key in request.instruments:
            try:
                instruments.append(
                    parse_instrument_key(instrument_key, market=MarketType.A_SHARE).key
                )
            except ValueError as exc:
                raise AShareOhlcvCollectionError(str(exc)) from exc
        for timeframe in request.timeframes:
            validate_a_share_ohlcv_timeframe(timeframe)
            provider_period_for_timeframe(timeframe)

        created_at = datetime.now(UTC)
        timestamp = created_at.strftime("%Y%m%dT%H%M%S%fZ")
        run_id = f"{timestamp}-{_slug_provider_name(self._provider.provider_name)}-a-share-ohlcv"
        self._root.mkdir(parents=True, exist_ok=True)

        files: list[AShareOhlcvFileSummary] = []
        warnings: list[str] = []
        for instrument_key in instruments:
            for timeframe in request.timeframes:
                file_summary = self._collect_one(instrument_key, timeframe, request)
                files.append(file_summary)
                warnings.extend(file_summary.warnings)

        succeeded = sum(file_summary.status == "ok" for file_summary in files)
        failed = len(files) - succeeded
        summary = AShareOhlcvRunSummary(
            run_id=run_id,
            provider=self._provider.provider_name,
            succeeded=succeeded,
            failed=failed,
            files=files,
            warnings=warnings,
        )
        self._write_manifest(run_id, created_at, request, instruments, summary)
        return summary

    def _collect_one(
        self,
        instrument_key: str,
        timeframe: str,
        request: AShareOhlcvRequest,
    ) -> AShareOhlcvFileSummary:
        artifact_path = f"{instrument_key}-{timeframe}.csv"
        target_path = self._root / artifact_path
        try:
            provider_dataframe = self._provider.fetch_ohlcv(
                instrument_key,
                timeframe,
                request.start_date,
                request.end_date,
                request.adjustment,
            )
            source_timestamp_semantics = _provider_source_timestamp_semantics(
                self._provider,
                timeframe,
            )
            dataframe, warnings = normalize_provider_ohlcv(
                provider_dataframe,
                timeframe=timeframe,
                source_timestamp_semantics=source_timestamp_semantics,
            )
            dataframe = dataframe[RESEARCH_OHLCV_COLUMNS]
            _write_csv_atomic(dataframe, target_path)
        except Exception as exc:
            return AShareOhlcvFileSummary(
                path=artifact_path,
                rows=0,
                start=None,
                stop=None,
                status="error",
                error=str(exc),
            )

        return AShareOhlcvFileSummary(
            path=artifact_path,
            rows=len(dataframe),
            start=dataframe["date"].iloc[0] if not dataframe.empty else None,
            stop=dataframe["date"].iloc[-1] if not dataframe.empty else None,
            status="ok",
            warnings=warnings,
        )

    def _write_manifest(
        self,
        run_id: str,
        created_at: datetime,
        request: AShareOhlcvRequest,
        instruments: list[str],
        summary: AShareOhlcvRunSummary,
    ) -> None:
        manifest_dir = self._root / ".manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        primary_timeframe = request.timeframes[0] if request.timeframes else "1d"
        history_depth_metadata = _provider_history_depth_metadata(
            self._provider,
            primary_timeframe,
        )
        provider_endpoint = _provider_endpoint(self._provider, primary_timeframe)
        timestamp_semantics = {
            "source_timezone": "Asia/Shanghai",
            "source_timestamp_semantics": _provider_source_timestamp_semantics(
                self._provider,
                primary_timeframe,
            ),
            "canonical_timezone": "UTC",
            "canonical_timestamp_semantics": "candle_open",
        }
        manifest = {
            "run_id": run_id,
            "market": "a_share",
            "provider": self._provider.provider_name,
            "provider_version": self._provider.provider_version,
            "created_at": created_at.isoformat(),
            "instruments": instruments,
            "timeframes": request.timeframes,
            "adjustment": request.adjustment,
            "timerange": {"start": request.start_date, "end": request.end_date},
            "files": [asdict(file_summary) for file_summary in summary.files],
            "warnings": summary.warnings,
            "timeframe_registry_version": "a_share_ohlcv_v1b",
            "timestamp_semantics": timestamp_semantics,
            "provider_endpoint": provider_endpoint,
            "session_filter": "a_share_regular_session"
            if is_a_share_minute_timeframe(primary_timeframe)
            else None,
            **history_depth_metadata,
        }
        (manifest_dir / f"{run_id}.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _slug_provider_name(provider_name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", provider_name).strip("-").lower()
    return slug or "provider"


def _write_csv_atomic(dataframe: pd.DataFrame, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(
        delete=False,
        dir=target_path.parent,
        prefix=f".{target_path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_file.name)
    temp_file.close()
    try:
        dataframe.to_csv(temp_path, columns=RESEARCH_OHLCV_COLUMNS, index=False)
        temp_path.replace(target_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def provider_period_for_timeframe(timeframe: str) -> str:
    try:
        return _PROVIDER_PERIOD_BY_TIMEFRAME[timeframe]
    except KeyError as exc:
        raise AShareOhlcvCollectionError(
            f"Unsupported A-share OHLCV timeframe: {timeframe}"
        ) from exc


def normalize_provider_ohlcv(
    provider_dataframe: pd.DataFrame,
    *,
    timeframe: str = "1d",
    source_timestamp_semantics: str = "candle_open",
    source_timezone: str = "Asia/Shanghai",
) -> tuple[pd.DataFrame, list[str]]:
    if provider_dataframe.empty:
        raise AShareOhlcvCollectionError("Provider returned empty OHLCV data.")

    column_mapping = _provider_column_mapping(provider_dataframe)
    dataframe = provider_dataframe.rename(columns=column_mapping)[RESEARCH_OHLCV_COLUMNS].copy()
    dataframe["date"] = _normalize_dates(
        dataframe["date"],
        timeframe=timeframe,
        source_timestamp_semantics=source_timestamp_semantics,
        source_timezone=source_timezone,
    )
    _normalize_numeric_columns(dataframe)

    _validate_numeric_values(dataframe)
    _validate_ohlc_relationship(dataframe)

    warnings = _reject_or_drop_duplicate_timestamps(dataframe)
    dataframe = dataframe.drop_duplicates(subset=RESEARCH_OHLCV_COLUMNS, keep="first")
    dataframe = dataframe.sort_values("date", kind="stable").reset_index(drop=True)
    session_frame = dataframe.copy()
    session_frame["date"] = pd.to_datetime(session_frame["date"], utc=True)
    validate_a_share_regular_session_frame(session_frame, timeframe)

    return dataframe, warnings


def _provider_column_mapping(provider_dataframe: pd.DataFrame) -> dict[str, str]:
    mapping = {}
    missing = set()

    for canonical_column, aliases in _PROVIDER_COLUMN_ALIASES.items():
        provider_column = next(
            (alias for alias in aliases if alias in provider_dataframe.columns),
            None,
        )
        if provider_column is None:
            missing.add(canonical_column)
            continue
        mapping[provider_column] = canonical_column

    if missing:
        raise AShareOhlcvCollectionError(f"Missing provider OHLCV columns: {sorted(missing)}")

    return mapping


def _normalize_dates(
    dates: pd.Series,
    *,
    timeframe: str,
    source_timestamp_semantics: str,
    source_timezone: str,
) -> pd.Series:
    try:
        parsed_dates = pd.to_datetime(dates, errors="raise")
    except (TypeError, ValueError) as exc:
        raise AShareOhlcvCollectionError("OHLCV dates must be valid.") from exc

    if parsed_dates.isna().any():
        raise AShareOhlcvCollectionError("OHLCV dates must be valid.")

    if not is_a_share_minute_timeframe(timeframe):
        return parsed_dates.dt.strftime("%Y-%m-%d")

    if source_timestamp_semantics not in {"candle_open", "candle_close"}:
        raise AShareOhlcvCollectionError(
            f"Unsupported source timestamp semantics: {source_timestamp_semantics}"
        )

    timezone = ZoneInfo(source_timezone)
    if parsed_dates.dt.tz is None:
        localized = parsed_dates.dt.tz_localize(timezone)
    else:
        localized = parsed_dates.dt.tz_convert(timezone)

    if source_timestamp_semantics == "candle_close":
        localized = localized - timedelta(minutes=timeframe_to_minutes(timeframe))

    return localized.dt.tz_convert(UTC).dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_numeric_columns(dataframe: pd.DataFrame) -> None:
    for column in _NUMERIC_COLUMNS:
        try:
            dataframe[column] = pd.to_numeric(dataframe[column], errors="raise").astype(float)
        except (TypeError, ValueError) as exc:
            raise AShareOhlcvCollectionError(
                "OHLCV numeric values must be parseable."
            ) from exc


def _validate_numeric_values(dataframe: pd.DataFrame) -> None:
    if not np.isfinite(dataframe[_NUMERIC_COLUMNS].to_numpy()).all():
        raise AShareOhlcvCollectionError("OHLCV values must be finite.")

    if not (dataframe[_PRICE_COLUMNS] > 0).all().all():
        raise AShareOhlcvCollectionError("OHLC prices must be greater than 0.")

    if not (dataframe["volume"] >= 0).all():
        raise AShareOhlcvCollectionError("Volume must be greater than or equal to 0.")


def _validate_ohlc_relationship(dataframe: pd.DataFrame) -> None:
    low = dataframe["low"]
    high = dataframe["high"]
    open_ = dataframe["open"]
    close = dataframe["close"]

    is_valid = (low <= pd.concat([open_, close], axis=1).min(axis=1)) & (
        high >= pd.concat([open_, close], axis=1).max(axis=1)
    ) & (low <= high)
    if not is_valid.all():
        raise AShareOhlcvCollectionError("Invalid OHLC relationship.")


def _reject_or_drop_duplicate_timestamps(dataframe: pd.DataFrame) -> list[str]:
    duplicate_rows = dataframe[dataframe.duplicated("date", keep=False)]
    if duplicate_rows.empty:
        return []

    for _, rows_for_date in duplicate_rows.groupby("date", sort=False):
        if len(rows_for_date.drop_duplicates(subset=RESEARCH_OHLCV_COLUMNS)) > 1:
            raise AShareOhlcvCollectionError("Conflicting duplicate OHLCV timestamps.")

    duplicate_count = len(dataframe) - len(
        dataframe.drop_duplicates(subset=RESEARCH_OHLCV_COLUMNS, keep="first")
    )
    return [f"Dropped {duplicate_count} identical duplicate OHLCV rows."]


def _provider_source_timestamp_semantics(
    provider: AShareOhlcvProvider,
    timeframe: str,
) -> str:
    method = getattr(provider, "source_timestamp_semantics", None)
    if method is not None:
        return method(timeframe)
    if is_a_share_minute_timeframe(timeframe):
        raise AShareOhlcvCollectionError(
            "Minute OHLCV provider must declare source timestamp semantics."
        )
    return "candle_open"


def _provider_endpoint(provider: AShareOhlcvProvider, timeframe: str) -> str | None:
    method = getattr(provider, "provider_endpoint", None)
    return method(timeframe) if method is not None else None


def _provider_history_depth_metadata(
    provider: AShareOhlcvProvider,
    timeframe: str,
) -> dict[str, object]:
    method = getattr(provider, "history_depth_metadata", None)
    return method(timeframe) if method is not None else {}
