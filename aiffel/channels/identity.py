"""Identités de canal : l'identifiant externe (``chat_id``) n'est stocké que haché et chiffré."""

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiffel.db.models import ChannelIdentity
from aiffel.vault import Keyring, SecretValue
from aiffel.vault.crypto import decrypt_global, encrypt_global


def hash_external_id(salt: SecretValue, channel: str, external_id: str) -> str:
    """HMAC-SHA256 avec sel : permet la recherche sans conserver l'identifiant en clair."""
    return hmac.new(
        salt.reveal().encode(), f"{channel}:{external_id}".encode(), hashlib.sha256
    ).hexdigest()


def _purpose(channel: str) -> str:
    return f"channel-id:{channel}"


@dataclass(frozen=True)
class IdentityService:
    salt: SecretValue
    keyring: Keyring

    async def resolve(
        self, session: AsyncSession, channel: str, external_id: str
    ) -> ChannelIdentity:
        """Retrouve l'identité, ou la crée (non appairée) au premier message."""
        digest = hash_external_id(self.salt, channel, external_id)
        identity = await session.scalar(
            select(ChannelIdentity).where(
                ChannelIdentity.channel == channel, ChannelIdentity.external_id_hash == digest
            )
        )
        if identity is None:
            identity = ChannelIdentity(
                channel=channel,
                external_id_hash=digest,
                external_id_enc=encrypt_global(self.keyring, _purpose(channel), external_id),
            )
            session.add(identity)
            await session.flush()
        return identity

    def external_id(self, identity: ChannelIdentity) -> str | None:
        """Identifiant en clair, pour un envoi proactif ; ``None`` s'il n'a pas été conservé."""
        if identity.external_id_enc is None:
            return None
        return decrypt_global(self.keyring, _purpose(identity.channel), identity.external_id_enc)

    @staticmethod
    def mark_paired(identity: ChannelIdentity, user_id: uuid.UUID) -> None:
        identity.user_id = user_id
        identity.paired_at = datetime.now(UTC)
