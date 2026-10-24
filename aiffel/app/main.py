"""Application FastAPI : point d'entrée HTTP d'Aiffel (webhook Telegram et mini web à venir)."""

from fastapi import FastAPI

from aiffel import __version__
from aiffel.logging import configure_logging
from aiffel.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="Aiffel", version=__version__, docs_url=None, redoc_url=None)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        # Les vérifications de la base et des connecteurs arrivent avec A1.
        return {"status": "ready"}

    return app


app = create_app()
