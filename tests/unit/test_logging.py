import logging

import httpx
import pytest

from aiffel.logging import configure_logging


@pytest.mark.parametrize("level", ["DEBUG", "INFO"])
async def test_http_client_urls_are_not_logged(
    level: str, caplog: pytest.LogCaptureFixture
) -> None:
    """L'URL de l'API Bot contient le jeton : httpx ne doit jamais la journaliser."""
    configure_logging(level)
    caplog.set_level(logging.DEBUG)  # le handler de test capte tout ce que les loggers émettent
    transport = httpx.MockTransport(lambda _r: httpx.Response(200, json={"ok": True}))
    async with httpx.AsyncClient(transport=transport) as client:
        await client.post("https://api.telegram.org/bot123:SECRET-TOKEN/getUpdates")
    assert "SECRET-TOKEN" not in caplog.text


def test_billing_fields_are_masked() -> None:
    from aiffel.logging import redact

    event = {"iban": "FR76", "siret": "808", "vat_number": "FR06", "legal_name": "G", "to": "a@b"}
    assert set(redact(None, "info", dict(event)).values()) == {"[masqué]"}
