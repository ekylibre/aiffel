"""Client Lyvre : adresse contrôlée, lecture seule, erreurs, clé jamais journalisée."""

import httpx
import pytest
from structlog.testing import capture_logs

from aiffel.connectors.lyvre.client import (
    READ_TOOLS,
    LyvreAuthError,
    LyvreClient,
    LyvreError,
    parse_base_url,
)
from aiffel.vault import SecretValue
from tests.fakes import LYVRE_KEY, FakeLyvre

ALLOWED = ("lyvre.org",)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://app.lyvre.org", "https://app.lyvre.org"),
        ("app.lyvre.org/", "https://app.lyvre.org"),
        ("https://lyvre.org", "https://lyvre.org"),
        ("https://APP.Lyvre.org/compta", "https://app.lyvre.org/compta"),
    ],
)
def test_accepted_addresses(raw: str, expected: str) -> None:
    assert parse_base_url(raw, ALLOWED) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "http://app.lyvre.org",
        "https://evil.example.org",
        "https://lyvre.org.evil.example",
        "https://app.lyvre.org@evil.example",
        "https://app.lyvre.org:8443",
        "https://app.lyvre.org?x=1",
        "https://notlyvre.org",
        "",
    ],
)
def test_refused_addresses(raw: str) -> None:
    with pytest.raises(LyvreError, match="Adresse Lyvre non reconnue"):
        parse_base_url(raw, ALLOWED)


def _client(fake: FakeLyvre, key: str = LYVRE_KEY) -> LyvreClient:
    return LyvreClient(fake.client(), "https://app.lyvre.org", SecretValue(key))


async def test_account_reports_domains_and_structure() -> None:
    fake = FakeLyvre(writes={"purchases"})
    account = await _client(fake).account()
    assert account.structure == "GAEC des Essais"
    assert account.reads == {"accounting", "purchases", "bank"}
    assert account.writes == {"purchases"}
    assert all(auth == f"Bearer {LYVRE_KEY}" for auth in fake.authorizations)


async def test_write_tools_are_refused_before_the_network() -> None:
    fake = FakeLyvre(writes={"purchases", "parties", "accounting"})
    client = _client(fake)
    for name in FakeLyvre.WRITE_TOOLS:
        assert name not in READ_TOOLS
        with pytest.raises(LyvreError, match="lit seulement"):
            await client.call(name, {"total_ttc": 10})
    assert fake.requests == []


async def test_errors_are_translated() -> None:
    fake = FakeLyvre(reads={"accounting"})
    client = _client(fake)
    with pytest.raises(LyvreError, match="ne lit pas ce domaine"):
        await client.call("list_purchases", {})
    fake.rate_limited = True
    with pytest.raises(LyvreError, match="12 s"):
        await client.call("get_balances", {"prefixes": "60"})
    fake.rate_limited, fake.revoked = False, True
    with pytest.raises(LyvreAuthError, match="/lyvre"):
        await client.call("get_balances", {"prefixes": "60"})


async def test_timeouts_and_outages() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lent", request=request)

    def broken(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="<html>erreur</html>")

    slow = LyvreClient(
        httpx.AsyncClient(transport=httpx.MockTransport(timeout)),
        "https://app.lyvre.org",
        SecretValue(LYVRE_KEY),
    )
    with pytest.raises(LyvreError, match="pas répondu à temps"):
        await slow.call("get_context", {})
    down = LyvreClient(
        httpx.AsyncClient(transport=httpx.MockTransport(broken)),
        "https://app.lyvre.org",
        SecretValue(LYVRE_KEY),
    )
    with pytest.raises(LyvreError, match=r"refusé la demande \(500\)"):
        await down.call("get_context", {})


async def test_the_key_never_reaches_logs_or_messages(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeLyvre()
    client = _client(fake)
    with capture_logs() as logs:
        await client.call("list_purchases", {"supplier": "Coop"})
        fake.revoked = True
        with pytest.raises(LyvreAuthError) as refused:
            await client.call("list_purchases", {"supplier": "Coop"})
    # Selon l'ordre des tests, structlog écrit déjà sur la sortie (loggers en cache) ou est capturé.
    output = capsys.readouterr()
    dumped = repr(logs) + output.out + output.err + str(refused.value) + repr(client.__dict__)
    assert LYVRE_KEY not in dumped and "Coop" not in repr(logs) + output.out + output.err
    assert {entry["event"] for entry in logs} <= {"lyvre.call"}
