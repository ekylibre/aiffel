"""Réponses aux commandes de base (jalon M1) ; le reste des messages ira à l'orchestrateur (A4)."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from aiffel.actions.service import ActionService, Outcome, parse_callback
from aiffel.channels.base import Button, InboundKind, InboundMessage, OutboundMessage
from aiffel.channels.dispatcher import UNSUPPORTED, WELCOME, Caller
from aiffel.connectors.ekylibre.client import EkylibreError, EkylibreUnauthorized
from aiffel.extensions import CommandHandler, Extension
from aiffel.farm_context import FarmContextService, FarmRef
from aiffel.farm_llm import FarmLLMService, LLMKeyError
from aiffel.pairing import PURPOSE_LLM_KEY, PairingService
from aiffel.providers.catalog import CHOICES, PROVIDERS, ProviderName
from aiffel.voice import VoiceService

HELP_LINES = (
    "• /ferme : votre ferme et ses parcelles",
    "• /modele : le modèle d'IA de la ferme (et votre propre clé)",
    "• /start : relier ou reconnecter votre compte Ekylibre",
    "• /aide : ce message",
)
HELP_END = "Bientôt : enregistrer une intervention en me la dictant, et répondre à vos questions."


def help_text(extra: list[str] | None = None) -> str:
    lines = [*HELP_LINES[:-1], *(extra or []), HELP_LINES[-1]]
    return "Voici ce que je sais faire pour l'instant :\n" + "\n".join(lines) + f"\n\n{HELP_END}"


NO_FARM = "Votre compte n'est relié à aucune ferme. Envoyez /start pour vous connecter."
RECONNECT = (
    "Ekylibre a refusé l'accès (déconnexion depuis un autre appareil ou mot de passe changé). "
    "Envoyez /start pour vous reconnecter."
)
UNAVAILABLE = "Ekylibre ne répond pas pour le moment. Réessayez dans quelques minutes."
MAX_LISTED_ZONES = 30
LLM_CALLBACK = "llm"
OWNER_ONLY = "Seul l'exploitant de la ferme peut changer le modèle d'IA."


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
    actions: ActionService | None = None
    voice: VoiceService | None = None
    llm: FarmLLMService | None = None
    extensions: list[Extension] = field(default_factory=list)

    def _command(self, name: str | None) -> CommandHandler | None:
        return next((e.commands[name] for e in self.extensions if name in e.commands), None)

    def _extension_callback(self, data: str | None) -> CommandHandler | None:
        prefix = (data or "").split(":", 1)[0]
        return next((e.callbacks[prefix] for e in self.extensions if prefix in e.callbacks), None)

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

        if message.kind is InboundKind.CALLBACK:
            # Les boutons ne passent jamais par le modèle (ADR-04).
            return await self._callback(caller, farm, message)
        if message.kind is InboundKind.COMMAND:
            if message.command == "start":
                access = await self.farms.ekylibre(farm, caller.user_id)
                if access is None or access.client is None:
                    return await self.invite(caller, f"Reconnexion à la ferme {farm.name}.")
                return [OutboundMessage(text=f"Vous êtes connecté à la ferme {farm.name}. /aide")]
            if message.command in ("aide", "help"):
                extra = [line for e in self.extensions for line in e.help_lines]
                return [OutboundMessage(text=help_text(extra))]
            if message.command == "ferme":
                return await self._farm_summary(caller, farm)
            if message.command in ("modele", "modèle", "model") and self.llm is not None:
                return await self._llm_menu(self.llm, farm)
            if (command := self._command(message.command)) is not None:
                return await command(caller, farm, message)
        if message.kind is InboundKind.VOICE and self.voice is not None and message.file_id:
            return await self._voice(self.voice, caller, farm, message)
        return await self.assistant(caller, farm, message)

    async def _voice(
        self, voice: VoiceService, caller: Caller, farm: FarmRef, message: InboundMessage
    ) -> list[OutboundMessage]:
        """Transcrit le vocal, le renvoie à l'utilisateur, puis le traite comme un texte."""
        result = await voice.transcribe(farm.farm_id, message.file_id or "", message.duration_s)
        if result.text is None:
            return [OutboundMessage(text=result.error or "Message vocal illisible.")]
        heard = OutboundMessage(
            text=f"🎙️ J'ai compris : « {result.text} »\n(Si c'est faux, écrivez-moi la correction.)"
        )
        as_text = message.model_copy(
            update={"kind": InboundKind.TEXT, "text": result.text, "transcribed": True}
        )
        return [heard, *await self.assistant(caller, farm, as_text)]

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

    async def _callback(
        self, caller: Caller, farm: FarmRef, message: InboundMessage
    ) -> list[OutboundMessage]:
        if (message.callback_data or "").startswith(f"{LLM_CALLBACK}:") and self.llm is not None:
            return await self._llm_callback(self.llm, caller, farm, message.callback_data or "")
        if (callback := self._extension_callback(message.callback_data)) is not None:
            return await callback(caller, farm, message)
        parsed = parse_callback(message.callback_data)
        if parsed is None or self.actions is None or caller.user_id is None:
            return [OutboundMessage(text="Ce bouton n'est plus valable.")]
        if parsed.verb == "c":
            return [
                OutboundMessage(
                    text="D'accord. Dites-moi ce qu'il faut corriger "
                    "(date, parcelle, produit, dose…)."
                )
            ]
        if parsed.verb == "x":
            cancelled = await self.actions.cancel(farm.farm_id, caller.user_id, parsed)
            return [OutboundMessage(text=cancelled.text)]

        access = await self.farms.ekylibre(farm, caller.user_id)
        if access is None or access.client is None:
            return [OutboundMessage(text=RECONNECT)]
        outcome: Outcome = await self.actions.validate(
            farm.farm_id, caller.user_id, parsed, access.client.create_intervention
        )
        if not outcome.ok and "reconnecter" in outcome.text:
            await self.farms.mark_needs_reconnect(farm, access.connection_id, caller.user_id)
        if outcome.ok and outcome.external_ref:
            link = f"{access.client.instance.base_url}/backend/interventions/{outcome.external_ref}"
            return [
                OutboundMessage(
                    text=outcome.text, buttons=[[Button(label="Voir dans Ekylibre", url=link)]]
                )
            ]
        return [OutboundMessage(text=outcome.text)]

    async def _llm_menu(self, llm: FarmLLMService, farm: FarmRef) -> list[OutboundMessage]:
        """Modèle actuel et choix possibles ; boutons réservés à l'exploitant."""
        setting = await llm.current(farm.farm_id)
        if setting.choice is None:
            current = "le modèle par défaut d'Aiffel"
        else:
            whose = (
                "clé de la ferme" if setting.choice.provider in setting.own_keys else "clé Aiffel"
            )
            current = f"{setting.choice.label} ({PROVIDERS[setting.choice.provider]}, {whose})"
        lines = [f"🤖 Modèle d'IA de la ferme : {current}.", "", "Modèles validés pour Aiffel :"]
        for choice in CHOICES:
            missing = "" if llm.usable(choice, setting) else " — clé requise"
            lines.append(
                f"• {choice.label} ({PROVIDERS[choice.provider]}) : {choice.note}{missing}"
            )
        for extension in self.extensions:
            if extension.llm_menu_lines is not None:
                lines += await extension.llm_menu_lines(farm.farm_id)
        if farm.role != "owner":
            return [OutboundMessage(text="\n".join([*lines, "", OWNER_ONLY]))]
        lines += [
            "",
            "Avec votre propre clé OpenRouter ou Anthropic, la consommation vous est facturée "
            "directement par le fournisseur.",
        ]
        buttons = [
            [
                Button(
                    label=("✅ " if choice == setting.choice else "") + choice.label,
                    data=f"{LLM_CALLBACK}:m:{choice.key}",
                )
            ]
            for choice in CHOICES
        ]
        buttons.append([Button(label="🔑 Ajouter ma clé", data=f"{LLM_CALLBACK}:k")])
        buttons += [
            [Button(label=f"Retirer ma clé {PROVIDERS[p]}", data=f"{LLM_CALLBACK}:rm:{p}")]
            for p in sorted(setting.own_keys)
        ]
        return [OutboundMessage(text="\n".join(lines), buttons=buttons)]

    async def _llm_callback(
        self, llm: FarmLLMService, caller: Caller, farm: FarmRef, data: str
    ) -> list[OutboundMessage]:
        if caller.user_id is None:
            return [OutboundMessage(text=NO_FARM)]
        if farm.role != "owner":
            return [OutboundMessage(text=OWNER_ONLY)]
        parts = data.split(":")
        if parts[1:2] == ["k"]:
            return await self._key_link(caller)
        if len(parts) == 3 and parts[1] == "rm" and parts[2] in PROVIDERS:
            provider: ProviderName = parts[2]
            removed = await llm.remove_key(farm.farm_id, caller.user_id, provider)
            text = f"Clé {PROVIDERS[provider]} retirée." if removed else "Aucune clé à retirer."
            return [OutboundMessage(text=text)]
        if len(parts) == 3 and parts[1] == "m":
            try:
                choice = await llm.choose(farm.farm_id, caller.user_id, parts[2])
            except LLMKeyError as exc:
                return [OutboundMessage(text=str(exc))]
            if not llm.usable(choice, await llm.current(farm.farm_id)):
                return [
                    OutboundMessage(
                        text=f"{choice.label} choisi, mais il faut une clé "
                        f"{PROVIDERS[choice.provider]} : d'ici là, j'utilise le modèle par défaut."
                    ),
                    *await self._key_link(caller),
                ]
            return [OutboundMessage(text=f"C'est noté : j'utilise désormais {choice.label}.")]
        return [OutboundMessage(text="Ce bouton n'est plus valable.")]

    async def _key_link(self, caller: Caller) -> list[OutboundMessage]:
        code = await self.pairing.create_code(caller.identity_id, PURPOSE_LLM_KEY)
        url = f"{self.public_base_url.rstrip('/')}/cle?c={code}"
        minutes = int(self.pairing.ttl.total_seconds() // 60)
        text = (
            "Pour votre sécurité, la clé se saisit sur une page web, jamais dans la conversation. "
            f"Ouvrez ce lien (valable {minutes} minutes, une seule fois)."
        )
        if url.startswith("https://"):
            return [OutboundMessage(text=text, buttons=[[Button(label="Ajouter ma clé", url=url)]])]
        return [OutboundMessage(text=f"{text}\n{url}")]
