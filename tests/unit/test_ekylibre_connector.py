import httpx
import pytest

from aiffel.app.web_routes import RateLimiter
from aiffel.connectors.ekylibre.auth import (
    EkylibreAuthError,
    EkylibreCredentials,
    EkylibreInstance,
    SimpleTokenAuth,
)
from aiffel.connectors.ekylibre.client import EkylibreClient, EkylibreUnauthorized
from aiffel.connectors.ekylibre.instances import InvalidInstance, parse_instance
from aiffel.vault import SecretValue
from tests.fakes import FakeEkylibre

DOMAINS = ("ekylibre-dev.com", "ekylibre.com")
DEMO = EkylibreInstance("https://demo.ekylibre-dev.com", "demo")


@pytest.mark.parametrize(
    ("raw", "tenant"),
    [
        ("demo.ekylibre-dev.com", "demo"),
        ("  https://Demo.ekylibre-dev.com/ ", "demo"),
        ("https://ferme-du-pre.ekylibre.com/backend/dashboards", "ferme-du-pre"),
    ],
)
def test_parse_instance_accepts_farm_addresses(raw: str, tenant: str) -> None:
    instance = parse_instance(raw, DOMAINS)
    assert instance.tenant == tenant
    assert instance.base_url == f"https://{tenant}.{instance.base_url.split('.', 1)[1]}"


@pytest.mark.parametrize(
    "raw",
    [
        "http://demo.ekylibre-dev.com",
        "demo.ekylibre-dev.com.evil.com",
        "evil-ekylibre-dev.com",
        "ekylibre-dev.com",
        "a.b.ekylibre-dev.com",
        "demo.ekylibre-dev.com:8443",
        "user:pass@demo.ekylibre-dev.com",
        "demo.ekylibre-dev.com/?x=1",
        "https://169.254.169.254",
        "https://127.0.0.1",
        "demo.ekylibre-dev.com/autre-chemin",
        "",
    ],
)
def test_parse_instance_rejects_other_hosts(raw: str) -> None:
    with pytest.raises(InvalidInstance):
        parse_instance(raw, DOMAINS)


def test_simple_token_headers() -> None:
    creds = EkylibreCredentials(SecretValue("a@b.fr"), SecretValue("TOK"))
    assert SimpleTokenAuth().headers(DEMO, creds) == {
        "Authorization": "simple-token a@b.fr TOK",
        "X-Tenant": "demo",
    }


async def test_login_success_and_wrong_password() -> None:
    fake = FakeEkylibre()
    async with fake.client() as http:
        creds = await SimpleTokenAuth().login(
            http, DEMO, "chef@demo.fr", SecretValue("motdepasse-chef")
        )
        assert creds.token == SecretValue("TOK-CHEF")
        with pytest.raises(EkylibreAuthError, match="incorrect"):
            await SimpleTokenAuth().login(http, DEMO, "chef@demo.fr", SecretValue("faux"))
    # Le mot de passe n'apparaît que dans le corps de la requête de jeton, jamais ailleurs.
    assert all("motdepasse" not in str(r.url) for r in fake.requests)


async def test_login_network_error_does_not_leak_password() -> None:
    def boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("password=motdepasse-chef")

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as http:
        with pytest.raises(EkylibreAuthError) as excinfo:
            await SimpleTokenAuth().login(http, DEMO, "c", SecretValue("motdepasse-chef"))
    assert "motdepasse" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None


async def test_client_strips_geometries_and_raises_on_revoked_token() -> None:
    fake = FakeEkylibre()
    creds = EkylibreCredentials(SecretValue("chef@demo.fr"), SecretValue("TOK-CHEF"))
    async with fake.client() as http:
        client = EkylibreClient(http, SimpleTokenAuth(), DEMO, creds)
        zones = await client.cultivable_zones()
        assert zones[0] == {"id": 1, "uuid": "u1", "work_number": "P12", "name": "Grands Champs"}
        assert await client.products("Plant") == [{"id": 11, "name": "Maïs Grands Champs 2026"}]
        fake.revoked.add("TOK-CHEF")
        with pytest.raises(EkylibreUnauthorized):
            await client.cultivable_zones()


def test_rate_limiter() -> None:
    limiter = RateLimiter(limit=2, window_s=60)
    assert limiter.allow("ip") and limiter.allow("ip")
    assert not limiter.allow("ip")
    assert limiter.allow("autre-ip")
