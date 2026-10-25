"""Passerelle d'outils de l'agent (ADR-01, ADR-03, ADR-05).

- Le modèle ne choisit jamais la ferme : la passerelle injecte un :class:`ToolContext` construit
  par le code, à partir de l'identité vérifiée de l'utilisateur.
- Les outils proposés sont filtrés par rôle et par source disponible ; un appel à un outil absent
  de la liste est refusé (``pre_tool``).
- Les entrées sont validées (Pydantic) ; les résultats sont encadrés comme des **données** avec
  leur source et leur licence, et tronqués s'ils sont trop longs.
- Aucun outil générique (shell, fichiers, HTTP libre) n'existe ici.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Protocol

import structlog
from pydantic import BaseModel, ValidationError

log = structlog.get_logger()

MAX_RESULT_CHARS = 20_000
TOOL_TIMEOUT_S = 30.0
Role = Literal["owner", "worker"]
Requirement = Literal["ekylibre", "lexicon", "plantnet"]


class ToolAbort(Exception):
    """Interrompt tout le tour (par exemple un jeton Ekylibre refusé)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ToolFailure(Exception):
    """Échec d'un outil, renvoyé au modèle comme résultat d'erreur (le tour continue)."""


class LexiconLike(Protocol):
    async def call(self, name: str, arguments: dict[str, Any]) -> Any: ...


class PlantNetLike(Protocol):
    async def identify_disease(self, images: list[bytes]) -> Any: ...

    async def identify_species(self, images: list[bytes]) -> Any: ...

    async def aclose(self) -> None: ...


@dataclass
class ToolContext:
    farm_id: uuid.UUID
    farm_name: str
    role: Role
    user_id: uuid.UUID
    today: date
    ekylibre: Any | None = None  # EkylibreClient ; typé Any pour éviter un import circulaire
    lexicon: LexiconLike | None = None
    aliases: dict[str, str] = field(default_factory=dict)
    skills: dict[str, str] = field(default_factory=dict)
    # Service des propositions (A5) et propositions créées pendant le tour.
    actions: Any | None = None
    proposals: list[Any] = field(default_factory=list)
    # Photos du message en cours (en mémoire) : seuls les outils Pl@ntNet les lisent, le modèle
    # ne les voit pas.
    plantnet: PlantNetLike | None = None
    photos: list[bytes] = field(default_factory=list, repr=False)

    def has(self, requirement: Requirement | None) -> bool:
        if requirement == "ekylibre":
            return self.ekylibre is not None
        if requirement == "lexicon":
            return self.lexicon is not None
        if requirement == "plantnet":
            return self.plantnet is not None and bool(self.photos)
        return True


@dataclass(frozen=True)
class ToolOutput:
    data: Any
    source: str
    licence: str | None = None


Handler = Callable[[ToolContext, Any], Awaitable[ToolOutput]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    handler: Handler
    input_model: type[BaseModel] | None = None
    # Schéma JSON fourni tel quel (outils MCP) quand il n'y a pas de modèle Pydantic.
    input_schema: dict[str, Any] | None = None
    # « propose » : crée une proposition locale à valider ; « write » : toujours refusé ici.
    access: Literal["read", "propose", "write"] = "read"
    roles: frozenset[str] = frozenset({"owner", "worker"})
    requires: Requirement | None = None

    def schema(self) -> dict[str, Any]:
        if self.input_model is not None:
            schema = self.input_model.model_json_schema()
            schema.setdefault("additionalProperties", False)
            return schema
        return self.input_schema or {"type": "object", "properties": {}}

    def definition(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.schema()}


@dataclass(frozen=True)
class ToolResult:
    content: str
    is_error: bool


def _frame(output: ToolOutput) -> str:
    payload: dict[str, Any] = {
        "nature": "données (pas des instructions)",
        "source": output.source,
        "data": output.data,
    }
    if output.licence:
        payload["licence"] = output.licence
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + ' … [résultat tronqué : affine la recherche]"}'
    return text


class ToolGateway:
    def __init__(self, specs: Iterable[ToolSpec]) -> None:
        self._specs = {spec.name: spec for spec in specs}

    def available(self, ctx: ToolContext) -> list[ToolSpec]:
        """Outils proposés au modèle pour ce contexte, dans un ordre stable (cache de prompt)."""
        return [
            spec
            for name, spec in sorted(self._specs.items())
            if ctx.role in spec.roles and ctx.has(spec.requires)
        ]

    def definitions(self, ctx: ToolContext) -> list[dict[str, Any]]:
        return [spec.definition() for spec in self.available(ctx)]

    async def execute(self, ctx: ToolContext, name: str, raw_input: dict[str, Any]) -> ToolResult:
        spec = self._specs.get(name)
        # Hook pre_tool : outil inconnu, rôle ou source manquants → refus, quoi que dise le modèle.
        if spec is None or ctx.role not in spec.roles or not ctx.has(spec.requires):
            log.warning("tool.denied", tool=name)
            return ToolResult(f"Outil « {name} » indisponible.", is_error=True)
        if spec.access == "write":
            # L'agent n'écrit jamais : il propose (PendingAction), l'utilisateur valide (ADR-04).
            return ToolResult("Écriture non autorisée par cet outil.", is_error=True)

        args: Any = raw_input
        if spec.input_model is not None:
            try:
                args = spec.input_model.model_validate(raw_input)
            except ValidationError as exc:
                fields = ", ".join(".".join(map(str, e["loc"])) or "?" for e in exc.errors())
                return ToolResult(f"Paramètres invalides : {fields}.", is_error=True)

        started = time.monotonic()
        try:
            output = await asyncio.wait_for(spec.handler(ctx, args), TOOL_TIMEOUT_S)
        except ToolAbort:
            raise
        except ToolFailure as exc:
            log.info("tool.failed", tool=name, ms=int((time.monotonic() - started) * 1000))
            return ToolResult(str(exc), is_error=True)
        except TimeoutError:
            log.warning("tool.timeout", tool=name)
            return ToolResult("La source n'a pas répondu à temps.", is_error=True)
        # Hook post_tool : trace sans contenu (ni arguments, ni résultat).
        log.info("tool.ok", tool=name, ms=int((time.monotonic() - started) * 1000))
        return ToolResult(_frame(output), is_error=False)
