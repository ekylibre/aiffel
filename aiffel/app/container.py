"""Assemblage des services à partir de la configuration (une seule fois par processus)."""

from dataclasses import dataclass, field

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from aiffel.channels.dispatcher import Dispatcher
from aiffel.channels.identity import IdentityService
from aiffel.channels.telegram.api import TelegramClient
from aiffel.db.session import make_engine, make_sessionmaker
from aiffel.logging import configure_logging
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
    _dispatcher: Dispatcher | None = field(default=None, init=False)

    def dispatcher(self) -> Dispatcher:
        if self._dispatcher is None:
            self._dispatcher = Dispatcher(self.sessions, self.identities)
        return self._dispatcher

    def worker(self) -> Worker:
        return Worker(self.dispatcher(), self.telegram)

    async def aclose(self) -> None:
        await self.telegram.aclose()
        await self.engine.dispose()


def build_services(settings: Settings | None = None) -> Services:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    database_url = _require(settings.database_url, "AIFFEL_DATABASE_URL")
    master_key = _require(settings.master_key, "AIFFEL_MASTER_KEY")
    hash_salt = _require(settings.hash_salt, "AIFFEL_HASH_SALT")
    bot_token = _require(settings.telegram_bot_token, "AIFFEL_TELEGRAM_BOT_TOKEN")

    engine = make_engine(database_url)
    keyring = Keyring.parse(master_key)
    return Services(
        settings=settings,
        engine=engine,
        sessions=make_sessionmaker(engine),
        keyring=keyring,
        identities=IdentityService(SecretValue(hash_salt), keyring),
        telegram=TelegramClient(SecretValue(bot_token)),
    )
