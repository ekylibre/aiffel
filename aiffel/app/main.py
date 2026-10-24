"""Application FastAPI : webhook Telegram et mini web de raccordement."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from aiffel import __version__
from aiffel.app.container import Services, build_services, is_fully_configured
from aiffel.app.telegram_routes import telegram_router
from aiffel.app.web_routes import web_router
from aiffel.db.session import make_engine, make_sessionmaker
from aiffel.logging import configure_logging
from aiffel.settings import Settings, get_settings
from aiffel.vault import SecretValue


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    services: Services | None = build_services(settings) if is_fully_configured(settings) else None
    engine: AsyncEngine | None = services.engine if services else None
    if engine is None and settings.database_url:
        engine = make_engine(settings.database_url.get_secret_value())

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if services is not None:
            await services.aclose()
        elif engine is not None:
            await engine.dispose()

    app = FastAPI(
        title="Aiffel", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan
    )

    if engine is not None and settings.telegram_webhook_secret is not None:
        app.include_router(
            telegram_router(
                services.sessions if services else make_sessionmaker(engine),
                SecretValue(settings.telegram_webhook_secret.get_secret_value()),
            )
        )
    if services is not None:
        app.include_router(
            web_router(
                services.pairing,
                settings.ekylibre_allowed_domains,
                services.notify_paired,
                secure_cookies=settings.public_base_url.startswith("https://"),
            )
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz", response_model=None)
    async def readyz() -> dict[str, str] | JSONResponse:
        if engine is None:
            return {"status": "ready", "database": "non configurée"}
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "unavailable", "database": "injoignable"}, 503)
        return {"status": "ready", "database": "ok"}

    return app


app = create_app()
