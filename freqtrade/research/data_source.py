import logging
import re
from pathlib import Path
from typing import Protocol

import pandas as pd

from freqtrade.markets import Instrument, MarketType, parse_instrument_key
from freqtrade.research.a_share_sessions import validate_a_share_regular_session_frame
from freqtrade.research.a_share_timeframes import (
    sort_a_share_ohlcv_timeframes,
    validate_a_share_ohlcv_timeframe,
)
from freqtrade.research.exceptions import ResearchUnsupportedFeatureError
from freqtrade.research.provenance import ResearchDataProvenance, find_local_csv_provenance


RESEARCH_OHLCV_COLUMNS = ["date", "open", "high", "low", "close", "volume"]
_NUMERIC_OHLCV_COLUMNS = RESEARCH_OHLCV_COLUMNS[1:]
_A_SHARE_CSV_STEM_RE = re.compile(
    r"^(?P<instrument_key>\d{6}\.(?:SH|SZ))-(?P<timeframe>.+)$",
    re.IGNORECASE,
)
logger = logging.getLogger(__name__)


class ResearchMarketDataSource(Protocol):
    def list_instruments(self) -> list[Instrument]: ...

    def available_timeframes(self, instrument_key: str) -> list[str]: ...

    def load_ohlcv(
        self,
        instrument_key: str,
        timeframe: str,
        adjustment: str = "raw",
    ) -> pd.DataFrame: ...

    def get_ohlcv_provenance(
        self,
        instrument_key: str,
        timeframe: str,
        adjustment: str = "raw",
    ) -> ResearchDataProvenance: ...


class LocalCsvResearchDataSource:
    def __init__(self, root: Path) -> None:
        self.root = root

    def list_instruments(self) -> list[Instrument]:
        instruments_by_key = {}

        for path in self.root.glob("*.csv"):
            csv_key = _parse_a_share_csv_stem(path.stem)
            if csv_key is None:
                logger.warning("Skipping invalid research data filename: %s", path.name)
                continue

            instrument_key, _ = csv_key
            instrument = parse_instrument_key(instrument_key, market=MarketType.A_SHARE)
            instruments_by_key[instrument.key] = instrument

        return [instruments_by_key[key] for key in sorted(instruments_by_key)]

    def available_timeframes(self, instrument_key: str) -> list[str]:
        instrument_key = _normalize_a_share_instrument_key(instrument_key)
        timeframes = set()

        for path in self.root.glob("*.csv"):
            csv_key = _parse_a_share_csv_stem(path.stem)
            if csv_key is None:
                continue

            csv_instrument_key, timeframe = csv_key
            if csv_instrument_key == instrument_key:
                timeframes.add(timeframe)

        return sort_a_share_ohlcv_timeframes(timeframes)

    def load_ohlcv(
        self,
        instrument_key: str,
        timeframe: str,
        adjustment: str = "raw",
    ) -> pd.DataFrame:
        if adjustment != "raw":
            raise ValueError(f"Unsupported research adjustment: {adjustment}")

        instrument_key = _normalize_a_share_instrument_key(instrument_key)
        timeframe = _validate_timeframe(timeframe)
        path = self._resolve_ohlcv_path(instrument_key, timeframe)
        if not path.is_file():
            raise FileNotFoundError(path)

        dataframe = pd.read_csv(path)
        if list(dataframe.columns) != RESEARCH_OHLCV_COLUMNS:
            raise ValueError("Invalid OHLCV columns")

        dataframe = dataframe.copy()
        dataframe["date"] = pd.to_datetime(dataframe["date"], utc=True)

        for column in _NUMERIC_OHLCV_COLUMNS:
            dataframe[column] = pd.to_numeric(dataframe[column], errors="raise").astype(float)

        validate_a_share_regular_session_frame(dataframe, timeframe)

        return dataframe.sort_values("date").reset_index(drop=True)

    def get_ohlcv_provenance(
        self,
        instrument_key: str,
        timeframe: str,
        adjustment: str = "raw",
    ) -> ResearchDataProvenance:
        if adjustment != "raw":
            raise ValueError(f"Unsupported research adjustment: {adjustment}")

        instrument_key = _normalize_a_share_instrument_key(instrument_key)
        timeframe = _validate_timeframe(timeframe)
        path = self._resolve_ohlcv_path(instrument_key, timeframe)
        return find_local_csv_provenance(self.root, path.name)

    def _resolve_ohlcv_path(self, instrument_key: str, timeframe: str) -> Path:
        root = self.root.resolve()
        path = (root / f"{instrument_key}-{timeframe}.csv").resolve()
        try:
            path.relative_to(root)
        except ValueError:
            raise ValueError("Invalid research OHLCV path") from None
        return path


def _normalize_a_share_instrument_key(instrument_key: str) -> str:
    try:
        return parse_instrument_key(instrument_key, market=MarketType.A_SHARE).key
    except ValueError:
        raise ValueError("Invalid research instrument") from None


def _parse_a_share_csv_stem(stem: str) -> tuple[str, str] | None:
    match = _A_SHARE_CSV_STEM_RE.fullmatch(stem)
    if match is None:
        return None

    timeframe = match.group("timeframe")
    try:
        timeframe = validate_a_share_ohlcv_timeframe(timeframe)
    except (ResearchUnsupportedFeatureError, ValueError):
        return None

    try:
        instrument_key = parse_instrument_key(
            match.group("instrument_key"),
            market=MarketType.A_SHARE,
        ).key
    except ValueError:
        return None

    return instrument_key, timeframe


def _validate_timeframe(timeframe: str) -> str:
    return validate_a_share_ohlcv_timeframe(timeframe)
