"""Propositions d'écriture et leur exécution validée (ADR-04).

- :meth:`ActionService.propose` est le **seul** point d'entrée de l'agent : il enregistre une
  ``PendingAction`` (charge utile, résumé produit par le code, contrôles, empreinte, expiration).
- :meth:`ActionService.validate` n'est appelé que par le bouton « Valider » (callback Telegram
  traité sans modèle). Il verrouille la proposition, revérifie le droit de valider, l'état,
  l'expiration et l'empreinte, puis écrit dans Ekylibre avec ``provider.id`` = id de la
  proposition, ce qui rend un double clic ou un nouvel essai sans effet.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiffel.connectors.ekylibre.client import EkylibreError, EkylibreUnauthorized
from aiffel.db import audit
from aiffel.db.models import Membership, PendingAction
from aiffel.db.session import farm_session

log = structlog.get_logger()

PROPOSAL_TTL = timedelta(hours=24)
PROVIDER = {"vendor": "aiffel", "name": "assistant"}
CALLBACK_PREFIX = "pa"
HASH_PREFIX_LEN = 8


def payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def callback_data(verb: str, action_id: uuid.UUID, digest: str) -> str:
    """``pa:<v|c|x>:<uuid>:<8 car. d'empreinte>`` : 49 octets, sous la limite Telegram de 64."""
    return f"{CALLBACK_PREFIX}:{verb}:{action_id}:{digest[:HASH_PREFIX_LEN]}"


@dataclass(frozen=True)
class ParsedCallback:
    verb: str
    action_id: uuid.UUID
    digest_prefix: str


def parse_callback(data: str | None) -> ParsedCallback | None:
    parts = (data or "").split(":")
    if len(parts) != 4 or parts[0] != CALLBACK_PREFIX or parts[1] not in ("v", "c", "x"):
        return None
    try:
        return ParsedCallback(parts[1], uuid.UUID(parts[2]), parts[3])
    except ValueError:
        return None


@dataclass(frozen=True)
class Proposal:
    id: uuid.UUID
    digest: str
    summary: str


@dataclass(frozen=True)
class Outcome:
    ok: bool
    text: str
    external_ref: str | None = None


# Écrit dans le logiciel cible ; renvoie la référence créée. Fourni par l'appelant (client du
# validateur), jamais par l'agent.
Executor = Callable[[dict[str, Any]], Awaitable[int]]


@dataclass
class ActionService:
    sessions: async_sessionmaker[AsyncSession]
    ttl: timedelta = PROPOSAL_TTL

    async def propose(
        self,
        farm_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        connector: str,
        kind: str,
        payload: dict[str, Any],
        summary: str,
        checks: list[dict[str, str]],
    ) -> Proposal:
        digest = payload_hash(payload)
        now = datetime.now(UTC)
        async with farm_session(self.sessions, farm_id) as session:
            # Une nouvelle proposition remplace la précédente encore ouverte du même auteur.
            await session.execute(
                update(PendingAction)
                .where(
                    PendingAction.proposed_by == user_id,
                    PendingAction.kind == kind,
                    PendingAction.status == "proposed",
                )
                .values(status="superseded")
            )
            action = PendingAction(
                farm_id=farm_id,
                proposed_by=user_id,
                connector=connector,
                kind=kind,
                payload=payload,
                summary=summary,
                checks=checks,
                payload_hash=digest,
                expires_at=now + self.ttl,
            )
            session.add(action)
            await session.flush()
            await audit.append(
                session,
                farm_id,
                f"{kind}.proposed",
                actor_user_id=user_id,
                target=str(action.id),
                details={"warnings": [c["code"] for c in checks if c["level"] == "warn"]},
            )
            return Proposal(action.id, digest, summary)

    async def open_proposal(self, farm_id: uuid.UUID, user_id: uuid.UUID, kind: str) -> str | None:
        """Résumé de la proposition encore ouverte de l'utilisateur (contexte pour « Corriger »)."""
        async with farm_session(self.sessions, farm_id) as session:
            return await session.scalar(
                select(PendingAction.summary).where(
                    PendingAction.proposed_by == user_id,
                    PendingAction.kind == kind,
                    PendingAction.status == "proposed",
                    PendingAction.expires_at > datetime.now(UTC),
                )
            )

    async def validate(
        self,
        farm_id: uuid.UUID,
        user_id: uuid.UUID,
        callback: ParsedCallback,
        execute: Executor,
    ) -> Outcome:
        async with farm_session(self.sessions, farm_id) as session:
            # Verrou de ligne : un second clic attend la fin du premier puis voit « exécutée ».
            action = await session.get(PendingAction, callback.action_id, with_for_update=True)
            if action is None:  # inexistante, ou d'une autre ferme (RLS)
                return Outcome(False, "Proposition introuvable.")
            if action.status == "executed":
                return Outcome(
                    True,
                    f"Déjà enregistrée dans Ekylibre (n° {action.external_ref}).",
                    action.external_ref,
                )
            if action.status not in ("proposed", "failed"):
                return Outcome(False, _STATUS_MESSAGES.get(action.status, "Proposition close."))
            if action.expires_at <= datetime.now(UTC):
                action.status = "expired"
                return Outcome(False, "Proposition expirée : redemandez-la si besoin.")
            if (
                not action.payload_hash.startswith(callback.digest_prefix)
                or payload_hash(action.payload) != action.payload_hash
            ):
                log.warning("action.hash_mismatch", action_id=str(action.id))
                return Outcome(
                    False,
                    "Cette proposition a changé depuis l'affichage : utilisez la plus récente.",
                )
            if not await self._can_validate(session, farm_id, user_id):
                await audit.append(
                    session,
                    farm_id,
                    f"{action.kind}.validation_denied",
                    actor_user_id=user_id,
                    target=str(action.id),
                )
                return Outcome(
                    False,
                    "Vous n'avez pas le droit de valider. "
                    "L'exploitant doit valider cette proposition.",
                )

            body = {**action.payload, "provider": {**PROVIDER, "id": str(action.id)}}
            try:
                external_id = await execute(body)
            except EkylibreUnauthorized:
                action.status = "failed"
                await audit.append(
                    session,
                    farm_id,
                    f"{action.kind}.failed",
                    actor_user_id=user_id,
                    target=str(action.id),
                    details={"reason": "unauthorized"},
                )
                return Outcome(
                    False,
                    "Ekylibre a refusé l'accès : envoyez /start pour vous reconnecter, "
                    "puis validez à nouveau.",
                )
            except EkylibreError as exc:
                action.status = "failed"
                await audit.append(
                    session,
                    farm_id,
                    f"{action.kind}.failed",
                    actor_user_id=user_id,
                    target=str(action.id),
                    details={"reason": str(exc)[:200]},
                )
                return Outcome(False, f"Échec de l'enregistrement : {exc}. Vous pouvez réessayer.")

            action.status, action.validated_by, action.external_ref = (
                "executed",
                user_id,
                str(external_id),
            )
            await audit.append(
                session,
                farm_id,
                f"{action.kind}.executed",
                actor_user_id=user_id,
                target=str(action.id),
                details={
                    "external_ref": str(external_id),
                    "proposed_by": str(action.proposed_by),
                    "payload_hash": action.payload_hash,
                },
            )
            return Outcome(
                True, f"Enregistrée dans Ekylibre ✅ (n° {external_id}).", str(external_id)
            )

    async def cancel(
        self, farm_id: uuid.UUID, user_id: uuid.UUID, callback: ParsedCallback
    ) -> Outcome:
        async with farm_session(self.sessions, farm_id) as session:
            action = await session.get(PendingAction, callback.action_id, with_for_update=True)
            if action is None:
                return Outcome(False, "Proposition introuvable.")
            if action.status != "proposed":
                return Outcome(False, _STATUS_MESSAGES.get(action.status, "Proposition close."))
            action.status = "cancelled"
            await audit.append(
                session,
                farm_id,
                f"{action.kind}.cancelled",
                actor_user_id=user_id,
                target=str(action.id),
            )
            return Outcome(True, "Proposition annulée. Rien n'a été enregistré.")

    @staticmethod
    async def _can_validate(session: AsyncSession, farm_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        # Relu en base à chaque validation : un droit retiré s'applique tout de suite.
        allowed = await session.scalar(
            select(Membership.can_validate).where(
                Membership.farm_id == farm_id, Membership.user_id == user_id
            )
        )
        return bool(allowed)


_STATUS_MESSAGES = {
    "cancelled": "Cette proposition a été annulée.",
    "superseded": "Cette proposition a été remplacée par une plus récente.",
    "expired": "Proposition expirée : redemandez-la si besoin.",
    "validated": "Validation déjà en cours.",
}
