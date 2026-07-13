import re

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


TEST_DATABASE_PATTERN = re.compile(r"^platform_test[a-z0-9_]*$")
TEST_DATABASE_OVERRIDE_QUERY_KEYS = {"database", "dbname"}
POSTGRES_SKIP_REASON = "PLATFORM_TEST_POSTGRES_URL is required for PostgreSQL migrations"


class RedactedPostgresUrl(str):
    def __repr__(self) -> str:
        return "'<redacted platform test PostgreSQL URL>'"


def validate_test_database_url(postgres_url: str) -> None:
    parsed_url = make_url(postgres_url)
    query_keys = {key.casefold() for key in parsed_url.query}
    database_name = parsed_url.database or ""
    if (
        parsed_url.get_backend_name() != "postgresql"
        or TEST_DATABASE_PATTERN.fullmatch(database_name) is None
        or query_keys & TEST_DATABASE_OVERRIDE_QUERY_KEYS
    ):
        raise RuntimeError("refusing an unsafe platform test database URL")


def require_effective_test_database(database_name: str) -> None:
    if TEST_DATABASE_PATTERN.fullmatch(database_name) is None:
        raise RuntimeError("refusing to reset a non-test platform database")


def reset_public_schema(postgres_url: str) -> None:
    validate_test_database_url(postgres_url)
    engine = create_engine(postgres_url)
    try:
        with engine.begin() as connection:
            database_name = connection.exec_driver_sql("SELECT current_database()").scalar_one()
            require_effective_test_database(database_name)
            connection.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
            connection.exec_driver_sql("CREATE SCHEMA public")
    finally:
        engine.dispose()
