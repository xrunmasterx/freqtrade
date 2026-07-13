import uvicorn

from freqtrade.platform.database import create_platform_engine
from freqtrade.platform_control.api_runtime import SqlPlatformControlQueryRepository
from freqtrade.platform_control.app import create_platform_app
from freqtrade.platform_control.settings import PlatformControlSettings


def main() -> None:
    settings = PlatformControlSettings.from_env()
    engine = create_platform_engine(settings.database)
    repository = SqlPlatformControlQueryRepository(engine)
    app = create_platform_app(settings, repository)
    uvicorn.run(
        app,
        host=settings.listen_host,
        port=settings.listen_port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
