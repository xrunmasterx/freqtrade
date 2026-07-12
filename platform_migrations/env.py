import os
import re

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import Connection, make_url

from freqtrade.platform import catalog_repository, runtime_models  # noqa: F401
from freqtrade.platform.database import PlatformBase


config = context.config
target_metadata = PlatformBase.metadata
_TEST_DATABASE_PATTERN = re.compile(r"^platform_test[a-z0-9_]*$")
_DATABASE_OVERRIDE_QUERY_KEYS = {"database", "dbname"}


def _include_object(_object, name: str | None, type_: str, _reflected: bool, _compare_to) -> bool:
    return not (type_ == "table" and name == "alembic_version")


def _database_url() -> tuple[str, bool]:
    configured_url = config.get_main_option("sqlalchemy.url")
    if configured_url:
        return configured_url, False
    test_url = os.environ.get("PLATFORM_TEST_POSTGRES_URL")
    if test_url:
        parsed_url = make_url(test_url)
        database_name = parsed_url.database or ""
        query_keys = {key.casefold() for key in parsed_url.query}
        if (
            parsed_url.get_backend_name() != "postgresql"
            or _TEST_DATABASE_PATTERN.fullmatch(database_name) is None
            or query_keys & _DATABASE_OVERRIDE_QUERY_KEYS
        ):
            raise RuntimeError(
                "PLATFORM_TEST_POSTGRES_URL must name an isolated test PostgreSQL database"
            )
        return test_url, True
    raise RuntimeError("platform migration database URL is not configured")


def _require_effective_test_database(connection: Connection) -> None:
    database_name = connection.exec_driver_sql("SELECT current_database()").scalar_one()
    if _TEST_DATABASE_PATTERN.fullmatch(database_name) is None:
        raise RuntimeError(
            "PLATFORM_TEST_POSTGRES_URL must name an isolated test PostgreSQL database"
        )


def run_migrations_offline() -> None:
    database_url, _is_test_url = _database_url()
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        version_table_schema="public",
        include_object=_include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    database_url, is_test_url = _database_url()
    connectable = create_engine(database_url, poolclass=pool.NullPool)

    try:
        with connectable.connect() as connection:
            if is_test_url:
                _require_effective_test_database(connection)
                connection.commit()
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                version_table_schema="public",
                include_object=_include_object,
            )

            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
