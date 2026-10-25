"""Logs JSON structurés, sans contenu utilisateur ni secret (NF-SEC, §9 de l'architecture)."""

import logging
from collections.abc import MutableMapping
from typing import Any

import structlog

# Clés jamais émises en clair, quel que soit le niveau de log.
REDACTED_KEYS = frozenset(
    {
        "password",
        "token",
        "authorization",
        "api_key",
        "secret",
        "master_key",
        "text",
        "content",
        "email",
        "chat_id",
        "iban",
        "siret",
        "vat_number",
        "legal_name",
        "to",
    }
)

# Bibliothèques qui journalisent les URL appelées au niveau INFO : l'API Bot Telegram porte le
# jeton du bot dans le chemin (``/bot<jeton>/…``), on ne garde donc que leurs avertissements.
NOISY_URL_LOGGERS = ("httpx", "httpcore", "mcp.client.streamable_http")


def redact(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in event_dict:
        if key.lower() in REDACTED_KEYS:
            event_dict[key] = "[masqué]"
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", level=level)
    for name in NOISY_URL_LOGGERS:
        logging.getLogger(name).setLevel(max(logging.WARNING, logging.getLevelName(level)))
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        cache_logger_on_first_use=True,
    )
