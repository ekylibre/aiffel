"""Orchestrateur : un tour de conversation avec l'assistant de ferme (A4, ADR-02/03).

Boucle : modèle → outils (via la passerelle) → modèle, jusqu'à une réponse finale, avec un
plafond d'itérations et de tokens. Le contenu de l'assistant est renvoyé tel quel d'une
itération à l'autre (historique en ajout seul) ; entre deux tours, seuls les textes échangés
sont conservés (``messages``), sur une fenêtre courte.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiffel.actions.service import ActionService, callback_data
from aiffel.channels.base import Button, InboundKind, InboundMessage, OutboundMessage
from aiffel.channels.dispatcher import Caller
from aiffel.db.models import Conversation, FarmMemory, Message
from aiffel.db.session import farm_session
from aiffel.extensions import UsageEvent, UsageHook
from aiffel.farm_context import EkylibreAccess, FarmContextService, FarmRef
from aiffel.farm_llm import NoPlatformCredit, ResolvedLLM
from aiffel.memory import CALLBACK_PREFIX as MEMORY_PREFIX
from aiffel.memory import MemoryService
from aiffel.orchestrator.llm import LLMError, LLMProvider, SystemPrompt, Usage
from aiffel.orchestrator.runtime import AgentRuntime
from aiffel.tools.registry import LexiconLike, PlantNetLike, ToolContext, ToolGateway
from aiffel.voice import AudioTooLargeError

log = structlog.get_logger()

HISTORY_MESSAGES = 10
HISTORY_WINDOW = timedelta(hours=12)
MAX_TURN_TOKENS = 400_000

RECONNECT = (
    "Ekylibre a refusé l'accès (déconnexion depuis un autre appareil ou mot de passe changé). "
    "Envoyez /start pour vous reconnecter."
)
UNAVAILABLE = "Je ne peux pas répondre pour le moment (service indisponible). Réessayez plus tard."
REFUSED = "Je ne peux pas traiter cette demande. Reformulez-la, ou envoyez /aide."
TOO_LONG = (
    "Je n'ai pas réussi à aller au bout de cette demande. Pouvez-vous la préciser "
    "(parcelle, campagne, produit) ?"
)
VOICE_LATER = "Les messages vocaux arrivent bientôt. En attendant, écrivez-moi votre demande."
TRANSCRIBED_NOTE = "[Message vocal transcrit automatiquement : des mots peuvent être mal reconnus]"
PHOTO_LATER = "Je ne sais pas encore analyser les photos. Décrivez-moi ce que vous voyez."
PHOTO_NOTE = (
    "[Photo jointe au message : tu ne la vois pas. Les outils Pl@ntNet l'analysent ; "
    "charge le savoir-faire diagnostiquer-photo]"
)
PHOTO_QUESTION = "Que montre cette photo ?"
PHOTO_TOO_LARGE = "Cette photo est trop lourde. Envoyez-la en photo (compressée), pas en fichier."
PHOTO_FAILED = "Je n'ai pas réussi à récupérer la photo. Renvoyez-la, s'il vous plaît."
BUDGET_EXHAUSTED = (
    "Le crédit d'IA offert à votre ferme est épuisé. Pour continuer, l'exploitant peut ajouter "
    "sa propre clé OpenRouter ou Anthropic avec /modele."
)
NO_MODEL = (
    "Aucun modèle d'IA n'est disponible pour votre ferme. L'exploitant peut en choisir un et "
    "ajouter sa clé avec /modele."
)


class LLMResolver(Protocol):
    """Choisit le fournisseur de modèle d'une ferme (voir :mod:`aiffel.farm_llm`)."""

    async def resolve(self, farm_id: uuid.UUID) -> ResolvedLLM | None:
        """Lève :class:`NoPlatformCredit` si la règle de crédit refuse la clé de la plateforme."""


def _total(usage: Usage) -> int:
    """Jetons d'un tour, cache compris : c'est ce que compte le quota de la clé d'Aiffel."""
    return (
        usage.input_tokens
        + usage.output_tokens
        + usage.cache_read_tokens
        + usage.cache_write_tokens
    )


@dataclass
class Orchestrator:
    sessions: async_sessionmaker[AsyncSession]
    farms: FarmContextService
    provider: LLMProvider | None  # modèle par défaut ; ``llm`` le remplace ferme par ferme
    gateway: ToolGateway
    runtime: AgentRuntime
    lexicon: LexiconLike | None = None
    max_iterations: int = 8
    model_label: str = "llm"
    actions: ActionService | None = None
    llm: LLMResolver | None = None
    # Consommation de chaque tour, transmise aux extensions (mesure, quota…).
    on_usage: UsageHook | None = None
    memory: MemoryService | None = None
    # Photos : Pl@ntNet et le téléchargement auprès du canal (en mémoire, borné).
    plantnet: PlantNetLike | None = None
    fetch_photo: Callable[[str, int], Awaitable[bytes]] | None = None
    photo_max_bytes: int = 10 * 1024 * 1024

    async def run_turn(
        self, caller: Caller, farm: FarmRef, message: InboundMessage
    ) -> list[OutboundMessage]:
        if caller.user_id is None:  # le répartiteur ne nous transmet que des utilisateurs appairés
            return []
        if message.kind is InboundKind.VOICE:
            return [OutboundMessage(text=VOICE_LATER)]
        analyse_photo = (
            message.kind is InboundKind.PHOTO
            and self.plantnet is not None
            and self.fetch_photo is not None
            and bool(message.file_id)
        )
        if message.kind is InboundKind.PHOTO and not analyse_photo and not message.text:
            return [OutboundMessage(text=PHOTO_LATER)]
        text = (message.text or "").strip() or (PHOTO_QUESTION if analyse_photo else "")
        if not text:
            return [OutboundMessage(text="Écrivez-moi votre question. /aide")]

        provider, label = self.provider, self.model_label
        platform, budget = True, None
        if self.llm is not None:
            try:
                resolved = await self.llm.resolve(farm.farm_id)
            except NoPlatformCredit as exc:
                return [OutboundMessage(text=exc.message)]
            provider = resolved.provider if resolved else None
            if resolved is not None:
                label, platform, budget = (
                    resolved.label,
                    not resolved.own_key,
                    resolved.token_budget,
                )
        if provider is None:
            return [OutboundMessage(text=NO_MODEL)]

        access = await self.farms.ekylibre(farm, caller.user_id)
        if access is not None and access.client is None:
            return [OutboundMessage(text=RECONNECT)]

        photos: list[bytes] = []
        if analyse_photo and self.fetch_photo is not None:
            # Téléchargée après les contrôles d'accès et de crédit ; en mémoire le temps du tour.
            try:
                photos = [await self.fetch_photo(message.file_id or "", self.photo_max_bytes)]
            except AudioTooLargeError:
                return [OutboundMessage(text=PHOTO_TOO_LARGE)]
            except Exception as exc:
                log.warning("agent.photo_download_failed", error=type(exc).__name__)
                return [OutboundMessage(text=PHOTO_FAILED)]

        ctx = ToolContext(
            farm_id=farm.farm_id,
            farm_name=farm.name,
            role="owner" if farm.role == "owner" else "worker",
            user_id=caller.user_id,
            today=date.today(),
            ekylibre=access.client if access else None,
            lexicon=self.lexicon,
            aliases=await self._aliases(farm.farm_id),
            skills={name: doc.body for name, doc in self.runtime.skills.items()},
            actions=self.actions,
            memory=self.memory,
            plantnet=self.plantnet,
            photos=photos,
        )
        open_proposal = (
            await self.actions.open_proposal(farm.farm_id, caller.user_id, "intervention")
            if self.actions
            else None
        )
        conversation_id, history = await self._history(
            farm.farm_id, caller.user_id, message.channel
        )
        content = f"{TRANSCRIBED_NOTE}\n{text}" if message.transcribed else text
        if photos:
            content = f"{PHOTO_NOTE}\n{content}"
        messages: list[dict[str, Any]] = [*history, {"role": "user", "content": content}]

        try:
            answer, usage = await self._loop(provider, ctx, messages, open_proposal, budget)
        except LLMError as exc:
            log.warning("agent.llm_error", error=str(exc))
            return [
                OutboundMessage(text=UNAVAILABLE),
                *await self._after_refusal(ctx, farm, access, caller.user_id),
            ]
        reconnect = await self._after_refusal(ctx, farm, access, caller.user_id)

        # L'historique ne garde que du texte : on y note qu'une photo accompagnait la question.
        recorded = f"[Photo] {text}" if photos else text
        await self._record(farm.farm_id, conversation_id, recorded, answer, usage, label, platform)
        replies = [OutboundMessage(text=answer)]
        if ctx.proposals:
            # Récapitulatif produit par le code à partir de la charge utile réelle : c'est lui que
            # l'utilisateur valide, pas la reformulation du modèle.
            proposal = ctx.proposals[-1]
            replies.append(
                OutboundMessage(
                    text=f"{proposal.summary}\n\nValidez pour enregistrer dans Ekylibre.",
                    buttons=[
                        [
                            Button(
                                label="✅ Valider",
                                data=callback_data("v", proposal.id, proposal.digest),
                            ),
                            Button(
                                label="✏️ Corriger",
                                data=callback_data("c", proposal.id, proposal.digest),
                            ),
                            Button(
                                label="✖️ Annuler",
                                data=callback_data("x", proposal.id, proposal.digest),
                            ),
                        ]
                    ],
                )
            )
        for fact in ctx.memory_proposals:
            # Rien n'est retenu sans ce bouton, traité hors modèle (F-MEM).
            replies.append(
                OutboundMessage(
                    text=f"🧠 Je retiens pour la ferme : « {fact.alias} » = {fact.target} ?",
                    buttons=[
                        [
                            Button(label="✅ Retenir", data=f"{MEMORY_PREFIX}:y:{fact.id}"),
                            Button(label="✖️ Non", data=f"{MEMORY_PREFIX}:n:{fact.id}"),
                        ]
                    ],
                )
            )
        return [*replies, *reconnect]

    async def _after_refusal(
        self, ctx: ToolContext, farm: FarmRef, access: EkylibreAccess | None, user_id: uuid.UUID
    ) -> list[OutboundMessage]:
        """Jeton Ekylibre refusé pendant le tour : connexion à refaire, et l'utilisateur le sait."""
        if not ctx.ekylibre_refused or access is None:
            return []
        await self.farms.mark_needs_reconnect(farm, access.connection_id, user_id)
        return [OutboundMessage(text=RECONNECT)]

    async def _loop(
        self,
        provider: LLMProvider,
        ctx: ToolContext,
        messages: list[dict[str, Any]],
        open_proposal: str | None = None,
        token_budget: int | None = None,
    ) -> tuple[str, Usage]:
        volatile = self._volatile(ctx)
        if open_proposal:
            volatile += (
                "\n- Proposition en attente de validation "
                "(une nouvelle proposition la remplacera) :\n" + open_proposal
            )
        system = SystemPrompt(stable=self.runtime.stable_prompt(), volatile=volatile)
        tools = self.gateway.definitions(ctx)
        usage = Usage()
        for _ in range(self.max_iterations):
            result = await provider.complete(system, messages, tools)
            usage += result.usage
            if result.refused:
                log.info("agent.refusal")
                return REFUSED, usage
            if not result.tool_calls:
                return result.text or TOO_LONG, usage
            if usage.input_tokens + usage.output_tokens > MAX_TURN_TOKENS:
                log.warning("agent.token_budget")
                return TOO_LONG, usage
            if token_budget is not None and _total(usage) >= token_budget:
                # Crédit de la plateforme épuisé en cours de tour : on s'arrête là.
                log.info("agent.platform_budget")
                return BUDGET_EXHAUSTED, usage
            messages.append({"role": "assistant", "content": result.assistant_content})
            outcomes = await asyncio.gather(
                *(self.gateway.execute(ctx, call.name, call.input) for call in result.tool_calls)
            )
            # Tous les résultats dans un seul message, dans l'ordre des appels.
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": call.id,
                            "content": outcome.content,
                            "is_error": outcome.is_error,
                        }
                        for call, outcome in zip(result.tool_calls, outcomes, strict=True)
                    ],
                }
            )
        log.warning("agent.max_iterations")
        return TOO_LONG, usage

    @staticmethod
    def _volatile(ctx: ToolContext) -> str:
        """Contexte propre à la requête, hors cache. Jamais d'identifiant interne ni de secret."""
        role = "exploitant" if ctx.role == "owner" else "salarié"
        lines = [
            "# Contexte",
            f"- Ferme : {ctx.farm_name}",
            f"- Interlocuteur : {role}",
            f"- Date du jour : {ctx.today.isoformat()} (campagne en cours : {ctx.today.year})",
            f"- Ekylibre : {'connecté' if ctx.ekylibre else 'non connecté'}",
        ]
        if ctx.aliases:
            lines.append(
                "- Noms usuels confirmés : "
                + "; ".join(f"« {k} » = {v}" for k, v in ctx.aliases.items())
            )
        return "\n".join(lines)

    async def _aliases(self, farm_id: uuid.UUID) -> dict[str, str]:
        async with farm_session(self.sessions, farm_id) as session:
            rows = await session.scalars(
                select(FarmMemory).where(
                    FarmMemory.kind == "alias", FarmMemory.confirmed_at.is_not(None)
                )
            )
            return {str(k): str(v) for row in rows for k, v in row.content.items()}

    async def _history(
        self, farm_id: uuid.UUID, user_id: uuid.UUID, channel: str
    ) -> tuple[uuid.UUID, list[dict[str, Any]]]:
        since = datetime.now(UTC) - HISTORY_WINDOW
        async with farm_session(self.sessions, farm_id) as session:
            conversation = await session.scalar(
                select(Conversation)
                .where(Conversation.user_id == user_id, Conversation.channel == channel)
                .order_by(Conversation.last_message_at.desc())
                .limit(1)
            )
            if conversation is None or conversation.last_message_at < since:
                conversation = Conversation(farm_id=farm_id, user_id=user_id, channel=channel)
                session.add(conversation)
                await session.flush()
                return conversation.id, []
            rows = list(
                await session.scalars(
                    select(Message)
                    .where(Message.conversation_id == conversation.id, Message.created_at >= since)
                    .order_by(Message.created_at.desc())
                    .limit(HISTORY_MESSAGES)
                )
            )
        history = [{"role": m.role, "content": m.content} for m in reversed(rows)]
        # L'historique doit commencer par l'utilisateur.
        while history and history[0]["role"] != "user":
            history.pop(0)
        return conversation.id, history

    async def _record(
        self,
        farm_id: uuid.UUID,
        conversation_id: uuid.UUID,
        question: str,
        answer: str,
        usage: Usage,
        model: str,
        platform: bool = True,
    ) -> None:
        """Hook on_turn_end : historique du tour, et consommation transmise aux extensions."""
        now = datetime.now(UTC)
        async with farm_session(self.sessions, farm_id) as session:
            # Horodatages explicites et croissants : now() est identique dans une transaction,
            # ce qui rendrait l'ordre question/réponse indéterminé à la relecture.
            session.add(
                Message(
                    farm_id=farm_id,
                    conversation_id=conversation_id,
                    role="user",
                    content=question,
                    created_at=now,
                )
            )
            session.add(
                Message(
                    farm_id=farm_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=answer,
                    created_at=now + timedelta(milliseconds=1),
                )
            )
            conversation = await session.get(Conversation, conversation_id)
            if conversation is not None:
                conversation.last_message_at = now
            if self.on_usage is not None:
                await self.on_usage(
                    session,
                    UsageEvent(
                        farm_id=farm_id,
                        at=now,
                        model=model[:100],
                        tokens_in=usage.input_tokens
                        + usage.cache_read_tokens
                        + usage.cache_write_tokens,
                        tokens_out=usage.output_tokens,
                        cost_usd=usage.cost_usd,
                        platform=platform,
                    ),
                )
        log.info(
            "agent.turn",
            tokens_in=usage.input_tokens,
            tokens_out=usage.output_tokens,
            cache_read=usage.cache_read_tokens,
        )
