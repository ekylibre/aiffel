"""Commande ``/lyvre`` et boutons ``lyvre:*`` : raccorder la comptabilité Lyvre (exploitant).

Traités hors modèle (ADR-04). La clé ne se saisit jamais dans la conversation : un lien à usage
unique ouvre la page ``/lyvre``. Se déconnecter demande une confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass

from aiffel.channels.base import Button, OutboundMessage
from aiffel.channels.dispatcher import Caller
from aiffel.connectors.lyvre.client import DOMAIN_LABELS
from aiffel.farm_context import FarmRef
from aiffel.lyvre import LyvreService
from aiffel.pairing import PURPOSE_LYVRE, PairingService

PREFIX = "lyvre"
OWNER_ONLY = "Seul l'exploitant de la ferme raccorde la comptabilité Lyvre."
INTRO = (
    "Lyvre tient la comptabilité de la ferme. Une fois raccordé, je réponds aux questions de "
    "l'exploitant sur les achats, les ventes, la banque, la TVA, la MSA ou les marges, en lecture "
    "seule : je n'écris jamais dans Lyvre.\n"
    "Dans Lyvre, créez une clé d'API (Configuration, puis Accès API) en lecture seule, puis "
    "enregistrez-la ici."
)


def domains_text(domains: frozenset[str]) -> str:
    return ", ".join(DOMAIN_LABELS.get(d, d) for d in sorted(domains)) or "aucun"


@dataclass
class LyvreMenu:
    pairing: PairingService
    lyvre: LyvreService
    public_base_url: str

    async def command(self, caller: Caller, farm: FarmRef) -> list[OutboundMessage]:
        if farm.role != "owner":
            return [OutboundMessage(text=OWNER_ONLY)]
        status = await self.lyvre.status(farm.farm_id)
        if status is None or status.status == "revoked":
            return await self._link(caller, INTRO, "Raccorder Lyvre")
        if status.status == "needs_reconnect":
            text = (
                f"🔑 Lyvre a refusé la clé de la structure « {status.structure} » (révoquée ou "
                "expirée). Créez-en une nouvelle dans Lyvre, puis enregistrez-la."
            )
            return await self._link(caller, text, "Enregistrer une nouvelle clé")
        text = (
            f"✅ Comptabilité Lyvre raccordée : « {status.structure} » ({status.base_url}).\n"
            f"Domaines lus : {domains_text(status.reads)}.\n"
            "Posez-moi vos questions (dépenses, trésorerie, TVA, MSA…)."
        )
        replies = await self._link(caller, text, "Changer de clé")
        replies[-1].buttons.append([Button(label="Déconnecter Lyvre", data=f"{PREFIX}:off")])
        return replies

    async def callback(self, caller: Caller, farm: FarmRef, data: str) -> list[OutboundMessage]:
        if farm.role != "owner" or caller.user_id is None:
            return [OutboundMessage(text=OWNER_ONLY)]
        verb = data.removeprefix(f"{PREFIX}:")
        if verb == "off":
            return [
                OutboundMessage(
                    text="Déconnecter Lyvre ? Je ne pourrai plus lire la comptabilité. La clé "
                    "reste à révoquer dans Lyvre si vous ne la réutilisez pas.",
                    buttons=[[Button(label="Confirmer", data=f"{PREFIX}:off!")]],
                )
            ]
        if verb == "off!":
            done = await self.lyvre.disconnect(farm.farm_id, caller.user_id)
            text = (
                "Lyvre est déconnecté. Pensez à révoquer la clé dans Lyvre."
                if done
                else "Lyvre n'était pas raccordé."
            )
            return [OutboundMessage(text=text)]
        return [OutboundMessage(text="Ce bouton n'est plus valable.")]

    async def _link(self, caller: Caller, text: str, label: str) -> list[OutboundMessage]:
        code = await self.pairing.create_code(caller.identity_id, PURPOSE_LYVRE)
        url = f"{self.public_base_url.rstrip('/')}/lyvre?c={code}"
        minutes = int(self.pairing.ttl.total_seconds() // 60)
        note = (
            f"La clé se saisit sur une page web (lien valable {minutes} minutes, une seule fois)."
        )
        if url.startswith("https://"):
            return [
                OutboundMessage(text=f"{text}\n\n{note}", buttons=[[Button(label=label, url=url)]])
            ]
        return [OutboundMessage(text=f"{text}\n\n{note}\n{url}")]
