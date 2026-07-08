from __future__ import annotations

from importlib import import_module, metadata
from typing import Any

import pandas as pd

from freqtrade.markets import CachedAShareCalendar, MarketType, parse_instrument_key
from freqtrade.research.side_data.alignment import effective_candle_time_for_publish_time


_FUND_FLOW_COLUMN_ALIASES = {
    "日期": "date",
    "主力净流入-净额": "main_net_inflow",
    "大单净流入-净额": "large_net_inflow",
    "中单净流入-净额": "medium_net_inflow",
    "小单净流入-净额": "small_net_inflow",
}


class AkshareAshareSideDataProvider:
    provider_name = "akshare"

    def __init__(self, calendar: CachedAShareCalendar | None = None) -> None:
        self._calendar = calendar

    @property
    def provider_version(self) -> str:
        try:
            return metadata.version("akshare")
        except metadata.PackageNotFoundError:
            return "not-installed"

    def fetch_fund_flow_daily(
        self,
        instrument_key: str,
        start_date: str | None,
        end_date: str | None,
    ) -> pd.DataFrame:
        instrument = parse_instrument_key(instrument_key, market=MarketType.A_SHARE)
        frame = _import_akshare().stock_individual_fund_flow(
            stock=instrument.symbol,
            market="sh" if instrument.venue == "SSE" else "sz",
        )
        frame = frame.rename(columns=_FUND_FLOW_COLUMN_ALIASES)
        result = frame[
            [
                "date",
                "main_net_inflow",
                "large_net_inflow",
                "medium_net_inflow",
                "small_net_inflow",
            ]
        ].copy()
        result["date"] = pd.to_datetime(result["date"], errors="raise").dt.date.astype(str)
        if start_date is not None:
            result = result[result["date"] >= _compact_to_iso(start_date)]
        if end_date is not None:
            result = result[result["date"] <= _compact_to_iso(end_date)]
        result.insert(1, "instrument", instrument.key)
        result["source"] = self.provider_name
        result["publish_time"] = result["date"] + "T15:30:00+08:00"
        result["ingest_time"] = str(pd.Timestamp.now(tz="UTC"))
        return result.reset_index(drop=True)

    def fetch_limit_pool(self, trade_date: str) -> list[dict[str, Any]]:
        calendar = self._require_calendar()
        trade_date_iso = _compact_to_iso(trade_date)
        frame = _import_akshare().stock_zt_pool_em(date=_iso_to_compact(trade_date_iso))
        ingest_time = str(pd.Timestamp.now(tz="UTC"))
        records: list[dict[str, Any]] = []
        for row in frame.to_dict("records"):
            instrument = parse_instrument_key(
                _instrument_key_from_symbol(row["代码"]),
                market=MarketType.A_SHARE,
            )
            publish_time = f"{trade_date_iso}T15:05:00+08:00"
            records.append(
                {
                    "schema_version": 1,
                    "event_id": (
                        f"akshare:limit_pool:{trade_date_iso}:{instrument.key}:limit_up"
                    ),
                    "dataset": "limit_pool",
                    "market": "a_share",
                    "instrument": instrument.key,
                    "event_type": "limit_up",
                    "event_time": f"{trade_date_iso}T15:00:00+08:00",
                    "publish_time": publish_time,
                    "ingest_time": ingest_time,
                    "effective_candle_time": effective_candle_time_for_publish_time(
                        publish_time,
                        calendar,
                    ),
                    "title": str(row.get("名称", "Limit up")),
                    "payload": {
                        "limit_stat": row.get("涨停统计"),
                        "sealed_amount": _optional_float(row.get("封板资金")),
                        "first_seal_time": row.get("首次封板时间"),
                        "last_seal_time": row.get("最后封板时间"),
                        "industry": row.get("所属行业"),
                    },
                    "source": self.provider_name,
                }
            )
        return records

    def fetch_announcements(
        self,
        instrument_key: str,
        start_date: str | None,
        end_date: str | None,
    ) -> list[dict[str, Any]]:
        calendar = self._require_calendar()
        instrument = parse_instrument_key(instrument_key, market=MarketType.A_SHARE)
        frame = _import_akshare().stock_individual_notice_report(
            security=instrument.symbol,
            symbol="全部",
            begin_date=start_date,
            end_date=end_date,
        )
        ingest_time = str(pd.Timestamp.now(tz="UTC"))
        records: list[dict[str, Any]] = []
        for index, row in enumerate(frame.to_dict("records")):
            publish_date = pd.Timestamp(row["公告日期"]).date().isoformat()
            publish_time = f"{publish_date}T19:30:00+08:00"
            records.append(
                {
                    "schema_version": 1,
                    "document_id": f"akshare:announcement:{instrument.key}:{index}",
                    "dataset": "announcements",
                    "market": "a_share",
                    "instrument": instrument.key,
                    "document_type": "announcement",
                    "title": str(row.get("公告标题", "")),
                    "publish_time": publish_time,
                    "ingest_time": ingest_time,
                    "effective_candle_time": effective_candle_time_for_publish_time(
                        publish_time,
                        calendar,
                    ),
                    "url": row.get("网址"),
                    "source": self.provider_name,
                    "payload": {"category": row.get("公告类型")},
                }
            )
        return records

    def _require_calendar(self) -> CachedAShareCalendar:
        if self._calendar is None:
            raise ValueError("Akshare A-share side-data provider requires a trading calendar.")
        return self._calendar


def _import_akshare() -> Any:
    try:
        return import_module("akshare")
    except ImportError as exc:
        raise RuntimeError(
            "Install optional dependency `akshare` before using A-share side-data collectors."
        ) from exc


def _compact_to_iso(value: str) -> str:
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return pd.Timestamp(value).date().isoformat()


def _iso_to_compact(value: str) -> str:
    return value.replace("-", "")


def _instrument_key_from_symbol(symbol: object) -> str:
    normalized = str(symbol).zfill(6)
    suffix = "SH" if normalized.startswith("6") else "SZ"
    return f"{normalized}.{suffix}"


def _optional_float(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)

