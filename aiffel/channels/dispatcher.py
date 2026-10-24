"""Répartition d'un message entrant : identité, appairage, puis traitement par l'assistant.

En A2, une identité non appairée reçoit l'invitation à se connecter (le lien arrive avec A3) ;
une identité appairée est confiée à ``paired_handler`` (l'orchestrateur, avec A4).
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiffel.channels.base import InboundKind, InboundMessage, OutboundMessage
from aiffel.channels.identity import IdentityService
from aiffel.db.session import global_session

PairedHandler = Callable[[uuid.UUID, InboundMessage], Awaitable[list[OutboundMessage]]]
UnpairedHandler = Callable[[uuid.UUID, InboundMessage], Awaitable[list[OutboundMessage]]]

WELCOME = (
    "Bonjour, je suis Aiffel, l'assistant de votre exploitation. 🌾\n\n"
    "Je peux enregistrer vos interventions dans Ekylibre et répondre à vos questions sur vos "
    "données. Pour commencer, il faut relier ce compte Telegram à votre ferme."
)
NOT_PAIRED = (
    "Ce compte Telegram n'est pas encore relié à une ferme. Envoyez /start pour vous connecter."
)
UNSUPPORTED = "Je ne sais pas encore traiter ce type de message. Écrivez-moi ou envoyez un vocal."


async def default_unpaired(
    identity_id: uuid.UUID, message: InboundMessage
) -> list[OutboundMessage]:
    if message.kind is InboundKind.COMMAND and message.command == "start":
        return [OutboundMessage(text=WELCOME)]
    return [OutboundMessage(text=NOT_PAIRED)]


async def placeholder_paired(_user_id: uuid.UUID, message: InboundMessage) -> list[OutboundMessage]:
    if message.kind is InboundKind.UNSUPPORTED:
        return [OutboundMessage(text=UNSUPPORTED)]
    return [OutboundMessage(text="Message reçu. L'assistant arrive très bientôt.")]


@dataclass
class Dispatcher:
    sessions: async_sessionmaker[AsyncSession]
    identities: IdentityService
    on_unpaired: UnpairedHandler = default_unpaired
    on_paired: PairedHandler = placeholder_paired

    async def handle(self, message: InboundMessage) -> list[OutboundMessage]:
        async with global_session(self.sessions) as session:
            identity = await self.identities.resolve(
                session, message.channel, message.external_user_id
            )
            identity_id, user_id = identity.id, identity.user_id
        if user_id is None:
            return await self.on_unpaired(identity_id, message)
        return await self.on_paired(user_id, message)
