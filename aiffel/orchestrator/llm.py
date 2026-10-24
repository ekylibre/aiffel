"""Fournisseurs de modèles de langage (ADR-02) : une interface, Anthropic en phase 1.

Le format pivot des messages est celui de l'API Messages d'Anthropic ; un autre fournisseur
(Mistral, Ollama) traduira depuis ce format.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

import anthropic
from anthropic.types.beta import BetaMessageParam, BetaTextBlockParam, BetaToolUnionParam

Effort = Literal["low", "medium", "high", "xhigh", "max"]

# Repli côté serveur si le modèle refuse : Anthropic choisit le modèle de repli selon la catégorie.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(Exception):
    """Fournisseur indisponible ou requête rejetée ; message sans donnée utilisateur."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass
class LLMResult:
    # Contenu à renvoyer tel quel dans l'historique (blocs de réflexion compris, ADR-02).
    assistant_content: list[Any]
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str
    model: str
    usage: Usage = field(default_factory=Usage)

    @property
    def refused(self) -> bool:
        return self.stop_reason == "refusal"


@dataclass(frozen=True)
class SystemPrompt:
    """``stable`` est mis en cache (identique pour toutes les fermes), ``volatile`` non."""

    stable: str
    volatile: str


class LLMProvider(Protocol):
    async def complete(
        self, system: SystemPrompt, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResult: ...


class AnthropicProvider:
    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        model: str,
        *,
        effort: Effort = "medium",
        max_tokens: int = 16000,
    ) -> None:
        self._client = client
        self.model = model
        self.effort: Effort = effort
        self.max_tokens = max_tokens

    async def complete(
        self, system: SystemPrompt, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResult:
        system_blocks: list[BetaTextBlockParam] = [
            # Les outils et ce bloc forment un préfixe stable, mis en cache.
            {"type": "text", "text": system.stable, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": system.volatile},
        ]
        try:
            response = await self._client.beta.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system_blocks,
                tools=cast(list[BetaToolUnionParam], tools),
                messages=cast(list[BetaMessageParam], messages),
                output_config={"effort": self.effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.RateLimitError:
            raise LLMError("limite de débit du fournisseur atteinte") from None
        except anthropic.APIStatusError as exc:
            raise LLMError(f"erreur du fournisseur ({exc.status_code})") from None
        except anthropic.APIConnectionError:
            raise LLMError("fournisseur injoignable") from None

        texts, calls = [], []
        for block in response.content:
            if block.type == "text":
                texts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(block.id, block.name, dict(block.input or {})))
        usage = response.usage
        return LLMResult(
            assistant_content=list(response.content),
            text="\n".join(texts).strip(),
            tool_calls=calls,
            stop_reason=str(response.stop_reason),
            model=response.model,
            usage=Usage(
                usage.input_tokens or 0,
                usage.output_tokens or 0,
                usage.cache_read_input_tokens or 0,
                usage.cache_creation_input_tokens or 0,
            ),
        )
