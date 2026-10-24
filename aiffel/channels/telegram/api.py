"""Client minimal de l'API Bot Telegram (https://core.telegram.org/bots/api), sur ``httpx``.

Le jeton du bot fait partie de l'URL : il ne doit jamais apparaître dans un log ou une exception.
"""

from typing import Any

import httpx

from aiffel.channels.base import OutboundMessage
from aiffel.vault import SecretValue

# Limite Telegram : 4096 caractères par message.
MAX_MESSAGE_LENGTH = 4096


class TelegramError(Exception):
    """Erreur d'appel à l'API Telegram ; le message ne contient pas le jeton."""


def split_text(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Découpe un texte trop long, de préférence en fin de paragraphe, de ligne ou sur un espace."""
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(" "))
        if cut < limit // 2:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest or not chunks:
        chunks.append(rest)
    return chunks


def _reply_markup(message: OutboundMessage) -> dict[str, Any] | None:
    if not message.buttons:
        return None
    rows = [
        [
            {"text": b.label, "url": b.url} if b.url else {"text": b.label, "callback_data": b.data}
            for b in row
        ]
        for row in message.buttons
    ]
    return {"inline_keyboard": rows}


class TelegramClient:
    def __init__(
        self,
        token: SecretValue,
        *,
        base_url: str = "https://api.telegram.org",
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=f"{base_url}/bot{token.reveal()}/", transport=transport, timeout=timeout
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def call(self, method: str, **params: Any) -> Any:
        payload = {k: v for k, v in params.items() if v is not None}
        try:
            response = await self._client.post(method, json=payload)
        except httpx.HTTPError as exc:
            # Ne pas propager l'exception d'origine : son message contient l'URL, donc le jeton.
            raise TelegramError(f"{method} : erreur réseau ({type(exc).__name__})") from None
        try:
            body = response.json()
        except ValueError:
            raise TelegramError(f"{method} : réponse illisible ({response.status_code})") from None
        if not body.get("ok"):
            description = str(body.get("description", ""))[:200]
            raise TelegramError(f"{method} : {response.status_code} {description}")
        return body.get("result")

    async def send(self, chat_id: str, message: OutboundMessage) -> None:
        chunks = split_text(message.text)
        for index, chunk in enumerate(chunks):
            last = index == len(chunks) - 1
            await self.call(
                "sendMessage",
                chat_id=chat_id,
                text=chunk,
                reply_markup=_reply_markup(message) if last else None,
                link_preview_options={"is_disabled": True},
            )

    async def typing(self, chat_id: str) -> None:
        await self.call("sendChatAction", chat_id=chat_id, action="typing")

    async def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        await self.call("answerCallbackQuery", callback_query_id=callback_id, text=text)

    async def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict[str, Any]]:
        result = await self.call(
            "getUpdates",
            offset=offset,
            timeout=timeout,
            allowed_updates=["message", "callback_query"],
        )
        return list(result or [])

    async def set_webhook(self, url: str, secret_token: SecretValue) -> None:
        await self.call(
            "setWebhook",
            url=url,
            secret_token=secret_token.reveal(),
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=False,
        )

    async def delete_webhook(self) -> None:
        await self.call("deleteWebhook")
