"""Modèle d'IA par ferme : choix, clé propre dans le coffre, isolation, /modele et page /cle."""

import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiffel.app.web_routes import llm_key_router
from aiffel.channels.base import InboundKind, InboundMessage
from aiffel.channels.dispatcher import Caller
from aiffel.channels.handlers import OWNER_ONLY, Handlers
from aiffel.channels.identity import IdentityService
from aiffel.connectors.ekylibre.auth import EkylibreInstance
from aiffel.db.models import AuditLog, Secret, UsageMetric
from aiffel.db.session import farm_session, global_session
from aiffel.farm_context import FarmContextService, FarmRef
from aiffel.farm_llm import FarmLLMService, LLMKeyError
from aiffel.orchestrator.agent import NO_MODEL, Orchestrator
from aiffel.orchestrator.llm import AnthropicProvider, LLMResult, Usage
from aiffel.orchestrator.runtime import AgentRuntime
from aiffel.pairing import PURPOSE_LLM_KEY, PairingError, PairingResult, PairingService
from aiffel.providers.catalog import ProviderName
from aiffel.providers.openrouter import OpenRouterProvider
from aiffel.settings import Settings
from aiffel.tools.catalog import phase1_tools
from aiffel.tools.registry import ToolGateway
from aiffel.vault import Keyring, SecretValue
from tests.conftest import create_farm
from tests.fakes import FakeEkylibre

Sessions = async_sessionmaker[AsyncSession]
DEMO = EkylibreInstance("https://demo.ekylibre-dev.com", "demo")
FARM_KEY = "sk-or-v1-CLE-DE-LA-FERME-0123456789"


class FakeVerifier:
    def __init__(self) -> None:
        self.accept = True
        self.calls: list[ProviderName] = []

    async def __call__(self, provider: ProviderName, _key: SecretValue) -> bool:
        self.calls.append(provider)
        return self.accept


@dataclass
class Env:
    owner: PairingResult
    worker: PairingResult
    pairing: PairingService
    farms: FarmContextService
    verifier: FakeVerifier
    sessions: Sessions

    def llm(self, **settings: Any) -> FarmLLMService:
        keys = {
            "anthropic_api_key": settings.pop("anthropic", "sk-ant-AIFFEL"),
            "openrouter_api_key": settings.pop("openrouter", None),
        }
        config = Settings().model_copy(
            update={k: SecretStr(v) if v else None for k, v in keys.items()} | settings
        )
        return FarmLLMService(
            self.sessions, self.farms.keyring, config, httpx.AsyncClient(), self.verifier
        )

    def handlers(self, llm: FarmLLMService) -> Handlers:
        return Handlers(self.pairing, self.farms, "https://app.aiffel.org", llm=llm)


@pytest.fixture
async def env(
    app_sessions: Sessions, owner_sessions: Sessions, keyring: Keyring
) -> AsyncIterator[Env]:
    async with owner_sessions() as session, session.begin():
        await session.execute(
            text(
                "TRUNCATE aiffel.pairing_codes, aiffel.channel_identities, aiffel.connections, "
                "aiffel.memberships, aiffel.source_map, aiffel.secrets, aiffel.usage_metrics, "
                "aiffel.messages, aiffel.conversations CASCADE"
            )
        )
    fake = FakeEkylibre()
    async with fake.client() as http:
        pairing = PairingService(app_sessions, keyring, http, timedelta(minutes=15))
        results = []
        for chat, email, password in (
            ("5001", "chef@demo.fr", "motdepasse-chef"),
            ("5002", "sal@demo.fr", "motdepasse-sal"),
        ):
            async with global_session(app_sessions) as session:
                identity = await IdentityService(SecretValue("sel"), keyring).resolve(
                    session, "telegram", chat
                )
            code = await pairing.create_code(identity.id)
            results.append(await pairing.complete(code, DEMO, email, SecretValue(password)))
        yield Env(
            results[0],
            results[1],
            pairing,
            FarmContextService(app_sessions, keyring, http),
            FakeVerifier(),
            app_sessions,
        )


def _caller(result: PairingResult) -> Caller:
    return Caller(identity_id=result.identity_id, user_id=result.user_id)


def _farm(result: PairingResult) -> FarmRef:
    return FarmRef(result.farm_id, result.farm_name, result.role, result.role == "owner")


def _msg(**fields: Any) -> InboundMessage:
    return InboundMessage(
        channel="telegram", external_user_id="5001", external_chat_id="5001", **fields
    )


async def test_default_then_chosen_model_with_the_farm_key(env: Env) -> None:
    llm = env.llm()
    farm_id, user_id = env.owner.farm_id, env.owner.user_id
    resolved = await llm.resolve(farm_id)
    assert resolved is not None and isinstance(resolved.provider, AnthropicProvider)
    assert resolved.label == "anthropic:claude-opus-5-5" and not resolved.own_key

    # DeepSeek sans clé OpenRouter (ni celle d'Aiffel, ni celle de la ferme) : modèle par défaut.
    await llm.choose(farm_id, user_id, "deepseek")
    assert (await llm.resolve(farm_id)).label == "anthropic:claude-opus-5-5"  # type: ignore[union-attr]

    await llm.save_key(farm_id, user_id, "openrouter", SecretValue(FARM_KEY))
    resolved = await llm.resolve(farm_id)
    assert resolved is not None and resolved.own_key
    assert isinstance(resolved.provider, OpenRouterProvider)
    assert resolved.label == "openrouter:deepseek/deepseek-v4.1-flash"
    assert resolved.provider._key.reveal() == FARM_KEY

    async with farm_session(env.sessions, farm_id) as session:
        secrets = list(await session.scalars(select(Secret)))
        audit = [row.action for row in await session.scalars(select(AuditLog))]
    assert all(FARM_KEY.encode() not in s.ciphertext for s in secrets)
    assert {"llm.model_chosen", "llm.key_saved"} <= set(audit)

    assert await llm.remove_key(farm_id, user_id, "openrouter")
    assert (await llm.resolve(farm_id)).label == "anthropic:claude-opus-5-5"  # type: ignore[union-attr]


async def test_refused_or_malformed_key_is_not_stored(env: Env) -> None:
    llm = env.llm()
    env.verifier.accept = False
    with pytest.raises(LLMKeyError, match="refuse"):
        await llm.save_key(
            env.owner.farm_id, env.owner.user_id, "openrouter", SecretValue(FARM_KEY)
        )
    with pytest.raises(LLMKeyError, match="format"):
        await llm.save_key(env.owner.farm_id, env.owner.user_id, "openrouter", SecretValue("abc"))
    assert env.verifier.calls == ["openrouter"]  # le format est vérifié avant tout appel
    assert (await llm.current(env.owner.farm_id)).own_keys == frozenset()


async def test_a_farm_key_never_serves_another_farm(env: Env, keyring: Keyring) -> None:
    llm = env.llm(anthropic=None)
    await llm.save_key(env.owner.farm_id, env.owner.user_id, "openrouter", SecretValue(FARM_KEY))
    await llm.choose(env.owner.farm_id, env.owner.user_id, "deepseek")
    assert await llm.resolve(env.owner.farm_id) is not None

    other = await create_farm(env.sessions, keyring, "Autre ferme")
    await llm.choose(other, env.owner.user_id, "deepseek")
    assert await llm.resolve(other) is None  # ni clé de ferme, ni clé d'Aiffel


async def test_no_usable_model_is_explained_and_cost_is_recorded(env: Env) -> None:
    class Provider:
        async def complete(self, *_args: Any) -> LLMResult:
            return LLMResult(
                [{"type": "text", "text": "ok"}],
                "ok",
                [],
                "end_turn",
                "m",
                Usage(10, 5, 0, 0, 0.0021),
            )

    llm = env.llm(anthropic=None)
    orchestrator = Orchestrator(
        env.sessions, env.farms, None, ToolGateway(phase1_tools()), AgentRuntime.load(), llm=llm
    )
    [reply] = await orchestrator.run_turn(
        _caller(env.owner), _farm(env.owner), _msg(kind=InboundKind.TEXT, text="?")
    )
    assert reply.text == NO_MODEL

    await llm.save_key(env.owner.farm_id, env.owner.user_id, "openrouter", SecretValue(FARM_KEY))
    await llm.choose(env.owner.farm_id, env.owner.user_id, "deepseek")
    llm._build = lambda *_args: Provider()  # type: ignore[method-assign,assignment,return-value]
    for _ in range(2):
        await orchestrator.run_turn(
            _caller(env.owner), _farm(env.owner), _msg(kind=InboundKind.TEXT, text="?")
        )
    async with farm_session(env.sessions, env.owner.farm_id) as session:
        metric = await session.scalar(select(UsageMetric))
    assert metric is not None and metric.model == "openrouter:deepseek/deepseek-v4.1-flash"
    assert float(metric.cost_usd) == pytest.approx(0.0042)


async def test_model_menu_and_buttons_are_for_the_owner_only(env: Env) -> None:
    llm = env.llm()
    handlers = env.handlers(llm)
    [menu] = await handlers.paired(
        _caller(env.owner), _msg(kind=InboundKind.COMMAND, command="modele")
    )
    assert "modèle par défaut" in menu.text and "DeepSeek V4.1 Flash" in menu.text
    data = [b.data for row in menu.buttons for b in row]
    assert "llm:m:deepseek" in data and "llm:k" in data

    [reply] = await handlers.paired(
        _caller(env.worker), _msg(kind=InboundKind.COMMAND, command="modele")
    )
    assert OWNER_ONLY in reply.text and not reply.buttons
    [reply] = await handlers.paired(
        _caller(env.worker), _msg(kind=InboundKind.CALLBACK, callback_data="llm:m:haiku")
    )
    assert reply.text == OWNER_ONLY
    assert (await llm.current(env.owner.farm_id)).choice is None

    [done] = await handlers.paired(
        _caller(env.owner), _msg(kind=InboundKind.CALLBACK, callback_data="llm:m:haiku")
    )
    assert "Claude Haiku 5.5" in done.text
    assert (await llm.current(env.owner.farm_id)).choice.key == "haiku"  # type: ignore[union-attr]

    # Modèle sans clé disponible : choix noté, et lien pour saisir la clé.
    noted, link = await handlers.paired(
        _caller(env.owner), _msg(kind=InboundKind.CALLBACK, callback_data="llm:m:mistral")
    )
    assert "il faut une clé OpenRouter" in noted.text
    assert link.buttons and link.buttons[0][0].url.startswith("https://app.aiffel.org/cle?c=")  # type: ignore[union-attr]


async def _key_code(env: Env, llm: FarmLLMService) -> str:
    [link] = await env.handlers(llm).paired(
        _caller(env.owner), _msg(kind=InboundKind.CALLBACK, callback_data="llm:k")
    )
    url = link.buttons[0][0].url or ""
    return url.split("c=", 1)[1]


async def test_key_codes_and_pairing_codes_do_not_mix(env: Env) -> None:
    code = await _key_code(env, env.llm())
    with pytest.raises(PairingError):
        await env.pairing.check_code(code)  # pas une page de raccordement
    assert await env.pairing.check_code(code, PURPOSE_LLM_KEY) == env.owner.identity_id


@pytest.fixture
async def web(env: Env) -> AsyncIterator[tuple[AsyncClient, FarmLLMService, list[str]]]:
    llm = env.llm()
    notified: list[str] = []

    async def notify(_identity: uuid.UUID, text_: str) -> None:
        notified.append(text_)

    app = FastAPI()
    app.include_router(llm_key_router(env.pairing, env.farms, llm, notify, secure_cookies=False))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, llm, notified


def _csrf(page: httpx.Response) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', page.text)
    assert match
    return match.group(1)


async def test_web_key_page_saves_the_key_once(
    env: Env,
    web: tuple[AsyncClient, FarmLLMService, list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    client, llm, notified = web
    code = await _key_code(env, llm)
    page = await client.get("/cle", params={"c": code})
    assert page.status_code == 200 and "Demo" in page.text
    assert page.headers["referrer-policy"] == "no-referrer"

    env.verifier.accept = False
    form = {"c": code, "provider": "openrouter", "key": FARM_KEY}
    refused = await client.post("/cle", data={**form, "csrf": _csrf(page)})
    assert refused.status_code == 422 and "refuse" in refused.text
    assert FARM_KEY not in refused.text

    env.verifier.accept = True
    done = await client.post("/cle", data={**form, "csrf": _csrf(refused)})
    assert done.status_code == 200 and "Clé enregistrée" in done.text
    assert notified and "OpenRouter" in notified[0]
    assert (await llm.current(env.owner.farm_id)).own_keys == {"openrouter"}
    assert FARM_KEY not in capsys.readouterr().out
    assert (await client.get("/cle", params={"c": code})).status_code == 410


async def test_web_key_page_refuses_workers_and_pairing_codes(
    env: Env, web: tuple[AsyncClient, FarmLLMService, list[str]]
) -> None:
    client, _llm, _notified = web
    worker_code = await env.pairing.create_code(env.worker.identity_id, PURPOSE_LLM_KEY)
    page = await client.get("/cle", params={"c": worker_code})
    assert page.status_code == 410 and "exploitant" in page.text
    pairing_code = await env.pairing.create_code(env.owner.identity_id)
    assert (await client.get("/cle", params={"c": pairing_code})).status_code == 410
