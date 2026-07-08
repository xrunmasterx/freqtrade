from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ResearchDataProvenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_type: str = "local_csv"
    artifact_path: str
    manifest_run_id: str | None = None
    manifest_path: str | None = None
    provider: str | None = None
    provider_version: str | None = None
    rows: int | None = None
    start: str | None = None
    stop: str | None = None
    adjustment: str | None = None
    created_at: str | None = None
    warnings: list[str] = Field(default_factory=list)


def _parse_manifest_created_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)

    return parsed.astimezone(UTC)


def find_local_csv_provenance(root: Path, artifact_path: str) -> ResearchDataProvenance:
    manifest_dir = root / ".manifests"
    best: tuple[datetime | None, str, Path, dict] | None = None

    if manifest_dir.is_dir():
        for path in manifest_dir.glob("*.json"):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

            created_at = str(manifest.get("created_at", ""))
            parsed_created_at = _parse_manifest_created_at(manifest.get("created_at"))
            for file_summary in manifest.get("files", []):
                if (
                    file_summary.get("path") == artifact_path
                    and file_summary.get("status") == "ok"
                ):
                    candidate = (parsed_created_at, created_at, path, manifest)
                    if best is None or _is_better_manifest_candidate(candidate, best):
                        best = candidate

    if best is None:
        return ResearchDataProvenance(artifact_path=artifact_path)

    _, _, manifest_path, manifest = best
    file_summary = next(
        item
        for item in manifest.get("files", [])
        if item.get("path") == artifact_path and item.get("status") == "ok"
    )
    return ResearchDataProvenance(
        artifact_path=artifact_path,
        manifest_run_id=manifest.get("run_id"),
        manifest_path=str(manifest_path.relative_to(root)),
        provider=manifest.get("provider"),
        provider_version=manifest.get("provider_version"),
        rows=file_summary.get("rows"),
        start=file_summary.get("start"),
        stop=file_summary.get("stop"),
        adjustment=manifest.get("adjustment"),
        created_at=manifest.get("created_at"),
        warnings=file_summary.get("warnings") or [],
    )


def _is_better_manifest_candidate(
    candidate: tuple[datetime | None, str, Path, dict],
    current_best: tuple[datetime | None, str, Path, dict],
) -> bool:
    candidate_dt, candidate_text, _, _ = candidate
    best_dt, best_text, _, _ = current_best

    if candidate_dt is not None:
        if best_dt is None:
            return True
        if candidate_dt != best_dt:
            return candidate_dt > best_dt
    elif best_dt is not None:
        return False

    return candidate_text > best_text
