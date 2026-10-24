"""Coffre d'identifiants d'Aiffel (ADR-06)."""

from aiffel.vault.crypto import Keyring, SecretValue, VaultError, generate_key
from aiffel.vault.store import SecretStore

__all__ = ["Keyring", "SecretStore", "SecretValue", "VaultError", "generate_key"]
