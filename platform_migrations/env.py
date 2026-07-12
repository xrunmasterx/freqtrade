import os
import re

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import make_url

from freqtrade.platform import catalog_repository, runtime_models  # noqa: F401
from freqtrade.platform.database import PlatformBase


config = context.config
target_metadata = PlatformBase.metadata
_TEST_DATABASE_PATTERN = re.compile(r"^platform_test[a-z0-9_]*$")


def _database_url() -> str:
    configured_url = config.get_main_option("sqlalchemy.url")
    if configured_url:
        return configured_url
    test_url = os.environ.get("PLATFORM_TEST_POSTGRES_URL")
    if test_url:
        parsed_url = make_url(test_url)
        database_name = parsed_url.database or ""
        if (
            parsed_url.get_backend_name() != "postgresql"
            or _TEST_DATABASE_PATTERN.fullmatch(database_name) is None
        ):
            raise RuntimeError(
                "PLATFORM_TEST_POSTGRES_URL must name an isolated test PostgreSQL database"
            )
        return test_url
    raise RuntimeError("platform migration database URL is not configured")


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_database_url(), poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
