"""Application FastAPI : point d'entrée HTTP d'Aiffel (webhook Telegram, puis mini web avec A3)."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from aiffel import __version__
from aiffel.app.telegram_routes import telegram_router
from aiffel.db.session import make_engine, make_sessionmaker
from aiffel.logging import configure_logging
from aiffel.settings import Settings, get_settings
from aiffel.vault import SecretValue


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="Aiffel", version=__version__, docs_url=None, redoc_url=None)
    engine = (
        make_engine(settings.database_url.get_secret_value()) if settings.database_url else None
    )

    if engine is not None and settings.telegram_webhook_secret is not None:
        app.include_router(
            telegram_router(
                make_sessionmaker(engine),
                SecretValue(settings.telegram_webhook_secret.get_secret_value()),
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
