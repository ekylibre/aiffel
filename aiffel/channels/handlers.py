"""Réponses aux commandes de base (jalon M1) ; le reste des messages ira à l'orchestrateur (A4)."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aiffel.channels.base import Button, InboundKind, InboundMessage, OutboundMessage
from aiffel.channels.dispatcher import UNSUPPORTED, WELCOME, Caller
from aiffel.connectors.ekylibre.client import EkylibreError, EkylibreUnauthorized
from aiffel.farm_context import FarmContextService, FarmRef
from aiffel.pairing import PairingService

HELP = (
    "Voici ce que je sais faire pour l'instant :\n"
    "• /ferme : votre ferme et ses parcelles\n"
    "• /start : relier ou reconnecter votre compte Ekylibre\n"
    "• /aide : ce message\n\n"
    "Bientôt : enregistrer une intervention en me la dictant, et répondre à vos questions."
)
NO_FARM = "Votre compte n'est relié à aucune ferme. Envoyez /start pour vous connecter."
RECONNECT = (
    "Ekylibre a refusé l'accès (déconnexion depuis un autre appareil ou mot de passe changé). "
    "Envoyez /start pour vous reconnecter."
)
UNAVAILABLE = "Ekylibre ne répond pas pour le moment. Réessayez dans quelques minutes."
MAX_LISTED_ZONES = 30

Assistant = Callable[[Caller, FarmRef, InboundMessage], Awaitable[list[OutboundMessage]]]


async def placeholder_assistant(
    _caller: Caller, _farm: FarmRef, message: InboundMessage
) -> list[OutboundMessage]:
    if message.kind is InboundKind.UNSUPPORTED:
        return [OutboundMessage(text=UNSUPPORTED)]
    return [OutboundMessage(text="Message reçu. L'assistant arrive très bientôt. /aide")]


@dataclass
class Handlers:
    pairing: PairingService
    farms: FarmContextService
    public_base_url: str
    assistant: Assistant = placeholder_assistant

    async def invite(self, caller: Caller, intro: str) -> list[OutboundMessage]:
        code = await self.pairing.create_code(caller.identity_id)
        url = f"{self.public_base_url.rstrip('/')}/connect?c={code}"
        minutes = int(self.pairing.ttl.total_seconds() // 60)
        text = (
            f"{intro}\n\nOuvrez ce lien pour vous connecter "
            f"(valable {minutes} minutes, une seule fois)."
        )
        # Telegram n'accepte que des URL publiques dans les boutons : en local, lien en clair.
        if url.startswith("https://"):
            return [OutboundMessage(text=text, buttons=[[Button(label="Me connecter", url=url)]])]
        return [OutboundMessage(text=f"{text}\n{url}")]

    async def unpaired(self, caller: Caller, message: InboundMessage) -> list[OutboundMessage]:
        if message.kind is InboundKind.COMMAND and message.command == "start":
            return await self.invite(caller, WELCOME)
        return [
            OutboundMessage(text="Je ne vous connais pas encore. Envoyez /start pour commencer.")
        ]

    async def paired(self, caller: Caller, message: InboundMessage) -> list[OutboundMessage]:
        if caller.user_id is None:
            return await self.unpaired(caller, message)
        farm = await self.farms.farm_of(caller.user_id)
        if farm is None:
            return await self.invite(caller, NO_FARM)

        if message.kind is InboundKind.COMMAND:
            if message.command == "start":
                access = await self.farms.ekylibre(farm, caller.user_id)
                if access is None or access.client is None:
                    return await self.invite(caller, f"Reconnexion à la ferme {farm.name}.")
                return [OutboundMessage(text=f"Vous êtes connecté à la ferme {farm.name}. /aide")]
            if message.command in ("aide", "help"):
                return [OutboundMessage(text=HELP)]
            if message.command == "ferme":
                return await self._farm_summary(caller, farm)
        return await self.assistant(caller, farm, message)

    async def _farm_summary(self, caller: Caller, farm: FarmRef) -> list[OutboundMessage]:
        if caller.user_id is None:
            return [OutboundMessage(text=NO_FARM)]
        access = await self.farms.ekylibre(farm, caller.user_id)
        if access is None or access.client is None:
            return [OutboundMessage(text=RECONNECT)]
        try:
            zones = await access.client.cultivable_zones()
        except EkylibreUnauthorized:
            await self.farms.mark_needs_reconnect(farm, access.connection_id, caller.user_id)
            return [OutboundMessage(text=RECONNECT)]
        except EkylibreError:
            return [OutboundMessage(text=UNAVAILABLE)]

        role = "exploitant" if farm.role == "owner" else "salarié"
        lines = [f"🌾 Ferme {farm.name} ({role})", f"{len(zones)} parcelle(s) cultivable(s) :"]
        names = sorted(str(z.get("name") or z.get("work_number") or "?") for z in zones)
        lines += [f"• {name}" for name in names[:MAX_LISTED_ZONES]]
        if len(names) > MAX_LISTED_ZONES:
            lines.append(f"… et {len(names) - MAX_LISTED_ZONES} autres.")
        lines.append("\nSource : Ekylibre")
        return [OutboundMessage(text="\n".join(lines))]
