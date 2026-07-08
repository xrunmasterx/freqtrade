import json

from freqtrade.research.provenance import find_local_csv_provenance


def test_find_local_csv_provenance_uses_latest_ok_manifest(tmp_path) -> None:
    manifest_dir = tmp_path / ".manifests"
    manifest_dir.mkdir()
    (tmp_path / "600519.SH-1d.csv").write_text(
        "date,open,high,low,close,volume\n2024-01-02,1,1,1,1,1\n",
        encoding="utf-8",
    )
    (manifest_dir / "old.json").write_text(
        json.dumps(
            {
                "run_id": "old",
                "provider": "akshare",
                "provider_version": "1.0",
                "created_at": "2026-07-07T01:00:00+00:00",
                "adjustment": "raw",
                "timerange": {"start": "20240101", "end": "20240131"},
                "files": [
                    {
                        "path": "600519.SH-1d.csv",
                        "rows": 1,
                        "start": "2024-01-02",
                        "stop": "2024-01-02",
                        "status": "ok",
                        "warnings": [],
                        "error": None,
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
                "provider": "akshare",
                "provider_version": "1.18.64",
                "created_at": "2026-07-07T02:00:00+00:00",
                "adjustment": "raw",
                "timerange": {"start": "20240101", "end": "20240701"},
                "files": [
                    {
                        "path": "600519.SH-1d.csv",
                        "rows": 118,
                        "start": "2024-01-02",
                        "stop": "2024-07-01",
                        "status": "ok",
                        "warnings": [],
                        "error": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    provenance = find_local_csv_provenance(tmp_path, "600519.SH-1d.csv")

    assert provenance.source_type == "local_csv"
    assert provenance.artifact_path == "600519.SH-1d.csv"
    assert provenance.manifest_run_id == "new"
    assert provenance.provider == "akshare"
    assert provenance.provider_version == "1.18.64"
    assert provenance.rows == 118
    assert provenance.start == "2024-01-02"
    assert provenance.stop == "2024-07-01"
    assert provenance.adjustment == "raw"


def test_find_local_csv_provenance_falls_back_without_manifest(tmp_path) -> None:
    (tmp_path / "600519.SH-1d.csv").write_text(
        "date,open,high,low,close,volume\n2024-01-02,1,1,1,1,1\n",
        encoding="utf-8",
    )

    provenance = find_local_csv_provenance(tmp_path, "600519.SH-1d.csv")

    assert provenance.source_type == "local_csv"
    assert provenance.artifact_path == "600519.SH-1d.csv"
    assert provenance.manifest_run_id is None
    assert provenance.provider is None


def test_find_local_csv_provenance_orders_timezones_chronologically(tmp_path) -> None:
    manifest_dir = tmp_path / ".manifests"
    manifest_dir.mkdir()
    (tmp_path / "600519.SH-1d.csv").write_text(
        "date,open,high,low,close,volume\n2024-01-02,1,1,1,1,1\n",
        encoding="utf-8",
    )
    (manifest_dir / "lexically-later.json").write_text(
        json.dumps(
            {
                "run_id": "lexically-later",
                "provider": "akshare",
                "provider_version": "1.0",
                "created_at": "2026-07-07T10:00:00+08:00",
                "adjustment": "raw",
                "files": [
                    {
                        "path": "600519.SH-1d.csv",
                        "rows": 10,
                        "start": "2024-01-02",
                        "stop": "2024-01-15",
                        "status": "ok",
                        "warnings": [],
                        "error": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (manifest_dir / "chronologically-later.json").write_text(
        json.dumps(
            {
                "run_id": "chronologically-later",
                "provider": "akshare",
                "provider_version": "1.1",
                "created_at": "2026-07-07T03:30:00+00:00",
                "adjustment": "raw",
                "files": [
                    {
                        "path": "600519.SH-1d.csv",
                        "rows": 20,
                        "start": "2024-01-02",
                        "stop": "2024-01-31",
                        "status": "ok",
                        "warnings": [],
                        "error": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    provenance = find_local_csv_provenance(tmp_path, "600519.SH-1d.csv")

    assert provenance.manifest_run_id == "chronologically-later"
    assert provenance.provider_version == "1.1"
    assert provenance.rows == 20
