from pathlib import Path

import pandas as pd

from freqtrade.markets import MarketType, parse_instrument_key


RESEARCH_OHLCV_COLUMNS = ["date", "open", "high", "low", "close", "volume"]
_NUMERIC_OHLCV_COLUMNS = RESEARCH_OHLCV_COLUMNS[1:]


class LocalCsvResearchDataSource:
    def __init__(self, root: Path) -> None:
        self.root = root

    def list_instruments(self) -> list[str]:
        instrument_keys = set()

        for path in self.root.glob("*.csv"):
            instrument_key = path.stem.rpartition("-")[0]
            instrument = parse_instrument_key(instrument_key, market=MarketType.A_SHARE)
            instrument_keys.add(instrument.key)

        return sorted(instrument_keys)

    def load_ohlcv(self, instrument_key: str, timeframe: str) -> pd.DataFrame:
        path = self.root / f"{instrument_key}-{timeframe}.csv"
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
