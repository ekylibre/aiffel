"""Modèle de données d'Aiffel (architecture phase 1, §6), schéma Postgres ``aiffel``.

Les tables portant un ``farm_id`` sont protégées par Row-Level Security (migration initiale) : le
rôle applicatif ne voit que les lignes de la ferme fixée par ``aiffel.farm_id`` dans la transaction.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "aiffel"

# Tables isolées par ferme : RLS activée et accès du rôle applicatif limité à la ferme courante.
FARM_SCOPED_TABLES = (
    "memberships",
    "connections",
    "secrets",
    "source_map",
    "conversations",
    "messages",
    "pending_actions",
    "audit_log",
    "farm_memory",
    "usage_metrics",
)


class Base(DeclarativeBase):
    metadata = MetaData(
        schema=SCHEMA,
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        },
    )


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _farm_fk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("farms.id", ondelete="CASCADE"), index=True)


class Farm(Base):
    __tablename__ = "farms"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    # Clé de données de la ferme, chiffrée par la clé maître (voir aiffel.vault).
    data_key_enc: Mapped[bytes] = mapped_column(LargeBinary)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    created_at: Mapped[datetime] = _created_at()


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    display_name: Mapped[str] = mapped_column(String(200))
    locale: Mapped[str] = mapped_column(String(10), server_default="fr")
    created_at: Mapped[datetime] = _created_at()


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("farm_id", "user_id"),
        CheckConstraint("role IN ('owner', 'worker')", name="role"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    farm_id: Mapped[uuid.UUID] = _farm_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    can_validate: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = _created_at()


class ChannelIdentity(Base):
    __tablename__ = "channel_identities"
    __table_args__ = (UniqueConstraint("channel", "external_id_hash"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    # Nul tant que l'identité n'est pas appairée.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(20))
    # Identifiant externe (chat_id Telegram) haché avec sel : jamais stocké en clair.
    external_id_hash: Mapped[str] = mapped_column(String(64))
    # Identifiant externe chiffré (clé dérivée de la clé maître), pour les messages proactifs.
    external_id_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    paired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class PairingCode(Base):
    __tablename__ = "pairing_codes"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_identities.id", ondelete="CASCADE")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Connection(Base):
    __tablename__ = "connections"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'needs_reconnect', 'revoked')", name="status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    farm_id: Mapped[uuid.UUID] = _farm_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    connector: Mapped[str] = mapped_column(String(50))
    base_url: Mapped[str] = mapped_column(String(500))
    tenant: Mapped[str | None] = mapped_column(String(100))
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), server_default="{}")
    status: Mapped[str] = mapped_column(String(20), server_default="active")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Secret(Base):
    __tablename__ = "secrets"
    __table_args__ = (UniqueConstraint("connection_id", "kind"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    farm_id: Mapped[uuid.UUID] = _farm_fk()
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(50))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = _created_at()
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceMap(Base):
    __tablename__ = "source_map"
    __table_args__ = (UniqueConstraint("farm_id", "data_type"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    farm_id: Mapped[uuid.UUID] = _farm_fk()
    data_type: Mapped[str] = mapped_column(String(50))
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="CASCADE")
    )
    can_read: Mapped[bool] = mapped_column(Boolean, server_default="true")
    can_write: Mapped[bool] = mapped_column(Boolean, server_default="false")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    farm_id: Mapped[uuid.UUID] = _farm_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    channel: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = _created_at()
    last_message_at: Mapped[datetime] = _created_at()


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (CheckConstraint("role IN ('user', 'assistant', 'tool')", name="role"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    farm_id: Mapped[uuid.UUID] = _farm_fk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


PENDING_ACTION_STATUSES = (
    "proposed",
    "validated",
    "executed",
    "failed",
    "cancelled",
    "expired",
    "superseded",
)


class PendingAction(Base):
    __tablename__ = "pending_actions"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in PENDING_ACTION_STATUSES) + ")",
            name="status",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    farm_id: Mapped[uuid.UUID] = _farm_fk()
    proposed_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    validated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    connector: Mapped[str] = mapped_column(String(50))
    kind: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    summary: Mapped[str] = mapped_column(Text)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), server_default="proposed")
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    external_ref: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuditLog(Base):
    """Journal en ajout seul ; ``prev_hash`` et ``hash`` sont calculés par un trigger Postgres."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    farm_id: Mapped[uuid.UUID] = _farm_fk()
    at: Mapped[datetime] = _created_at()
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str | None] = mapped_column(String(200))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    hash: Mapped[str | None] = mapped_column(String(64))


class FarmMemory(Base):
    __tablename__ = "farm_memory"
    __table_args__ = (CheckConstraint("kind IN ('alias', 'habit', 'preference')", name="kind"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    farm_id: Mapped[uuid.UUID] = _farm_fk()
    kind: Mapped[str] = mapped_column(String(20))
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source: Mapped[str | None] = mapped_column(String(100))
    # Un fait n'est retenu qu'une fois confirmé par un utilisateur (F-MEM).
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class UsageMetric(Base):
    __tablename__ = "usage_metrics"

    farm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("farms.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    model: Mapped[str] = mapped_column(String(100), primary_key=True)
    tokens_in: Mapped[int] = mapped_column(BigInteger, server_default="0")
    tokens_out: Mapped[int] = mapped_column(BigInteger, server_default="0")
    stt_seconds: Mapped[int] = mapped_column(Integer, server_default="0")


INBOUND_JOB_STATUSES = ("pending", "processing", "done", "failed")


class InboundJob(Base):
    """File des messages entrants (table Postgres, ``FOR UPDATE SKIP LOCKED``).

    Globale : la ferme n'est connue qu'après résolution de l'identité. Le contenu brut (``payload``)
    est effacé dès le traitement terminé, pour ne pas conserver de message en clair.
    """

    __tablename__ = "inbound_jobs"
    __table_args__ = (
        UniqueConstraint("channel", "external_update_id"),
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in INBOUND_JOB_STATUSES) + ")",
            name="status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(20))
    # Identifiant de l'événement chez le canal (update_id Telegram) : déduplique les renvois.
    external_update_id: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), server_default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = _created_at()
