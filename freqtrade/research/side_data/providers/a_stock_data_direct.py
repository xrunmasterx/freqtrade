from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd
import requests

from freqtrade.markets import CachedAShareCalendar, MarketType, parse_instrument_key
from freqtrade.research.side_data.alignment import effective_candle_time_for_publish_time


class AStockDataDirectSideDataProvider:
    provider_name = "a-stock-data-direct"
    provider_version = "local"

    def __init__(
        self,
        calendar: CachedAShareCalendar | None = None,
        *,
        clock: Callable[[], pd.Timestamp] | None = None,
    ) -> None:
        self._calendar = calendar
        self._clock = clock or (lambda: pd.Timestamp.now(tz="UTC"))
        self._session = requests.Session()

    def fetch_sector_membership(self, instrument_key: str) -> list[dict[str, Any]]:
        calendar = self._require_calendar()
        instrument = parse_instrument_key(instrument_key, market=MarketType.A_SHARE)
        response = self._session.get(
            "https://29.push2.eastmoney.com/api/qt/slist/get",
            params={
                "spt": "3",
                "secid": f"{1 if instrument.venue == 'SSE' else 0}.{instrument.symbol}",
                "fields": "f12,f14,f3,f128",
            },
            timeout=15,
        )
        response.raise_for_status()
        rows = response.json().get("data", {}).get("diff", []) or []
        ingest_time = str(self._clock())
        records: list[dict[str, Any]] = []
        for row in rows:
            publish_time = ingest_time
            sector_code = row.get("f12")
            records.append(
                {
                    "schema_version": 1,
                    "event_id": (
                        f"a-stock-data-direct:sector_membership:{instrument.key}:"
                        f"{sector_code}"
                    ),
                    "dataset": "sector_membership",
                    "market": "a_share",
                    "instrument": instrument.key,
                    "event_type": "sector_membership",
                    "event_time": publish_time,
                    "publish_time": publish_time,
                    "ingest_time": ingest_time,
                    "effective_candle_time": effective_candle_time_for_publish_time(
                        publish_time,
                        calendar,
                    ),
                    "title": str(row.get("f14", "")),
                    "payload": {
                        "sector_code": sector_code,
                        "sector_name": row.get("f14"),
                        "change_pct": row.get("f3"),
                        "leading_stock": row.get("f128"),
                    },
                    "source": self.provider_name,
                }
            )
        return records

    def _require_calendar(self) -> CachedAShareCalendar:
        if self._calendar is None:
            raise ValueError(
                "A-stock-data-direct side-data provider requires a trading calendar."
            )
        return self._calendar
