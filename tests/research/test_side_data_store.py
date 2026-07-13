import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from freqtrade.research.side_data.models import ResearchDocument, ResearchEvent
from freqtrade.research.side_data.provenance import find_side_data_provenance
from freqtrade.research.side_data.store import LocalResearchSideDataStore


def test_research_event_requires_stable_identity_and_effective_time() -> None:
    event = ResearchEvent(
        event_id="a-share-limit-pool:2026-07-07:600519.SH:limit_up",
        dataset="limit_pool",
        market="a_share",
        instrument="600519.SH",
        event_type="limit_up",
        event_time="2026-07-07T15:00:00+08:00",
        publish_time="2026-07-07T15:05:00+08:00",
        ingest_time="2026-07-07T16:00:00+08:00",
        effective_candle_time="2026-07-08 00:00:00+00:00",
        title="Limit up",
        source="eastmoney",
        payload={"reason": "sector theme"},
    )

    assert event.schema_version == 1
    assert event.instrument == "600519.SH"
    assert event.payload["reason"] == "sector theme"


def test_research_document_rejects_invalid_market() -> None:
    with pytest.raises(ValidationError):
        ResearchDocument(
            document_id="bad",
            dataset="announcements",
            market="crypto",
            instrument="600519.SH",
            document_type="announcement",
            title="Announcement",
            publish_time="2026-07-07T19:30:00+08:00",
            ingest_time="2026-07-07T20:00:00+08:00",
            effective_candle_time="2026-07-08 00:00:00+00:00",
            source="cninfo",
        )


def test_find_side_data_provenance_uses_latest_ok_manifest(tmp_path) -> None:
    manifest_dir = tmp_path / ".manifests"
    manifest_dir.mkdir()
    artifact_path = "events/limit_pool/2026-07-07.jsonl"

    (manifest_dir / "old.json").write_text(
        json.dumps(
            {
                "run_id": "old",
                "provider": "eastmoney",
                "provider_version": "1.0",
                "created_at": "2026-07-07T01:00:00+00:00",
                "files": [
                    {
                        "path": artifact_path,
                        "dataset": "limit_pool",
                        "kind": "event",
                        "rows": 12,
                        "start": "2026-07-01",
                        "stop": "2026-07-07",
                        "status": "ok",
                        "warnings": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (manifest_dir / "new.json").write_text(
        json.dumps(
            {
                "run_id": "new",
                "provider": "eastmoney",
                "provider_version": "1.1",
                "created_at": "2026-07-07T02:00:00+00:00",
                "files": [
                    {
                        "path": artifact_path,
                        "dataset": "limit_pool",
                        "kind": "event",
                        "rows": 20,
                        "start": "2026-07-01",
                        "stop": "2026-07-08",
                        "status": "ok",
                        "warnings": ["late corrections"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    provenance = find_side_data_provenance(tmp_path, artifact_path)

    assert provenance["artifact_path"] == artifact_path
    assert provenance["manifest_run_id"] == "new"
    assert provenance["provider"] == "eastmoney"
    assert provenance["provider_version"] == "1.1"
    assert provenance["rows"] == 20
    assert provenance["start"] == "2026-07-01"
    assert provenance["stop"] == "2026-07-08"
    assert provenance["dataset"] == "limit_pool"
    assert provenance["kind"] == "event"
    assert provenance["warnings"] == ["late corrections"]


def test_find_side_data_provenance_falls_back_without_manifest(tmp_path) -> None:
    artifact_path = "events/limit_pool/2026-07-07.jsonl"

    provenance = find_side_data_provenance(tmp_path, artifact_path)

    assert provenance == {
        "artifact_path": artifact_path,
        "manifest_run_id": None,
    }


def test_find_side_data_provenance_skips_malformed_manifest_structures(tmp_path) -> None:
    manifest_dir = tmp_path / ".manifests"
    manifest_dir.mkdir()
    artifact_path = "events/limit_pool/2026-07-07.jsonl"

    (manifest_dir / "broken.json").write_text("{not json", encoding="utf-8")
    (manifest_dir / "root-list.json").write_text("[]", encoding="utf-8")
    (manifest_dir / "files-not-list.json").write_text(
        json.dumps(
            {
                "run_id": "bad-files",
                "created_at": "2026-07-07T01:00:00+00:00",
                "files": {},
            }
        ),
        encoding="utf-8",
    )
    (manifest_dir / "file-entry-not-dict.json").write_text(
        json.dumps(
            {
                "run_id": "bad-entry",
                "created_at": "2026-07-07T02:00:00+00:00",
                "files": ["not-a-dict"],
            }
        ),
        encoding="utf-8",
    )
    (manifest_dir / "valid.json").write_text(
        json.dumps(
            {
                "run_id": "valid",
                "provider": "eastmoney",
                "provider_version": "1.2",
                "created_at": "2026-07-07T03:00:00+00:00",
                "files": [
                    {
                        "path": artifact_path,
                        "dataset": "limit_pool",
                        "kind": "event",
                        "rows": 21,
                        "start": "2026-07-01",
                        "stop": "2026-07-08",
                        "status": "ok",
                        "warnings": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    provenance = find_side_data_provenance(tmp_path, artifact_path)

    assert provenance["manifest_run_id"] == "valid"
    assert provenance["rows"] == 21


def test_find_side_data_provenance_returns_fallback_when_all_manifests_are_malformed(
    tmp_path,
) -> None:
    manifest_dir = tmp_path / ".manifests"
    manifest_dir.mkdir()
    artifact_path = "events/limit_pool/2026-07-07.jsonl"

    (manifest_dir / "broken.json").write_text("{not json", encoding="utf-8")
    (manifest_dir / "root-string.json").write_text('"bad-root"', encoding="utf-8")
    (manifest_dir / "files-string.json").write_text(
        json.dumps(
            {
                "run_id": "bad-files",
                "files": "not-a-list",
            }
        ),
        encoding="utf-8",
    )
    (manifest_dir / "entry-number.json").write_text(
        json.dumps(
            {
                "run_id": "bad-entry",
                "files": [123],
            }
        ),
        encoding="utf-8",
    )

    provenance = find_side_data_provenance(tmp_path, artifact_path)

    assert provenance == {
        "artifact_path": artifact_path,
        "manifest_run_id": None,
    }


def _write_side_data_fixture(root: Path) -> None:
    (root / "features" / "fund_flow_daily").mkdir(parents=True)
    (root / "events" / "limit_pool").mkdir(parents=True)
    (root / "documents" / "announcements").mkdir(parents=True)
    (root / "features" / "fund_flow_daily" / "600519.SH.csv").write_text(
        "date,instrument,main_net_inflow,large_net_inflow,medium_net_inflow,"
        "small_net_inflow,source,publish_time,ingest_time\n"
        "2026-07-07,600519.SH,1000,800,100,100,eastmoney,"
        "2026-07-07T15:30:00+08:00,2026-07-07T16:00:00+08:00\n",
        encoding="utf-8",
    )
    (root / "events" / "limit_pool" / "2026-07-07.jsonl").write_text(
        '{"schema_version":1,"event_id":"limit:2026-07-07:600519.SH",'
        '"dataset":"limit_pool","market":"a_share","instrument":"600519.SH",'
        '"event_type":"limit_up","event_time":"2026-07-07T15:00:00+08:00",'
        '"publish_time":"2026-07-07T15:05:00+08:00",'
        '"ingest_time":"2026-07-07T16:00:00+08:00",'
        '"effective_candle_time":"2026-07-07 00:00:00+00:00",'
        '"title":"Limit up","payload":{"reason":"theme"},"source":"eastmoney"}\n',
        encoding="utf-8",
    )
    (root / "documents" / "announcements" / "600519.SH.jsonl").write_text(
        '{"schema_version":1,"document_id":"cninfo:600519.SH:1",'
        '"dataset":"announcements","market":"a_share","instrument":"600519.SH",'
        '"document_type":"announcement","title":"Announcement",'
        '"publish_time":"2026-07-07T19:30:00+08:00",'
        '"ingest_time":"2026-07-07T20:00:00+08:00",'
        '"effective_candle_time":"2026-07-08 00:00:00+00:00",'
        '"url":"https://example.invalid/a.pdf","source":"cninfo",'
        '"payload":{"category":"notice"}}\n',
        encoding="utf-8",
    )


def _write_side_data_manifest(
    root: Path,
    *,
    artifact_path: str,
    dataset: str,
    kind: str,
    start: str,
    stop: str,
    provider: str = "eastmoney",
    provider_version: str = "1.1",
    run_id: str = "task-3-fixture",
    warnings: list[str] | None = None,
) -> None:
    manifest_dir = root / ".manifests"
    manifest_dir.mkdir(exist_ok=True)
    (manifest_dir / "fixture.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "provider": provider,
                "provider_version": provider_version,
                "created_at": "2026-07-07T20:30:00+08:00",
                "files": [
                    {
                        "path": artifact_path,
                        "dataset": dataset,
                        "kind": kind,
                        "rows": 1,
                        "start": start,
                        "stop": stop,
                        "status": "ok",
                        "warnings": warnings or [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_local_side_data_store_lists_available_datasets(tmp_path) -> None:
    _write_side_data_fixture(tmp_path)
    _write_side_data_manifest(
        tmp_path,
        artifact_path="features/fund_flow_daily/600519.SH.csv",
        dataset="fund_flow_daily",
        kind="feature",
        start="2026-07-07",
        stop="2026-07-07",
        warnings=["late correction"],
    )
    store = LocalResearchSideDataStore(tmp_path)

    datasets = store.list_datasets(instrument_key="600519.SH")

    assert [item.dataset_id for item in datasets] == [
        "fund_flow_daily",
        "limit_pool",
        "announcements",
    ]
    assert datasets[0].kind == "feature"
    assert datasets[0].available is True
    assert datasets[0].provider == "eastmoney"
    assert datasets[0].provider_version == "1.1"
    assert datasets[0].manifest_run_id == "task-3-fixture"
    assert datasets[0].start == "2026-07-07"
    assert datasets[0].stop == "2026-07-07"
    assert datasets[0].warnings == ["late correction"]


def test_local_side_data_store_loads_feature_frame(tmp_path) -> None:
    _write_side_data_fixture(tmp_path)
    store = LocalResearchSideDataStore(tmp_path)

    frame = store.load_feature_frame("600519.SH", ["fund_flow_daily"])

    assert list(frame.columns) == [
        "date",
        "instrument",
        "feature_fund_flow_daily_main_net_inflow",
        "feature_fund_flow_daily_large_net_inflow",
        "feature_fund_flow_daily_medium_net_inflow",
        "feature_fund_flow_daily_small_net_inflow",
        "source",
        "publish_time",
        "ingest_time",
    ]
    assert frame.iloc[0]["feature_fund_flow_daily_main_net_inflow"] == 1000.0


def test_local_side_data_store_loads_events_and_documents(tmp_path) -> None:
    _write_side_data_fixture(tmp_path)
    store = LocalResearchSideDataStore(tmp_path)

    events = store.load_events("600519.SH", ["limit_pool"])
    documents = store.load_documents("600519.SH", ["announcements"])

    assert events[0].event_type == "limit_up"
    assert events[0].payload == {"reason": "theme"}
    assert documents[0].document_type == "announcement"
    assert documents[0].title == "Announcement"


def test_local_side_data_store_rejects_unknown_dataset(tmp_path) -> None:
    store = LocalResearchSideDataStore(tmp_path)

    with pytest.raises(ValueError, match=r"^Unknown research side dataset: unknown$"):
        store.load_feature_frame("600519.SH", ["unknown"])


def test_local_side_data_store_disables_all_datasets_with_empty_allowlist(tmp_path) -> None:
    _write_side_data_fixture(tmp_path)
    store = LocalResearchSideDataStore(tmp_path, enabled_datasets=[])

    assert store.list_datasets(instrument_key="600519.SH") == []

    with pytest.raises(
        ValueError,
        match=r"^Unknown research side dataset: fund_flow_daily$",
    ):
        store.load_feature_frame("600519.SH", ["fund_flow_daily"])

    with pytest.raises(
        ValueError,
        match=r"^Unknown research side dataset: limit_pool$",
    ):
        store.load_events("600519.SH", ["limit_pool"])


def test_local_side_data_store_lists_only_enabled_subset(tmp_path) -> None:
    _write_side_data_fixture(tmp_path)
    store = LocalResearchSideDataStore(
        tmp_path,
        enabled_datasets=["limit_pool", "announcements"],
    )

    datasets = store.list_datasets(instrument_key="600519.SH")

    assert [item.dataset_id for item in datasets] == ["limit_pool", "announcements"]


def test_local_side_data_store_rejects_incompatible_dataset_kind(tmp_path) -> None:
    _write_side_data_fixture(tmp_path)
    store = LocalResearchSideDataStore(tmp_path)

    with pytest.raises(
        ValueError,
        match=r"^Research side dataset announcements has incompatible kind$",
    ):
        store.load_events("600519.SH", ["announcements"])
