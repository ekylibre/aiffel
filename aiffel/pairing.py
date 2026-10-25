"""Appairage d'un compte de messagerie avec une ferme Ekylibre (architecture §4.1, ADR-07).

1. ``/start`` sur Telegram : un code à usage unique (haché en base) et un lien vers la page web.
2. Sur la page, l'utilisateur donne l'adresse de sa ferme, son email et son mot de passe Ekylibre.
3. Aiffel obtient le ``simple-token`` et ne garde que lui, chiffré ; le mot de passe est oublié.
4. Ferme, utilisateur, adhésion, connexion et secrets sont créés ou mis à jour en une transaction,
   et le compte de messagerie est relié à l'utilisateur.

Un salarié qui se connecte à la même instance rejoint la ferme existante ; une même personne qui se
reconnecte (jeton refusé, nouveau téléphone) retrouve son utilisateur.
"""

import hashlib
import secrets
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiffel.connectors.ekylibre.auth import EkylibreInstance, SimpleTokenAuth
from aiffel.connectors.ekylibre.client import EkylibreClient, EkylibreError
from aiffel.db import audit
from aiffel.db.models import (
    ChannelIdentity,
    Connection,
    Farm,
    Membership,
    PairingCode,
    SourceMap,
    User,
)
from aiffel.db.session import farm_session, global_session
from aiffel.vault import Keyring, SecretStore, SecretValue
from aiffel.vault.crypto import new_wrapped_data_key

PAIRING_MAX_ATTEMPTS = 5
# Objets des liens à usage unique envoyés sur Telegram.
PURPOSE_PAIR = "pair"
PURPOSE_LLM_KEY = "llm_key"
EXPIRED = {
    PURPOSE_PAIR: "Ce lien n'est plus valable. Envoyez /start à Aiffel sur Telegram "
    "pour en recevoir un nouveau.",
    PURPOSE_LLM_KEY: "Ce lien n'est plus valable. Envoyez /modele à Aiffel sur Telegram "
    "pour en recevoir un nouveau.",
}
# Objet d'un lien créé par une extension : message générique quand il a expiré.
EXPIRED_OTHER = "Ce lien n'est plus valable. Redemandez-le à Aiffel sur Telegram."
# Appelé dans la transaction qui crée une ferme (démarrage de l'essai, quand le paiement est actif).
FarmCreatedHook = Callable[[AsyncSession, uuid.UUID], Awaitable[None]]
CONNECTOR = "ekylibre"
# Noms de types de secrets (pas des secrets).
SECRET_TOKEN = "simple_token"  # noqa: S105
SECRET_EMAIL = "account_email"  # noqa: S105

# Sources par défaut d'une ferme raccordée à Ekylibre (F-CON-4).
DEFAULT_SOURCES = {
    "cultivable_zones": (True, False),
    "products": (True, False),
    "interventions": (True, True),
    "activity_productions": (True, False),
}


class PairingError(Exception):
    """Message destiné à l'utilisateur, sans donnée sensible."""


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


@dataclass(frozen=True)
class PairingResult:
    farm_id: uuid.UUID
    farm_name: str
    user_id: uuid.UUID
    identity_id: uuid.UUID
    role: str
    farm_created: bool


@dataclass
class PairingService:
    sessions: async_sessionmaker[AsyncSession]
    keyring: Keyring
    http: httpx.AsyncClient
    ttl: timedelta = timedelta(minutes=15)
    on_farm_created: FarmCreatedHook | None = None

    async def create_code(self, identity_id: uuid.UUID, purpose: str = PURPOSE_PAIR) -> str:
        """Nouveau code pour cette identité et cet objet ; annule les précédents inutilisés."""
        code = secrets.token_urlsafe(24)
        now = datetime.now(UTC)
        async with global_session(self.sessions) as session:
            await session.execute(
                text(
                    "UPDATE aiffel.pairing_codes SET used_at = :now "
                    "WHERE channel_identity_id = :i AND purpose = :p AND used_at IS NULL"
                ),
                {"now": now, "i": identity_id, "p": purpose},
            )
            session.add(
                PairingCode(
                    code_hash=_hash_code(code),
                    channel_identity_id=identity_id,
                    expires_at=now + self.ttl,
                    purpose=purpose,
                )
            )
        return code

    async def check_code(self, code: str, purpose: str = PURPOSE_PAIR) -> uuid.UUID:
        """Identité d'un code encore valide pour cet objet ; sinon :class:`PairingError`."""
        async with global_session(self.sessions) as session:
            row = await session.get(PairingCode, _hash_code(code))
            if (
                row is None
                or row.purpose != purpose
                or row.used_at is not None
                or row.expires_at <= datetime.now(UTC)
                or row.attempts >= PAIRING_MAX_ATTEMPTS
            ):
                raise PairingError(EXPIRED.get(purpose, EXPIRED_OTHER))
            return row.channel_identity_id

    async def use_code(self, code: str, purpose: str) -> uuid.UUID:
        """Consomme un code valide (une seule fois, même en cas de requêtes simultanées)."""
        identity_id = await self.check_code(code, purpose)
        async with global_session(self.sessions) as session:
            row = await session.get(PairingCode, _hash_code(code), with_for_update=True)
            if row is None or row.used_at is not None:
                raise PairingError(EXPIRED.get(purpose, EXPIRED_OTHER))
            row.used_at = datetime.now(UTC)
        return identity_id

    async def user_of(self, identity_id: uuid.UUID) -> uuid.UUID | None:
        """Utilisateur relié à une identité de messagerie (``None`` si elle n'est pas appairée)."""
        async with global_session(self.sessions) as session:
            identity = await session.get(ChannelIdentity, identity_id)
            return identity.user_id if identity else None

    async def record_failure(self, code: str) -> None:
        async with global_session(self.sessions) as session:
            await session.execute(
                text(
                    "UPDATE aiffel.pairing_codes SET attempts = attempts + 1 WHERE code_hash = :h"
                ),
                {"h": _hash_code(code)},
            )

    async def complete(
        self, code: str, instance: EkylibreInstance, email: str, password: SecretValue
    ) -> PairingResult:
        identity_id = await self.check_code(code)
        auth = SimpleTokenAuth()
        credentials = await auth.login(self.http, instance, email, password)
        client = EkylibreClient(self.http, auth, instance, credentials)
        try:
            me = await client.me()
            profile = await client.profile()
        except EkylibreError:
            raise PairingError(
                "Connexion acceptée, mais impossible de lire votre profil Ekylibre."
            ) from None

        account_id = str(me.get("id"))
        role = "owner" if profile.get("administrator") else "worker"
        display_name = str(me.get("full_name") or "Utilisateur")[:200]

        async with global_session(self.sessions) as session:
            existing = await session.scalar(
                text("SELECT aiffel.farm_of_instance(:c, :u, :t)"),
                {"c": CONNECTOR, "u": instance.base_url, "t": instance.tenant},
            )
        farm_id: uuid.UUID = existing or uuid.uuid4()

        async with farm_session(self.sessions, farm_id) as session:
            # Les tables globales (codes, identités, utilisateurs) restent accessibles ici :
            # tout l'appairage tient en une transaction.
            pairing = await session.get(PairingCode, _hash_code(code), with_for_update=True)
            if pairing is None or pairing.used_at is not None or pairing.purpose != PURPOSE_PAIR:
                raise PairingError("Ce lien vient d'être utilisé. Envoyez /start pour recommencer.")
            pairing.used_at = datetime.now(UTC)

            farm = await session.get(Farm, farm_id)
            farm_created = farm is None
            if farm is None:
                farm = Farm(
                    id=farm_id,
                    name=instance.tenant.replace("-", " ").title(),
                    data_key_enc=new_wrapped_data_key(self.keyring, farm_id),
                )
                session.add(farm)
                await session.flush()
                if self.on_farm_created is not None:
                    await self.on_farm_created(session, farm_id)

            identity = await session.get(ChannelIdentity, identity_id)
            if identity is None:  # impossible : clé étrangère du code ; défense en profondeur
                raise PairingError("Compte de messagerie introuvable. Envoyez /start.")
            user_id = await self._find_user(session, identity, account_id)
            if user_id is None:
                user = User(display_name=display_name, locale=str(me.get("locale") or "fr")[:10])
                session.add(user)
                await session.flush()
                user_id = user.id
            identity.user_id, identity.paired_at = user_id, datetime.now(UTC)

            await self._upsert_membership(session, farm_id, user_id, role)
            connection = await self._upsert_connection(
                session, farm_id, user_id, instance, account_id
            )
            store = SecretStore(self.keyring)
            await store.put(session, farm_id, connection.id, SECRET_TOKEN, credentials.token)
            await store.put(session, farm_id, connection.id, SECRET_EMAIL, credentials.email)
            await self._default_sources(session, farm_id, connection.id)
            await audit.append(
                session,
                farm_id,
                "connection.paired",
                actor_user_id=user_id,
                target=f"{CONNECTOR}:{instance.tenant}",
                details={"role": role, "farm_created": farm_created},
            )
            farm_name = farm.name

        return PairingResult(farm_id, farm_name, user_id, identity_id, role, farm_created)

    @staticmethod
    async def _find_user(
        session: AsyncSession, identity: ChannelIdentity, account_id: str
    ) -> uuid.UUID | None:
        if identity.user_id is not None:
            return identity.user_id
        return await session.scalar(
            select(Connection.user_id).where(
                Connection.connector == CONNECTOR, Connection.external_account_id == account_id
            )
        )

    @staticmethod
    async def _upsert_membership(
        session: AsyncSession, farm_id: uuid.UUID, user_id: uuid.UUID, role: str
    ) -> None:
        membership = await session.scalar(
            select(Membership).where(Membership.farm_id == farm_id, Membership.user_id == user_id)
        )
        if membership is None:
            session.add(
                Membership(
                    farm_id=farm_id, user_id=user_id, role=role, can_validate=role == "owner"
                )
            )
        else:
            membership.role = role
        await session.flush()

    @staticmethod
    async def _upsert_connection(
        session: AsyncSession,
        farm_id: uuid.UUID,
        user_id: uuid.UUID,
        instance: EkylibreInstance,
        account_id: str,
    ) -> Connection:
        connection = await session.scalar(
            select(Connection).where(
                Connection.connector == CONNECTOR,
                Connection.user_id == user_id,
                Connection.base_url == instance.base_url,
            )
        )
        if connection is None:
            connection = Connection(
                farm_id=farm_id,
                user_id=user_id,
                connector=CONNECTOR,
                base_url=instance.base_url,
                tenant=instance.tenant,
                external_account_id=account_id,
            )
            session.add(connection)
        else:
            connection.status, connection.external_account_id = "active", account_id
        await session.flush()
        return connection

    @staticmethod
    async def _default_sources(
        session: AsyncSession, farm_id: uuid.UUID, connection_id: uuid.UUID
    ) -> None:
        known = set(
            await session.scalars(select(SourceMap.data_type).where(SourceMap.farm_id == farm_id))
        )
        for data_type, (can_read, can_write) in DEFAULT_SOURCES.items():
            if data_type not in known:
                session.add(
                    SourceMap(
                        farm_id=farm_id,
                        data_type=data_type,
                        connection_id=connection_id,
                        can_read=can_read,
                        can_write=can_write,
                    )
                )
        await session.flush()
