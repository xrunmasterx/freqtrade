import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from freqtrade.platform.database import PlatformDatabaseSettings
from freqtrade.platform.runtime_domain import Identifier


class PlatformControlSettingsError(RuntimeError):
    pass


class PlatformControlSecretError(RuntimeError):
    pass


class PlatformControlSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    listen_host: Literal["127.0.0.1", "::1"] = "127.0.0.1"
    listen_port: int = Field(default=8090, ge=1, le=65535, strict=True)
    username: Identifier
    api_password_file: Path = Field(exclude=True, repr=False)
    jwt_secret_file: Path = Field(exclude=True, repr=False)
    database: PlatformDatabaseSettings = Field(exclude=True, repr=False)

    @model_validator(mode="after")
    def validate_secret_paths(self) -> "PlatformControlSettings":
        paths = (
            self.api_password_file,
            self.jwt_secret_file,
            self.database.password_file,
        )
        if any(not path.is_absolute() for path in paths):
            raise ValueError("secret paths must be absolute")
        normalized = {os.path.normcase(os.path.normpath(path)) for path in paths}
        if len(normalized) != len(paths):
            raise ValueError("secret paths must be distinct")
        return self

    @classmethod
    def from_values(cls, values: Mapping[str, object]) -> "PlatformControlSettings":
        try:
            return cls.model_validate(values)
        except (TypeError, ValueError, ValidationError):
            raise PlatformControlSettingsError("invalid_platform_control_settings") from None

    @classmethod
    def from_env(cls) -> "PlatformControlSettings":
        try:
            values: dict[str, object] = {
                "listen_host": os.environ.get(
                    "PLATFORM_CONTROL_LISTEN_HOST",
                    "127.0.0.1",
                ),
                "listen_port": int(os.environ.get("PLATFORM_CONTROL_LISTEN_PORT", "8090")),
                "username": os.environ["PLATFORM_CONTROL_USERNAME"],
                "api_password_file": Path(os.environ["PLATFORM_CONTROL_API_PASSWORD_FILE"]),
                "jwt_secret_file": Path(os.environ["PLATFORM_CONTROL_JWT_SECRET_FILE"]),
                "database": PlatformDatabaseSettings(
                    host=os.environ["PLATFORM_DATABASE_HOST"],
                    port=int(os.environ["PLATFORM_DATABASE_PORT"]),
                    database=os.environ["PLATFORM_DATABASE_NAME"],
                    username=os.environ["PLATFORM_DATABASE_USERNAME"],
                    password_file=Path(os.environ["PLATFORM_DATABASE_PASSWORD_FILE"]),
                ),
            }
        except (KeyError, TypeError, ValueError, ValidationError):
            raise PlatformControlSettingsError("invalid_platform_control_settings") from None
        return cls.from_values(values)


@dataclass(frozen=True, repr=False)
class _PlatformSecrets:
    _api_password: str
    _jwt_secret: str

    def __repr__(self) -> str:
        return "<redacted platform-control secrets>"


def _read_secret(path: Path) -> str:
    try:
        value = path.read_bytes().decode("utf-8").rstrip("\r\n")
    except (OSError, UnicodeError):
        raise PlatformControlSecretError("invalid_platform_control_secret") from None
    if not value or "\r" in value or "\n" in value or "\x00" in value:
        raise PlatformControlSecretError("invalid_platform_control_secret")
    return value


def _validate_secret_file_identities(settings: PlatformControlSettings) -> None:
    paths = (
        settings.api_password_file,
        settings.jwt_secret_file,
        settings.database.password_file,
    )
    try:
        canonical_paths = tuple(path.resolve(strict=True) for path in paths)
        if any(not path.is_file() for path in canonical_paths):
            raise OSError
        if any(
            left == right or left.samefile(right)
            for left, right in combinations(canonical_paths, 2)
        ):
            raise OSError
    except (OSError, RuntimeError):
        raise PlatformControlSecretError("invalid_platform_control_secret") from None


def load_platform_secrets(settings: PlatformControlSettings) -> _PlatformSecrets:
    _validate_secret_file_identities(settings)
    api_password = _read_secret(settings.api_password_file)
    jwt_secret = _read_secret(settings.jwt_secret_file)
    if len(jwt_secret) < 32 or secrets.compare_digest(
        api_password.encode("utf-8"),
        jwt_secret.encode("utf-8"),
    ):
        raise PlatformControlSecretError("invalid_platform_control_secret")
    return _PlatformSecrets(api_password, jwt_secret)
