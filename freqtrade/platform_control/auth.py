import secrets
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials, OAuth2PasswordBearer

from freqtrade.platform_control.settings import PlatformControlSettings, _PlatformSecrets
from freqtrade.rpc.api_server.api_auth import create_token, get_user_from_token
from freqtrade.rpc.api_server.api_schemas import AccessAndRefreshToken, AccessToken


_BASIC = HTTPBasic(auto_error=False)
_BEARER = OAuth2PasswordBearer(tokenUrl="/api/v2/token/login", auto_error=False)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="unauthorized",
        headers={"WWW-Authenticate": "Basic, Bearer"},
    )


def _compare_text(left: str, right: str) -> bool:
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _verify_basic(
    credentials: HTTPBasicCredentials,
    settings: PlatformControlSettings,
    platform_secrets: _PlatformSecrets,
) -> bool:
    username_matches = _compare_text(credentials.username, settings.username)
    password_matches = _compare_text(
        credentials.password,
        platform_secrets._api_password,
    )
    return username_matches and password_matches


def _token_user(
    token: str,
    token_type: str,
    settings: PlatformControlSettings,
    platform_secrets: _PlatformSecrets,
) -> str:
    try:
        username = get_user_from_token(token, platform_secrets._jwt_secret, token_type)
    except (AttributeError, HTTPException, TypeError):
        raise _unauthorized() from None
    if not isinstance(username, str) or not _compare_text(username, settings.username):
        raise _unauthorized()
    return username


def create_platform_user_dependency(
    settings: PlatformControlSettings,
    platform_secrets: _PlatformSecrets,
) -> Callable:
    def require_platform_user(
        credentials: HTTPBasicCredentials | None = Depends(_BASIC),
        token: str | None = Depends(_BEARER),
    ) -> str:
        if token is not None:
            return _token_user(token, "access", settings, platform_secrets)
        if credentials is not None and _verify_basic(credentials, settings, platform_secrets):
            return settings.username
        raise _unauthorized()

    return require_platform_user


def create_auth_router(
    settings: PlatformControlSettings,
    platform_secrets: _PlatformSecrets,
) -> APIRouter:
    router = APIRouter()

    @router.post("/token/login", response_model=AccessAndRefreshToken)
    def token_login(
        credentials: HTTPBasicCredentials | None = Depends(_BASIC),
    ) -> AccessAndRefreshToken:
        if credentials is None or not _verify_basic(credentials, settings, platform_secrets):
            raise _unauthorized()
        token_data = {"identity": {"u": settings.username}}
        return AccessAndRefreshToken(
            access_token=create_token(
                token_data,
                platform_secrets._jwt_secret,
                token_type="access",  # noqa: S106
            ),
            refresh_token=create_token(
                token_data,
                platform_secrets._jwt_secret,
                token_type="refresh",  # noqa: S106
            ),
        )

    @router.post("/token/refresh", response_model=AccessToken)
    def token_refresh(token: str | None = Depends(_BEARER)) -> AccessToken:
        if token is None:
            raise _unauthorized()
        username = _token_user(token, "refresh", settings, platform_secrets)
        return AccessToken(
            access_token=create_token(
                {"identity": {"u": username}},
                platform_secrets._jwt_secret,
                token_type="access",  # noqa: S106
            )
        )

    return router
