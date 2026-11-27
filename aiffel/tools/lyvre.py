"""Outils Lyvre de l'agent : lecture de la comptabilité de la ferme (T4, ADR-10).

Un outil ``lyvre_<nom>`` par outil de **lecture** de Lyvre (instantané
``connectors/lyvre/tool_schemas.json``), proposé seulement :

- aux exploitants (données comptables et de paie) ;
- si la ferme a une connexion Lyvre active ;
- si la clé lit le domaine de l'outil (Comptabilité, Banque, Achats…).

Aucun outil d'écriture de Lyvre n'existe ici (test bloquant
``tests/unit/test_lyvre_tools.py::test_no_lyvre_write_tool``).
"""

from __future__ import annotations

from typing import Any

from aiffel.connectors.lyvre.client import (
    DOMAIN_LABELS,
    TOOL_SCHEMAS,
    LyvreAuthError,
    LyvreError,
)
from aiffel.tools.registry import ToolContext, ToolFailure, ToolOutput, ToolSpec

PREFIX = "lyvre_"
LYVRE_REFUSED = (
    "La clé Lyvre a été refusée : la connexion est à refaire. N'appelle plus Lyvre pendant ce "
    "tour ; réponds sans inventer de chiffres comptables."
)


def _source(domain: str) -> str:
    label = DOMAIN_LABELS.get(domain)
    return f"Lyvre ({label})" if label else "Lyvre"


def _handler(remote_name: str, domain: str) -> Any:
    async def handler(ctx: ToolContext, args: dict[str, Any]) -> ToolOutput:
        if ctx.lyvre is None or ctx.lyvre_refused:
            raise ToolFailure("Lyvre n'est pas disponible pour ce tour.")
        try:
            data = await ctx.lyvre.call(remote_name, args)
        except LyvreAuthError:
            ctx.lyvre_refused = True
            raise ToolFailure(LYVRE_REFUSED) from None
        except LyvreError as exc:
            raise ToolFailure(str(exc)) from None
        return ToolOutput(data, _source(domain))

    return handler


def lyvre_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name=f"{PREFIX}{name}",
            description=f"Lyvre (comptabilité) — {spec['title']} : {spec['description']}",
            input_schema=spec["input_schema"],
            handler=_handler(name, spec["domain"]),
            roles=frozenset({"owner"}),
            requires="lyvre",
            domain=spec["domain"],
        )
        for name, spec in sorted(TOOL_SCHEMAS.items())
    ]
