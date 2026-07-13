import runpy
from pathlib import Path


BACKEND_ROOT = Path(__file__).parents[2]
MIGRATION_PATH = (
    BACKEND_ROOT
    / "platform_migrations"
    / "versions"
    / "20260714_0003_template_audit_actions.py"
)


def test_template_audit_migration_is_additive_and_controls_public_schema() -> None:
    assert MIGRATION_PATH.is_file()
    migration = runpy.run_path(str(MIGRATION_PATH))
    assert migration["revision"] == "20260714_0003"
    assert migration["down_revision"] == "20260712_0002"

    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "SET LOCAL search_path TO public, pg_catalog" in source
    assert "platform_migration_schema_control_failed" in source
    assert "publish_template" in source
    assert "deprecate_template" in source
    assert "revoke_template" in source
