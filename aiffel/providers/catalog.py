"""Modèles proposés aux fermes : ceux que le banc d'évaluation (A7) a validés.

Résultats du 2026-10-24 sur 50 phrases du corpus de Duke (PR #10) : chaque entrée garde la
procédure reconnue, la parcelle retrouvée et une proposition d'intervention à plus de 75 %.
Un modèle n'entre ici qu'après un passage du banc ; ``key`` sert dans les boutons Telegram.
Sans choix, une ferme garde le modèle par défaut (``AIFFEL_LLM_PROVIDER``, ``AIFFEL_LLM_MODEL``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from aiffel.orchestrator.llm import Effort

ProviderName = Literal["anthropic", "openrouter"]
PROVIDERS: dict[ProviderName, str] = {"anthropic": "Anthropic", "openrouter": "OpenRouter"}


@dataclass(frozen=True)
class ModelChoice:
    key: str
    label: str
    provider: ProviderName
    model: str
    note: str
    effort: Effort | None = None


CHOICES: tuple[ModelChoice, ...] = (
    ModelChoice(
        "deepseek",
        "DeepSeek V4.1 Flash",
        "openrouter",
        "deepseek/deepseek-v4.1-flash",
        "le moins cher à qualité égale, rapide",
    ),
    ModelChoice(
        "mistral",
        "Mistral Large 4",
        "openrouter",
        "mistralai/mistral-large-4-0",
        "éditeur européen, plus lent",
    ),
    ModelChoice("haiku", "Claude Haiku 5.5", "anthropic", "claude-haiku-5-5", "économique, rapide"),
    ModelChoice("sonnet", "Claude Sonnet 5.5", "anthropic", "claude-sonnet-5-5", "le plus rapide"),
)
BY_KEY = {choice.key: choice for choice in CHOICES}


def by_key(key: str | None) -> ModelChoice | None:
    return BY_KEY.get(key or "")
