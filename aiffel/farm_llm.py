"""Modèle d'IA de chaque ferme : choix parmi les modèles validés, clé propre facultative.

- Le choix est rangé dans ``farms.settings["llm"]`` ; sans choix, la ferme garde le modèle par
  défaut d'Aiffel.
- La clé d'une ferme est un secret du coffre (ADR-06), porté par une connexion ``llm:<fournisseur>``
  de la ferme. Elle n'est déchiffrée qu'ici, au moment de construire le fournisseur du tour.
- Sans clé propre, la clé d'Aiffel (``AIFFEL_*_API_KEY``) sert, si elle existe, dans la limite de
  ``AIFFEL_PLATFORM_TOKEN_QUOTA`` jetons par ferme (au total) ; sinon le modèle n'est pas
  utilisable et la ferme retombe sur le modèle par défaut.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import cast

import anthropic
import httpx
import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiffel.db import audit
from aiffel.db.models import Connection, Farm, UsageMetric
from aiffel.db.session import farm_session
from aiffel.orchestrator.llm import AnthropicProvider, LLMProvider
from aiffel.providers.catalog import PROVIDERS, ModelChoice, ProviderName, by_key
from aiffel.providers.keys import KeyCheckError, verify_key
from aiffel.providers.openrouter import OpenRouterProvider
from aiffel.settings import Settings
from aiffel.vault import Keyring, SecretStore, SecretValue

log = structlog.get_logger()

CONNECTOR_PREFIX = "llm:"
SECRET_API_KEY = "api_key"  # noqa: S105 (nom du type de secret)
ENDPOINTS: dict[ProviderName, str] = {
    "anthropic": "https://api.anthropic.com",
    "openrouter": "https://openrouter.ai/api/v1",
}
MIN_KEY_LENGTH = 20

KeyVerifier = Callable[[ProviderName, SecretValue], Awaitable[bool]]


class LLMKeyError(Exception):
    """Message destiné à l'utilisateur, sans la clé."""


class QuotaExhausted(Exception):
    """La ferme a consommé les jetons offerts sur la clé d'Aiffel et n'a pas de clé propre."""


@dataclass(frozen=True)
class FarmLLM:
    """Réglage d'une ferme ; ``choice`` est ``None`` pour le modèle par défaut d'Aiffel."""

    choice: ModelChoice | None
    own_keys: frozenset[ProviderName]


@dataclass(frozen=True)
class ResolvedLLM:
    provider: LLMProvider
    label: str  # « fournisseur:modèle », clé de usage_metrics
    own_key: bool
    # Jetons encore offerts sur la clé d'Aiffel (plafond du tour) ; None : pas de limite.
    token_budget: int | None = None


@dataclass
class FarmLLMService:
    sessions: async_sessionmaker[AsyncSession]
    keyring: Keyring
    settings: Settings
    http: httpx.AsyncClient
    verifier: KeyVerifier | None = None
    _anthropic: dict[str, anthropic.AsyncAnthropic] = field(default_factory=dict, repr=False)

    # --- Lecture -----------------------------------------------------------------------------

    def platform_key(self, provider: ProviderName) -> SecretValue | None:
        value = (
            self.settings.openrouter_api_key
            if provider == "openrouter"
            else self.settings.anthropic_api_key
        )
        return SecretValue(value.get_secret_value()) if value is not None else None

    async def current(self, farm_id: uuid.UUID) -> FarmLLM:
        async with farm_session(self.sessions, farm_id) as session:
            farm = await session.get(Farm, farm_id)
            choice = by_key(((farm.settings if farm else {}) or {}).get("llm", {}).get("model"))
            return FarmLLM(choice, await self._own_keys(session))

    def usable(self, choice: ModelChoice, setting: FarmLLM) -> bool:
        return choice.provider in setting.own_keys or self.platform_key(choice.provider) is not None

    async def resolve(self, farm_id: uuid.UUID) -> ResolvedLLM | None:
        """Fournisseur du tour pour cette ferme ; ``None`` si aucun modèle n'est utilisable."""
        async with farm_session(self.sessions, farm_id) as session:
            farm = await session.get(Farm, farm_id)
            wanted = by_key(((farm.settings if farm else {}) or {}).get("llm", {}).get("model"))
            candidates: list[tuple[ProviderName, str]] = []
            if wanted is not None:
                candidates.append((wanted.provider, wanted.model))
            candidates.append((self.settings.llm_provider, self.settings.llm_model))
            remaining = self._remaining(await self._platform_tokens(session))
            exhausted = False
            for provider, model in candidates:
                own = await self._key(session, farm_id, provider)
                key = own or self.platform_key(provider)
                if key is None:
                    continue
                if own is None and remaining is not None and remaining <= 0:
                    exhausted = True
                    continue
                if wanted is not None and (provider, model) != (wanted.provider, wanted.model):
                    log.warning("llm.choice_unusable", provider=wanted.provider)
                return ResolvedLLM(
                    self._build(provider, model, key),
                    f"{provider}:{model}",
                    own is not None,
                    None if own is not None else remaining,
                )
        if exhausted:
            raise QuotaExhausted
        return None

    async def allowance(self, farm_id: uuid.UUID) -> tuple[int, int] | None:
        """Jetons consommés sur la clé d'Aiffel et quota ; ``None`` sans limite."""
        if self.settings.platform_token_quota is None:
            return None
        async with farm_session(self.sessions, farm_id) as session:
            return await self._platform_tokens(session), self.settings.platform_token_quota

    def _remaining(self, used: int) -> int | None:
        quota = self.settings.platform_token_quota
        return None if quota is None else max(0, quota - used)

    @staticmethod
    async def _platform_tokens(session: AsyncSession) -> int:
        total = await session.scalar(
            select(func.coalesce(func.sum(UsageMetric.platform_tokens), 0))
        )
        return int(total or 0)

    # --- Écriture (exploitant) ---------------------------------------------------------------

    async def choose(self, farm_id: uuid.UUID, user_id: uuid.UUID, key: str) -> ModelChoice:
        choice = by_key(key)
        if choice is None:
            raise LLMKeyError("Ce modèle n'est pas proposé.")
        async with farm_session(self.sessions, farm_id) as session:
            farm = await session.get(Farm, farm_id, with_for_update=True)
            if farm is None:
                raise LLMKeyError("Ferme introuvable.")
            # Nouveau dictionnaire : la colonne JSONB n'est pas suivie en profondeur.
            farm.settings = {**(farm.settings or {}), "llm": {"model": choice.key}}
            await audit.append(
                session,
                farm_id,
                "llm.model_chosen",
                actor_user_id=user_id,
                target=f"{choice.provider}:{choice.model}",
            )
        return choice

    async def save_key(
        self, farm_id: uuid.UUID, user_id: uuid.UUID, provider: ProviderName, key: SecretValue
    ) -> None:
        """Vérifie la clé chez le fournisseur, puis la range chiffrée dans le coffre."""
        if provider not in PROVIDERS:
            raise LLMKeyError("Fournisseur inconnu.")
        if len(key.reveal().strip()) < MIN_KEY_LENGTH or any(c.isspace() for c in key.reveal()):
            raise LLMKeyError("Cette clé n'a pas le bon format. Copiez-la à nouveau.")
        verifier = self.verifier or (lambda p, k: verify_key(p, k, self.http))
        try:
            accepted = await verifier(provider, key)
        except KeyCheckError:
            raise LLMKeyError(
                f"{PROVIDERS[provider]} ne répond pas. Réessayez dans quelques minutes."
            ) from None
        if not accepted:
            raise LLMKeyError(f"{PROVIDERS[provider]} refuse cette clé.")
        async with farm_session(self.sessions, farm_id) as session:
            connection = await self._connection(session, provider)
            if connection is None:
                connection = Connection(
                    farm_id=farm_id,
                    user_id=user_id,
                    connector=f"{CONNECTOR_PREFIX}{provider}",
                    base_url=ENDPOINTS[provider],
                )
                session.add(connection)
                await session.flush()
            else:
                connection.status, connection.user_id = "active", user_id
            await SecretStore(self.keyring).put(
                session, farm_id, connection.id, SECRET_API_KEY, key
            )
            await audit.append(
                session,
                farm_id,
                "llm.key_saved",
                actor_user_id=user_id,
                target=f"{CONNECTOR_PREFIX}{provider}",
            )

    async def remove_key(
        self, farm_id: uuid.UUID, user_id: uuid.UUID, provider: ProviderName
    ) -> bool:
        async with farm_session(self.sessions, farm_id) as session:
            connection = await self._connection(session, provider)
            if connection is None or connection.status != "active":
                return False
            await SecretStore(self.keyring).revoke(session, connection.id, SECRET_API_KEY)
            connection.status = "revoked"
            await audit.append(
                session,
                farm_id,
                "llm.key_removed",
                actor_user_id=user_id,
                target=f"{CONNECTOR_PREFIX}{provider}",
            )
        return True

    # --- Interne -----------------------------------------------------------------------------

    @staticmethod
    async def _connection(session: AsyncSession, provider: ProviderName) -> Connection | None:
        return cast(
            Connection | None,
            await session.scalar(
                select(Connection).where(Connection.connector == f"{CONNECTOR_PREFIX}{provider}")
            ),
        )

    @staticmethod
    async def _own_keys(session: AsyncSession) -> frozenset[ProviderName]:
        connectors = await session.scalars(
            select(Connection.connector).where(
                Connection.connector.startswith(CONNECTOR_PREFIX), Connection.status == "active"
            )
        )
        return frozenset(
            cast(ProviderName, c.removeprefix(CONNECTOR_PREFIX))
            for c in connectors
            if c.removeprefix(CONNECTOR_PREFIX) in PROVIDERS
        )

    async def _key(
        self, session: AsyncSession, farm_id: uuid.UUID, provider: ProviderName
    ) -> SecretValue | None:
        connection = await self._connection(session, provider)
        if connection is None or connection.status != "active":
            return None
        return await SecretStore(self.keyring).get(session, farm_id, connection.id, SECRET_API_KEY)

    def _build(self, provider: ProviderName, model: str, key: SecretValue) -> LLMProvider:
        if provider == "openrouter":
            return OpenRouterProvider(
                key,
                model,
                effort=self.settings.llm_effort,
                max_tokens=self.settings.llm_max_tokens,
                http=self.http,
            )
        # Un client par clé, réutilisé d'un tour à l'autre (pool de connexions).
        digest = hashlib.sha256(key.reveal().encode()).hexdigest()
        client = self._anthropic.get(digest)
        if client is None:
            client = anthropic.AsyncAnthropic(api_key=key.reveal())
            self._anthropic[digest] = client
        return AnthropicProvider(
            client, model, effort=self.settings.llm_effort, max_tokens=self.settings.llm_max_tokens
        )

    async def aclose(self) -> None:
        for client in self._anthropic.values():
            await client.close()
        self._anthropic.clear()
