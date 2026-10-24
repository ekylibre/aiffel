"""Chiffrement d'enveloppe (ADR-06).

- La **clé maître** (``AIFFEL_MASTER_KEY``) ne chiffre que les clés de données des fermes.
- Chaque ferme a sa **clé de données** (AES-256), stockée chiffrée dans ``farms.data_key_enc``.
- Chaque secret est chiffré par la clé de données de sa ferme, avec des données associées (AAD)
  qui lient le chiffré à sa ferme, sa connexion et son type : copié ailleurs, il est illisible.

Format de ``AIFFEL_MASTER_KEY`` : ``<version>:<clé base64url de 32 octets>``, plusieurs entrées
séparées par des virgules pour la rotation (la version la plus haute chiffre ; toutes déchiffrent).
Une clé seule, sans version, vaut la version 1.
"""

from __future__ import annotations

import base64
import os
import uuid
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

KEY_BYTES = 32
NONCE_BYTES = 12


class VaultError(Exception):
    """Erreur du coffre ; le message ne contient jamais de matériel secret."""


class SecretValue:
    """Valeur secrète en clair, masquée dans ``repr``, ``str`` et les logs."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretValue('[masqué]')"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SecretValue) and other._value == self._value

    def __hash__(self) -> int:
        return hash(self._value)


def _decode_key(encoded: str) -> bytes:
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except ValueError as exc:
        raise VaultError("clé maître mal encodée (base64url attendu)") from exc
    if len(raw) != KEY_BYTES:
        raise VaultError(f"clé maître de {len(raw)} octets, {KEY_BYTES} attendus")
    return raw


@dataclass(frozen=True, repr=False)
class Keyring:
    """Clés maîtres par version ; ``current`` sert à chiffrer."""

    keys: dict[int, bytes]
    current: int

    @classmethod
    def parse(cls, spec: str) -> Keyring:
        keys: dict[int, bytes] = {}
        for item in filter(None, (part.strip() for part in spec.split(","))):
            version_text, sep, encoded = item.partition(":")
            if not sep:
                version_text, encoded = "1", item
            if not version_text.isdigit() or not 0 < int(version_text) < 256:
                raise VaultError("version de clé maître invalide (1 à 255)")
            keys[int(version_text)] = _decode_key(encoded)
        if not keys:
            raise VaultError("AIFFEL_MASTER_KEY est vide")
        return cls(keys=keys, current=max(keys))

    def __repr__(self) -> str:
        return f"Keyring(versions={sorted(self.keys)}, current={self.current})"


def generate_key() -> str:
    """Nouvelle clé base64url de 32 octets, pour ``AIFFEL_MASTER_KEY``."""
    return base64.urlsafe_b64encode(os.urandom(KEY_BYTES)).rstrip(b"=").decode()


def _farm_key_aad(farm_id: uuid.UUID) -> bytes:
    return b"aiffel:farm-data-key:" + str(farm_id).encode()


def secret_aad(farm_id: uuid.UUID, connection_id: uuid.UUID, kind: str) -> bytes:
    return f"aiffel:secret:{farm_id}:{connection_id}:{kind}".encode()


def new_wrapped_data_key(keyring: Keyring, farm_id: uuid.UUID) -> bytes:
    """Crée la clé de données d'une ferme et la renvoie chiffrée (version | nonce | chiffré)."""
    return wrap_data_key(keyring, farm_id, AESGCM.generate_key(bit_length=256))


def wrap_data_key(keyring: Keyring, farm_id: uuid.UUID, data_key: bytes) -> bytes:
    nonce = os.urandom(NONCE_BYTES)
    master = AESGCM(keyring.keys[keyring.current])
    return (
        bytes([keyring.current]) + nonce + master.encrypt(nonce, data_key, _farm_key_aad(farm_id))
    )


def unwrap_data_key(keyring: Keyring, farm_id: uuid.UUID, wrapped: bytes) -> bytes:
    if len(wrapped) < 1 + NONCE_BYTES + 16:
        raise VaultError("clé de données chiffrée tronquée")
    version, nonce, ciphertext = (
        wrapped[0],
        wrapped[1 : 1 + NONCE_BYTES],
        wrapped[1 + NONCE_BYTES :],
    )
    if version not in keyring.keys:
        raise VaultError(f"clé maître version {version} absente du trousseau")
    try:
        return AESGCM(keyring.keys[version]).decrypt(nonce, ciphertext, _farm_key_aad(farm_id))
    except InvalidTag as exc:
        raise VaultError(
            "clé de données illisible (mauvaise clé maître ou mauvaise ferme)"
        ) from exc


def rewrap_data_key(keyring: Keyring, farm_id: uuid.UUID, wrapped: bytes) -> bytes:
    """Rechiffre une clé de données avec la version courante (rotation de la clé maître)."""
    return wrap_data_key(keyring, farm_id, unwrap_data_key(keyring, farm_id, wrapped))


def encrypt_secret(data_key: bytes, aad: bytes, value: SecretValue) -> tuple[bytes, bytes]:
    nonce = os.urandom(NONCE_BYTES)
    return AESGCM(data_key).encrypt(nonce, value.reveal().encode(), aad), nonce


def decrypt_secret(data_key: bytes, aad: bytes, ciphertext: bytes, nonce: bytes) -> SecretValue:
    try:
        return SecretValue(AESGCM(data_key).decrypt(nonce, ciphertext, aad).decode())
    except InvalidTag as exc:
        raise VaultError("secret illisible (altéré ou rattaché à une autre ligne)") from exc


# --- Données globales (hors ferme), par exemple les identifiants de canal chiffrés. ---
# Clé dérivée de la clé maître par HKDF, une par version et par usage : la rotation de la clé
# maître s'applique aussi à ces données.


def _derived_key(keyring: Keyring, version: int, purpose: str) -> bytes:
    hkdf = HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=None, info=purpose.encode())
    return hkdf.derive(keyring.keys[version])


def encrypt_global(keyring: Keyring, purpose: str, value: str) -> bytes:
    """Chiffre une donnée hors ferme (format : version | nonce | chiffré)."""
    nonce = os.urandom(NONCE_BYTES)
    key = _derived_key(keyring, keyring.current, purpose)
    aad = purpose.encode()
    return bytes([keyring.current]) + nonce + AESGCM(key).encrypt(nonce, value.encode(), aad)


def decrypt_global(keyring: Keyring, purpose: str, blob: bytes) -> str:
    if len(blob) < 1 + NONCE_BYTES + 16:
        raise VaultError("donnée chiffrée tronquée")
    version, nonce, ciphertext = blob[0], blob[1 : 1 + NONCE_BYTES], blob[1 + NONCE_BYTES :]
    if version not in keyring.keys:
        raise VaultError(f"clé maître version {version} absente du trousseau")
    key = _derived_key(keyring, version, purpose)
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, purpose.encode()).decode()
    except InvalidTag as exc:
        raise VaultError("donnée globale illisible") from exc
