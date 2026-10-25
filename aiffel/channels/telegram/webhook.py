"""Enregistrement et retrait du webhook Telegram.

Lancé une fois après déploiement :

    python -m aiffel.channels.telegram.webhook set
    python -m aiffel.channels.telegram.webhook delete

Pointe sur ``AIFFEL_PUBLIC_BASE_URL/telegram/webhook`` et transmet le secret attendu par la route.
"""

from __future__ import annotations

import asyncio
import sys

from aiffel.channels.telegram.api import TelegramClient
from aiffel.settings import get_settings
from aiffel.vault import SecretValue

WEBHOOK_PATH = "/telegram/webhook"


async def _run(action: str) -> int:
    settings = get_settings()
    if settings.telegram_bot_token is None:
        print("AIFFEL_TELEGRAM_BOT_TOKEN est requis.", file=sys.stderr)
        return 2
    client = TelegramClient(SecretValue(settings.telegram_bot_token.get_secret_value()))
    try:
        if action == "set":
            if settings.telegram_webhook_secret is None:
                print("AIFFEL_TELEGRAM_WEBHOOK_SECRET est requis.", file=sys.stderr)
                return 2
            url = settings.public_base_url.rstrip("/") + WEBHOOK_PATH
            if not url.startswith("https://"):
                print(f"L'URL doit être en https (actuelle : {url}).", file=sys.stderr)
                return 3
            await client.set_webhook(
                url, SecretValue(settings.telegram_webhook_secret.get_secret_value())
            )
            print(f"Webhook enregistré sur {url}.")
            return 0
        if action == "delete":
            await client.delete_webhook()
            print("Webhook retiré.")
            return 0
        print(f"Action inconnue : {action} (attendu : set ou delete).", file=sys.stderr)
        return 1
    finally:
        await client.aclose()


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "set"
    sys.exit(asyncio.run(_run(action)))


if __name__ == "__main__":
    main()
