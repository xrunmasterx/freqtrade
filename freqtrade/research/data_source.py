import re
from pathlib import Path

import pandas as pd

from freqtrade.markets import Instrument, MarketType, parse_instrument_key


RESEARCH_OHLCV_COLUMNS = ["date", "open", "high", "low", "close", "volume"]
_NUMERIC_OHLCV_COLUMNS = RESEARCH_OHLCV_COLUMNS[1:]
_A_SHARE_CSV_STEM_RE = re.compile(r"^(?P<instrument_key>\d{6}\.(?:SH|SZ))-", re.IGNORECASE)
_TIMEFRAME_RE = re.compile(r"^[0-9]+[mhdwM]$")


class LocalCsvResearchDataSource:
    def __init__(self, root: Path) -> None:
        self.root = root

    def list_instruments(self) -> list[Instrument]:
        instruments_by_key = {}

        for path in self.root.glob("*.csv"):
            match = _A_SHARE_CSV_STEM_RE.match(path.stem)
            if match is None:
                raise ValueError(f"Invalid A-share research data filename: {path.name}")

            instrument_key = match.group("instrument_key")
            instrument = parse_instrument_key(instrument_key, market=MarketType.A_SHARE)
            instruments_by_key[instrument.key] = instrument

        return [instruments_by_key[key] for key in sorted(instruments_by_key)]

    def load_ohlcv(self, instrument_key: str, timeframe: str) -> pd.DataFrame:
        instrument_key = _normalize_a_share_instrument_key(instrument_key)
        timeframe = _validate_timeframe(timeframe)
        path = self._resolve_ohlcv_path(instrument_key, timeframe)
        if not path.is_file():
            raise FileNotFoundError(path)

        dataframe = pd.read_csv(path)
        missing_columns = set(RESEARCH_OHLCV_COLUMNS) - set(dataframe.columns)
        if missing_columns:
            raise ValueError(f"Missing OHLCV columns: {sorted(missing_columns)}")

        dataframe = dataframe.loc[:, RESEARCH_OHLCV_COLUMNS].copy()
        dataframe["date"] = pd.to_datetime(dataframe["date"], utc=True)

        for column in _NUMERIC_OHLCV_COLUMNS:
            dataframe[column] = pd.to_numeric(dataframe[column], errors="raise").astype(float)

        return dataframe.sort_values("date").reset_index(drop=True)

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


def _validate_timeframe(timeframe: str) -> str:
    if not timeframe or not _TIMEFRAME_RE.fullmatch(timeframe):
        raise ValueError("Invalid research timeframe")
    return timeframe
