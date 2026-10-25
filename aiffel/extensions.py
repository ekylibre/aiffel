"""Extensions : modules installés à part qui complètent Aiffel sans que le cœur les connaisse.

Exemple : le paiement d'Aiffel en ligne, propre à l'hébergeur du service. Une extension est un
paquet Python qui déclare une fabrique dans le groupe d'entry points ``aiffel.extensions`` :

    [project.entry-points."aiffel.extensions"]
    paiement = "mon_paquet.extension:build"

``build(core: CoreServices) -> Extension | None`` reçoit ce que le cœur lui prête et renvoie ses
ajouts, ou ``None`` si elle n'est pas configurée. Les règles du cœur s'appliquent aux extensions :
leurs boutons sont traités hors modèle, leurs outils passent par la passerelle d'outils, et elles
n'accèdent aux données d'une ferme que par ``farm_session``.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Protocol

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiffel.channels.base import InboundMessage, OutboundMessage

if TYPE_CHECKING:
    from fastapi import APIRouter

    from aiffel.channels.dispatcher import Caller
    from aiffel.farm_context import FarmContextService, FarmRef
    from aiffel.pairing import FarmCreatedHook, PairingService
    from aiffel.settings import Settings
    from aiffel.tools.registry import ToolSpec
    from aiffel.vault import Keyring

log = structlog.get_logger()

ENTRY_POINT_GROUP = "aiffel.extensions"
# Migrations d'une extension : un module qui fournit ``upgrade(url_proprietaire)`` et ``TABLES``
# (ses tables, table de version comprise). ``upgrade`` est lancée après les migrations du cœur par
# ``aiffel.db.deploy`` et par ``aiffel.testing`` ; le cœur ignore ``TABLES`` dans ses comparaisons.
MIGRATIONS_GROUP = "aiffel.migrations"

CommandHandler = Callable[["Caller", "FarmRef", InboundMessage], Awaitable[list[OutboundMessage]]]
Notify = Callable[[uuid.UUID, OutboundMessage], Awaitable[None]]


@dataclass(frozen=True)
class CreditDecision:
    """Droit d'une ferme au crédit d'IA de la plateforme ; ``message`` est montré si refusé.

    ``token_budget`` : jetons encore permis sur la clé de la plateforme pendant ce tour (``None`` :
    pas de plafond) ; l'agent s'arrête quand il est atteint.
    """

    allowed: bool
    message: str | None = None
    token_budget: int | None = None


@dataclass(frozen=True)
class UsageEvent:
    """Consommation d'un tour d'agent ou d'une transcription, transmise aux extensions."""

    farm_id: uuid.UUID
    at: datetime
    model: str  # « fournisseur:modèle », ou « stt:<modèle> »
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    stt_seconds: int = 0
    # Vrai si la clé de la plateforme a servi (et non celle de la ferme).
    platform: bool = False


# Appelé dans la ``farm_session`` qui enregistre le tour ou la transcription.
UsageHook = Callable[[AsyncSession, UsageEvent], Awaitable[None]]
# Lignes ajoutées au menu /modele (crédit restant…), pour une ferme.
MenuLines = Callable[[uuid.UUID], Awaitable[list[str]]]


class CreditPolicy(Protocol):
    async def platform_credit(self, session: AsyncSession, farm_id: uuid.UUID) -> CreditDecision:
        """``session`` est la ``farm_session`` de la ferme."""
        ...


@dataclass(frozen=True)
class Periodic:
    """Tâche lancée régulièrement par le worker ; elle se protège seule des exécutions doubles."""

    name: str
    every: timedelta
    run: Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class CoreServices:
    """Ce que le cœur prête aux extensions."""

    settings: Settings
    sessions: async_sessionmaker[AsyncSession]
    keyring: Keyring
    pairing: PairingService
    farms: FarmContextService
    # Message proactif à une identité de canal (meilleur effort).
    notify: Notify


@dataclass
class Extension:
    name: str
    # Commandes Telegram (« /abonnement » → "abonnement"), réservées aux utilisateurs appairés.
    commands: dict[str, CommandHandler] = field(default_factory=dict)
    # Boutons : préfixe de ``callback_data`` avant « : » (« bill:inv » → "bill").
    callbacks: dict[str, CommandHandler] = field(default_factory=dict)
    help_lines: list[str] = field(default_factory=list)
    routers: list[APIRouter] = field(default_factory=list)
    tools: list[ToolSpec] = field(default_factory=list)
    credit_policy: CreditPolicy | None = None
    on_farm_created: FarmCreatedHook | None = None
    on_usage: UsageHook | None = None
    llm_menu_lines: MenuLines | None = None
    periodic: list[Periodic] = field(default_factory=list)
    aclose: Callable[[], Awaitable[None]] | None = None


Factory = Callable[[CoreServices], "Extension | None"]


def load_extensions(core: CoreServices, factories: list[Factory] | None = None) -> list[Extension]:
    """Extensions installées et configurées ; ``factories`` remplace la découverte (tests)."""
    if factories is None:
        factories = [entry.load() for entry in entry_points(group=ENTRY_POINT_GROUP)]
    loaded = [ext for ext in (factory(core) for factory in factories) if ext is not None]
    names = [ext.name for ext in loaded]
    if len(set(names)) != len(names):
        raise RuntimeError(f"Extensions en double : {names}")
    for kind in ("commands", "callbacks"):
        keys = [key for ext in loaded for key in getattr(ext, kind)]
        if len(set(keys)) != len(keys):
            raise RuntimeError(f"Deux extensions déclarent la même entrée ({kind}) : {keys}")
    policies = [ext.name for ext in loaded if ext.credit_policy is not None]
    if len(policies) > 1:
        raise RuntimeError(f"Une seule règle de crédit possible : {policies}")
    log.info("extensions.loaded", names=names)
    return loaded


def credit_policy(extensions: list[Extension]) -> CreditPolicy | None:
    return next((ext.credit_policy for ext in extensions if ext.credit_policy), None)


def on_farm_created(extensions: list[Extension]) -> FarmCreatedHook | None:
    hooks = [ext.on_farm_created for ext in extensions if ext.on_farm_created is not None]
    if not hooks:
        return None

    async def run(session: AsyncSession, farm_id: uuid.UUID) -> None:
        for hook in hooks:
            await hook(session, farm_id)

    return run


def on_usage(extensions: list[Extension]) -> UsageHook | None:
    hooks = [ext.on_usage for ext in extensions if ext.on_usage is not None]
    if not hooks:
        return None

    async def run(session: AsyncSession, event: UsageEvent) -> None:
        for hook in hooks:
            await hook(session, event)

    return run


def llm_menu_lines(extensions: list[Extension]) -> MenuLines | None:
    sources = [ext.llm_menu_lines for ext in extensions if ext.llm_menu_lines is not None]
    if not sources:
        return None

    async def run(farm_id: uuid.UUID) -> list[str]:
        return [line for source in sources for line in await source(farm_id)]

    return run


class ExtensionMigrations(Protocol):
    TABLES: tuple[str, ...]

    def upgrade(self, url: str) -> None: ...


def extension_migrations() -> list[ExtensionMigrations]:
    return [entry.load() for entry in entry_points(group=MIGRATIONS_GROUP)]


def extension_tables() -> frozenset[str]:
    return frozenset(table for module in extension_migrations() for table in module.TABLES)
