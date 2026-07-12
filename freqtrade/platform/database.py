from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session


class PlatformBase(DeclarativeBase):
    pass


class PlatformDatabaseSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    database: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    username: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    password_file: Path

    @model_validator(mode="after")
    def require_postgres_host(self) -> "PlatformDatabaseSettings":
        if self.host == "sqlite":
            raise ValueError("PostgreSQL is required for production platform settings")
        return self

    def read_password(self) -> str:
        value = self.password_file.read_text(encoding="utf-8").rstrip("\r\n")
        if not value or "\n" in value or "\r" in value or "\x00" in value:
            raise ValueError("platform database password file is invalid")
        return value

    def sqlalchemy_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.username,
            password=self.read_password(),
            host=self.host,
            port=self.port,
            database=self.database,
        )


def create_platform_engine(settings: PlatformDatabaseSettings) -> Engine:
    return create_engine(settings.sqlalchemy_url(), pool_pre_ping=True, future=True)


@contextmanager
def platform_session(engine: Engine) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as session:
        yield session
