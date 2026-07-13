from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def find_side_data_provenance(root: Path, artifact_path: str) -> dict[str, Any]:
    manifest_dir = root / ".manifests"
    best: tuple[datetime | None, str, Path, dict[str, Any], dict[str, Any]] | None = None

    if manifest_dir.is_dir():
        for path in manifest_dir.glob("*.json"):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

            if not isinstance(manifest, dict):
                continue

            files = manifest.get("files")
            if not isinstance(files, list):
                continue

            created_at_text = str(manifest.get("created_at", ""))
            parsed_created_at = _parse_created_at(manifest.get("created_at"))
            for file_summary in files:
                if not isinstance(file_summary, dict):
                    continue
                if file_summary.get("path") != artifact_path:
                    continue
                if file_summary.get("status") != "ok":
                    continue

                candidate = (
                    parsed_created_at,
                    created_at_text,
                    path,
                    manifest,
                    file_summary,
                )
                if best is None or _is_better(candidate, best):
                    best = candidate

    if best is None:
        return {
            "artifact_path": artifact_path,
            "manifest_run_id": None,
        }

    _, _, manifest_path, manifest, file_summary = best
    return {
        "artifact_path": artifact_path,
        "manifest_run_id": manifest.get("run_id"),
        "manifest_path": str(manifest_path.relative_to(root)),
        "provider": manifest.get("provider"),
        "provider_version": manifest.get("provider_version"),
        "rows": file_summary.get("rows"),
        "start": file_summary.get("start"),
        "stop": file_summary.get("stop"),
        "dataset": file_summary.get("dataset"),
        "kind": file_summary.get("kind"),
        "created_at": manifest.get("created_at"),
        "warnings": file_summary.get("warnings") or [],
    }


def _parse_created_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)

    return parsed.astimezone(UTC)


def _is_better(
    candidate: tuple[datetime | None, str, Path, dict[str, Any], dict[str, Any]],
    current: tuple[datetime | None, str, Path, dict[str, Any], dict[str, Any]],
) -> bool:
    candidate_dt, candidate_text, _, _, _ = candidate
    current_dt, current_text, _, _, _ = current

    if candidate_dt is not None:
        if current_dt is None:
            return True
        if candidate_dt != current_dt:
            return candidate_dt > current_dt
    elif current_dt is not None:
        return False

    return candidate_text > current_text
