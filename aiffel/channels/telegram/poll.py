"""Mode *polling* pour le développement local : pas de DNS ni de tunnel.

``uv run python -m aiffel.channels.telegram.poll`` récupère les messages par ``getUpdates``,
les met en file comme le webhook, et les traite dans le même processus. Refusé en production.
Si un webhook est actif sur ce bot, Telegram renvoie une erreur 409 : on ne le supprime jamais
automatiquement, car ce pourrait être le bot de production.
"""

import asyncio

import structlog

from aiffel import queue
from aiffel.app.container import build_services
from aiffel.channels.telegram.api import TelegramError
from aiffel.channels.telegram.parse import CHANNEL
from aiffel.db.session import global_session

log = structlog.get_logger()
RETRY_DELAY_S = 5.0


async def _main() -> None:
    services = build_services()
    if services.settings.env == "production":
        raise SystemExit("Le mode polling est réservé au développement.")
    worker = services.worker()
    offset: int | None = None
    log.info("polling.started")
    try:
        while True:
            try:
                updates = await services.telegram.get_updates(offset)
            except TelegramError as exc:
                if "409" in str(exc):  # webhook actif : on ne le supprime jamais (voir plus haut)
                    raise
                # Erreur passagère de Telegram (502, coupure réseau) : on réessaie.
                log.warning("polling.retry", error=str(exc))
                await asyncio.sleep(RETRY_DELAY_S)
                continue
            for update in updates:
                offset = int(update["update_id"]) + 1
                async with global_session(services.sessions) as session:
                    await queue.enqueue(session, CHANNEL, int(update["update_id"]), update)
            while await worker.run_once():
                pass
    finally:
        await services.aclose()


if __name__ == "__main__":
    asyncio.run(_main())
