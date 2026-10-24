"""Application FastAPI : point d'entrée HTTP d'Aiffel (webhook Telegram et mini web à venir)."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from aiffel import __version__
from aiffel.db.session import make_engine
from aiffel.logging import configure_logging
from aiffel.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="Aiffel", version=__version__, docs_url=None, redoc_url=None)
    engine = (
        make_engine(settings.database_url.get_secret_value()) if settings.database_url else None
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
