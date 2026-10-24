"""Assemblage des services à partir de la configuration (une seule fois par processus)."""

from dataclasses import dataclass, field
from datetime import timedelta

import httpx
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from aiffel.channels.base import OutboundMessage
from aiffel.channels.dispatcher import Dispatcher
from aiffel.channels.handlers import Handlers
from aiffel.channels.identity import IdentityService
from aiffel.channels.telegram.api import TelegramClient
from aiffel.db.models import ChannelIdentity
from aiffel.db.session import global_session, make_engine, make_sessionmaker
from aiffel.farm_context import FarmContextService
from aiffel.logging import configure_logging
from aiffel.pairing import PairingResult, PairingService
from aiffel.settings import Settings, get_settings
from aiffel.vault import Keyring, SecretValue
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
    _dispatcher: Dispatcher | None = field(default=None, init=False)

    def handlers(self) -> Handlers:
        return Handlers(self.pairing, self.farms, self.settings.public_base_url)

    def dispatcher(self) -> Dispatcher:
        if self._dispatcher is None:
            handlers = self.handlers()
            self._dispatcher = Dispatcher(
                self.sessions, self.identities, handlers.unpaired, handlers.paired
            )
        return self._dispatcher

    def worker(self) -> Worker:
        return Worker(self.dispatcher(), self.telegram)

    async def notify_paired(self, result: PairingResult) -> None:
        """Confirme le raccordement sur Telegram (meilleur effort)."""
        async with global_session(self.sessions) as session:
            identity = await session.get(ChannelIdentity, result.identity_id)
            chat_id = self.identities.external_id(identity) if identity else None
        if chat_id:
            await self.telegram.send(
                chat_id,
                OutboundMessage(
                    text=f"Connecté à la ferme {result.farm_name} ✅\n\nEssayez /ferme ou /aide."
                ),
            )

    async def aclose(self) -> None:
        await self.telegram.aclose()
        await self.http.aclose()
        await self.engine.dispose()


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
    return Services(
        settings=settings,
        engine=engine,
        sessions=sessions,
        keyring=keyring,
        identities=IdentityService(SecretValue(hash_salt), keyring),
        telegram=TelegramClient(SecretValue(bot_token)),
        http=http,
        pairing=PairingService(
            sessions, keyring, http, timedelta(minutes=settings.pairing_code_ttl_minutes)
        ),
        farms=FarmContextService(sessions, keyring, http),
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
