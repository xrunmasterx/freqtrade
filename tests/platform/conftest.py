import os

import pytest

from tests.platform.postgres_test_support import (
    POSTGRES_SKIP_REASON,
    RedactedPostgresUrl,
    reset_public_schema,
    validate_test_database_url,
)


@pytest.fixture
def postgres_url() -> str:
    raw_url = os.environ.get("PLATFORM_TEST_POSTGRES_URL")
    if raw_url is None:
        pytest.skip(POSTGRES_SKIP_REASON)
    validate_test_database_url(raw_url)
    test_url = RedactedPostgresUrl(raw_url)

    reset_public_schema(test_url)
    try:
        yield test_url
    finally:
        reset_public_schema(test_url)
