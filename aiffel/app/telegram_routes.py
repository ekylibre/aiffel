"""Webhook Telegram : vérifie le secret, met la mise à jour en file et répond tout de suite."""

import hmac
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiffel import queue
from aiffel.channels.telegram.parse import CHANNEL
from aiffel.db.session import global_session
from aiffel.vault import SecretValue

MAX_BODY_BYTES = 1_000_000


def telegram_router(
    sessions: async_sessionmaker[AsyncSession], webhook_secret: SecretValue
) -> APIRouter:
    router = APIRouter()

    @router.post("/telegram/webhook")
    async def webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        # Comparaison à temps constant ; même réponse que la route soit absente ou le secret faux.
        expected = webhook_secret.reveal().encode()
        given = (x_telegram_bot_api_secret_token or "").encode()
        if not hmac.compare_digest(given, expected):
            raise HTTPException(status_code=401)
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413)
        try:
            update = await request.json()
            update_id = int(update["update_id"])
        except (ValueError, KeyError, TypeError):
            raise HTTPException(status_code=400) from None
        async with global_session(sessions) as session:
            await queue.enqueue(session, CHANNEL, update_id, update)
        return {"ok": True}

    return router
