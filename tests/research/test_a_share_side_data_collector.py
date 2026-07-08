import json

import pandas as pd

from freqtrade.markets import CachedAShareCalendar
from freqtrade.research.side_data.collectors.a_share_side_data import (
    AShareSideDataCollector,
    AShareSideDataRequest,
)


class FakeSideDataProvider:
    provider_name = "fake"
    provider_version = "1.0"

    def fetch_fund_flow_daily(
        self,
        instrument_key: str,
        start_date: str | None,
        end_date: str | None,
    ) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "date": ["2026-07-07"],
                "instrument": [instrument_key],
                "main_net_inflow": [1000.0],
                "large_net_inflow": [800.0],
                "medium_net_inflow": [100.0],
                "small_net_inflow": [100.0],
                "source": ["fake"],
                "publish_time": ["2026-07-07T15:30:00+08:00"],
                "ingest_time": ["2026-07-07T16:00:00+08:00"],
            }
        )

    def fetch_limit_pool(self, trade_date: str) -> list[dict]:
        return [
            {
                "schema_version": 1,
                "event_id": f"limit:{trade_date}:600519.SH",
                "dataset": "limit_pool",
                "market": "a_share",
                "instrument": "600519.SH",
                "event_type": "limit_up",
                "event_time": f"{trade_date}T15:00:00+08:00",
                "publish_time": f"{trade_date}T15:05:00+08:00",
                "ingest_time": f"{trade_date}T16:00:00+08:00",
                "effective_candle_time": f"{trade_date} 00:00:00+00:00",
                "title": "Limit up",
                "payload": {"reason": "theme"},
                "source": "fake",
            }
        ]

    def fetch_announcements(
        self,
        instrument_key: str,
        start_date: str | None,
        end_date: str | None,
    ) -> list[dict]:
        return [
            {
                "schema_version": 1,
                "document_id": f"fake:{instrument_key}:1",
                "dataset": "announcements",
                "market": "a_share",
                "instrument": instrument_key,
                "document_type": "announcement",
                "title": "Announcement",
                "publish_time": "2026-07-07T19:30:00+08:00",
                "ingest_time": "2026-07-07T20:00:00+08:00",
                "effective_candle_time": "2026-07-08 00:00:00+00:00",
                "url": "https://example.invalid/a.pdf",
                "source": "fake",
                "payload": {"category": "notice"},
            }
        ]


class PartialFailureProvider(FakeSideDataProvider):
    provider_name = "mixed"

    def fetch_limit_pool(self, trade_date: str) -> list[dict]:
        raise RuntimeError("limit feed down")


def test_side_data_collector_writes_artifacts_and_manifest(tmp_path) -> None:
    collector = AShareSideDataCollector(tmp_path, FakeSideDataProvider())

    summary = collector.collect(
        AShareSideDataRequest(
            instruments=["600519.SH"],
            datasets=["fund_flow_daily", "limit_pool", "announcements"],
            start_date="2026-07-07",
            end_date="2026-07-07",
        )
    )

    assert summary.failed == 0
    assert summary.succeeded == 3
    assert (tmp_path / "features" / "fund_flow_daily" / "600519.SH.csv").is_file()
    assert (tmp_path / "events" / "limit_pool" / "2026-07-07.jsonl").is_file()
    assert (tmp_path / "documents" / "announcements" / "600519.SH.jsonl").is_file()
    manifests = list((tmp_path / ".manifests").glob("*.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["provider"] == "fake"
    assert manifest["datasets"] == ["fund_flow_daily", "limit_pool", "announcements"]
    assert all(not file_summary["path"].startswith("G:") for file_summary in manifest["files"])


def test_side_data_collector_records_partial_failure_without_partial_artifact(tmp_path) -> None:
    collector = AShareSideDataCollector(tmp_path, PartialFailureProvider())

    summary = collector.collect(
        AShareSideDataRequest(
            instruments=["600519.SH"],
            datasets=["fund_flow_daily", "limit_pool", "announcements"],
            start_date="2026-07-07",
            end_date="2026-07-07",
        )
    )

    assert summary.succeeded == 2
    assert summary.failed == 1
    assert [(file.path, file.status) for file in summary.files] == [
        ("features/fund_flow_daily/600519.SH.csv", "ok"),
        ("events/limit_pool/2026-07-07.jsonl", "error"),
        ("documents/announcements/600519.SH.jsonl", "ok"),
    ]
    assert summary.files[1].error == "limit feed down"
    assert (tmp_path / "features" / "fund_flow_daily" / "600519.SH.csv").is_file()
    assert not (tmp_path / "events" / "limit_pool" / "2026-07-07.jsonl").exists()
    assert (tmp_path / "documents" / "announcements" / "600519.SH.jsonl").is_file()


def test_side_data_collector_uses_calendar_for_limit_pool_dates(tmp_path) -> None:
    calendar = CachedAShareCalendar(
        open_dates={
            pd.Timestamp("2026-07-07").date(),
            pd.Timestamp("2026-07-09").date(),
        },
        known_dates={
            pd.Timestamp("2026-07-07").date(),
            pd.Timestamp("2026-07-08").date(),
            pd.Timestamp("2026-07-09").date(),
        },
    )
    collector = AShareSideDataCollector(tmp_path, FakeSideDataProvider(), calendar=calendar)

    summary = collector.collect(
        AShareSideDataRequest(
            instruments=["600519.SH"],
            datasets=["limit_pool"],
            start_date="2026-07-07",
            end_date="2026-07-09",
        )
    )

    assert summary.failed == 0
    assert [file.path for file in summary.files] == [
        "events/limit_pool/2026-07-07.jsonl",
        "events/limit_pool/2026-07-09.jsonl",
    ]
    assert not (tmp_path / "events" / "limit_pool" / "2026-07-08.jsonl").exists()
