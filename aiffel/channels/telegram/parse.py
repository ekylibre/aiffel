"""Conversion d'une mise à jour de l'API Bot Telegram en :class:`InboundMessage`."""

from typing import Any

from aiffel.channels.base import InboundKind, InboundMessage

CHANNEL = "telegram"


def parse_update(update: dict[str, Any]) -> InboundMessage | None:
    """Renvoie ``None`` pour les mises à jour ignorées (messages de groupe, édition, etc.)."""
    if callback := update.get("callback_query"):
        message = callback.get("message") or {}
        sender = callback.get("from") or {}
        chat = message.get("chat") or {}
        if chat.get("type") not in (None, "private"):
            return None
        return InboundMessage(
            channel=CHANNEL,
            external_user_id=str(sender["id"]),
            external_chat_id=str(chat.get("id", sender["id"])),
            kind=InboundKind.CALLBACK,
            callback_id=str(callback["id"]),
            callback_data=callback.get("data"),
            language_code=sender.get("language_code"),
            first_name=sender.get("first_name"),
        )

    message = update.get("message")
    if not message:
        return None
    chat, sender = message.get("chat") or {}, message.get("from") or {}
    # Phase 1 : conversations privées uniquement (pas de groupes ni de canaux).
    if chat.get("type") != "private" or "id" not in sender:
        return None

    base: dict[str, Any] = {
        "channel": CHANNEL,
        "external_user_id": str(sender["id"]),
        "external_chat_id": str(chat["id"]),
        "language_code": sender.get("language_code"),
        "first_name": sender.get("first_name"),
    }

    if text := message.get("text"):
        if text.startswith("/"):
            head, _, args = text.partition(" ")
            command = head[1:].split("@", 1)[0].lower()
            return InboundMessage(
                **base, kind=InboundKind.COMMAND, command=command, command_args=args or None
            )
        return InboundMessage(**base, kind=InboundKind.TEXT, text=text)

    if voice := message.get("voice") or message.get("audio"):
        return InboundMessage(
            **base,
            kind=InboundKind.VOICE,
            file_id=voice["file_id"],
            duration_s=voice.get("duration"),
        )

    if photos := message.get("photo"):
        largest = max(photos, key=lambda p: p.get("file_size", 0) or p.get("width", 0))
        return InboundMessage(
            **base, kind=InboundKind.PHOTO, file_id=largest["file_id"], text=message.get("caption")
        )

    if location := message.get("location"):
        return InboundMessage(
            **base,
            kind=InboundKind.LOCATION,
            latitude=location["latitude"],
            longitude=location["longitude"],
        )

    return InboundMessage(**base, kind=InboundKind.UNSUPPORTED)
