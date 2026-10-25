"""Assemblage des services à partir de la configuration (une seule fois par processus)."""

import uuid
from dataclasses import dataclass, field
from datetime import timedelta

import anthropic
import httpx
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from aiffel.actions.service import ActionService
from aiffel.channels.base import OutboundMessage
from aiffel.channels.dispatcher import Dispatcher
from aiffel.channels.handlers import Handlers, placeholder_assistant
from aiffel.channels.identity import IdentityService
from aiffel.channels.telegram.api import TelegramClient
from aiffel.connectors.lexicon.client import LexiconClient
from aiffel.db.models import ChannelIdentity
from aiffel.db.session import global_session, make_engine, make_sessionmaker
from aiffel.farm_context import FarmContextService
from aiffel.farm_llm import FarmLLMService
from aiffel.logging import configure_logging
from aiffel.orchestrator.agent import Orchestrator
from aiffel.orchestrator.llm import AnthropicProvider, Effort, LLMProvider
from aiffel.orchestrator.runtime import AgentRuntime
from aiffel.pairing import PairingResult, PairingService
from aiffel.providers.openrouter import OpenRouterProvider
from aiffel.settings import Settings, get_settings
from aiffel.tools.catalog import phase1_tools, propose_tools
from aiffel.tools.registry import ToolGateway
from aiffel.vault import Keyring, SecretValue
from aiffel.voice import VoiceService, WhisperTranscriber
from aiffel.worker import Worker


class ConfigurationError(RuntimeError):
    pass


def _require(value: SecretStr | None, name: str) -> str:
    if value is None or not value.get_secret_value():
        raise ConfigurationError(f"{name} est requis")
    return value.get_secret_value()


@dataclass
class Services:
    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    keyring: Keyring
    identities: IdentityService
    telegram: TelegramClient
    http: httpx.AsyncClient
    pairing: PairingService
    farms: FarmContextService
    lexicon: LexiconClient
    actions: ActionService
    orchestrator: Orchestrator | None
    voice: VoiceService | None = None
    llm: FarmLLMService | None = None
    _dispatcher: Dispatcher | None = field(default=None, init=False)

    def handlers(self) -> Handlers:
        assistant = self.orchestrator.run_turn if self.orchestrator else placeholder_assistant
        return Handlers(
            self.pairing,
            self.farms,
            self.settings.public_base_url,
            assistant,
            self.actions,
            self.voice,
            self.llm,
        )

    def dispatcher(self) -> Dispatcher:
        if self._dispatcher is None:
            handlers = self.handlers()
            self._dispatcher = Dispatcher(
                self.sessions, self.identities, handlers.unpaired, handlers.paired
            )
        return self._dispatcher

    def worker(self) -> Worker:
        return Worker(self.dispatcher(), self.telegram)

    async def notify(self, identity_id: uuid.UUID, text: str) -> None:
        """Message proactif sur Telegram (meilleur effort)."""
        async with global_session(self.sessions) as session:
            identity = await session.get(ChannelIdentity, identity_id)
            chat_id = self.identities.external_id(identity) if identity else None
        if chat_id:
            await self.telegram.send(chat_id, OutboundMessage(text=text))

    async def notify_paired(self, result: PairingResult) -> None:
        """Confirme le raccordement sur Telegram (meilleur effort)."""
        await self.notify(
            result.identity_id,
            f"Connecté à la ferme {result.farm_name} ✅\n\nEssayez /ferme ou /aide.",
        )

    async def aclose(self) -> None:
        if self.llm is not None:
            await self.llm.aclose()
            await self.llm.http.aclose()
        await self.telegram.aclose()
        await self.http.aclose()
        await self.engine.dispose()


def make_provider(
    settings: Settings, model: str | None = None, effort: Effort | None = None
) -> LLMProvider | None:
    """Fournisseur de modèle choisi par la configuration ; ``None`` sans clé d'API."""
    model = model or settings.llm_model
    effort = effort or settings.llm_effort
    if settings.llm_provider == "openrouter":
        if settings.openrouter_api_key is None:
            return None
        return OpenRouterProvider(
            SecretValue(settings.openrouter_api_key.get_secret_value()),
            model,
            effort=effort,
            max_tokens=settings.llm_max_tokens,
        )
    if settings.anthropic_api_key is None:
        return None
    return AnthropicProvider(
        anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value()),
        model,
        effort=effort,
        max_tokens=settings.llm_max_tokens,
    )


def build_services(settings: Settings | None = None) -> Services:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    database_url = _require(settings.database_url, "AIFFEL_DATABASE_URL")
    master_key = _require(settings.master_key, "AIFFEL_MASTER_KEY")
    hash_salt = _require(settings.hash_salt, "AIFFEL_HASH_SALT")
    bot_token = _require(settings.telegram_bot_token, "AIFFEL_TELEGRAM_BOT_TOKEN")

    engine = make_engine(database_url)
    sessions = make_sessionmaker(engine)
    keyring = Keyring.parse(master_key)
    # Pas de redirection suivie : une instance Ekylibre ne doit pas renvoyer ailleurs (SSRF).
    http = httpx.AsyncClient(timeout=20.0, follow_redirects=False)
    farms = FarmContextService(sessions, keyring, http)
    lexicon = LexiconClient(settings.lexicon_mcp_url)
    actions = ActionService(sessions)
    # Client propre aux modèles d'IA : réponses longues (outils, raisonnement).
    llm = FarmLLMService(sessions, keyring, settings, httpx.AsyncClient(timeout=120.0))
    # L'agent tourne même sans clé d'Aiffel : une ferme peut apporter la sienne (/modele).
    orchestrator = Orchestrator(
        sessions,
        farms,
        make_provider(settings),
        ToolGateway([*phase1_tools(), *propose_tools()]),
        AgentRuntime.load(),
        lexicon=lexicon,
        max_iterations=settings.agent_max_iterations,
        model_label=f"{settings.llm_provider}:{settings.llm_model}",
        actions=actions,
        llm=llm,
    )
    telegram = TelegramClient(SecretValue(bot_token))
    voice = (
        VoiceService(
            sessions,
            telegram.download,
            WhisperTranscriber(
                settings.whisper_model,
                settings.whisper_device,
                settings.whisper_compute_type,
                settings.whisper_language,
                settings.whisper_cache_dir,
            ),
            max_seconds=settings.voice_max_seconds,
        )
        if settings.stt_enabled
        else None
    )
    return Services(
        settings=settings,
        engine=engine,
        sessions=sessions,
        keyring=keyring,
        identities=IdentityService(SecretValue(hash_salt), keyring),
        telegram=telegram,
        http=http,
        pairing=PairingService(
            sessions, keyring, http, timedelta(minutes=settings.pairing_code_ttl_minutes)
        ),
        farms=farms,
        lexicon=lexicon,
        actions=actions,
        orchestrator=orchestrator,
        voice=voice,
        llm=llm,
    )


def is_fully_configured(settings: Settings) -> bool:
    return all(
        value is not None and value.get_secret_value()
        for value in (
            settings.database_url,
            settings.master_key,
            settings.hash_salt,
            settings.telegram_bot_token,
        )
    )
