"""Vérification d'une clé d'API d'IA avant de l'enregistrer dans le coffre de la ferme.

Une simple lecture chez le fournisseur (aucune génération, donc aucun coût). Les erreurs ne
reprennent jamais la clé ni la réponse brute du fournisseur.
"""

from __future__ import annotations

import anthropic
import httpx

from aiffel.providers.catalog import ProviderName
from aiffel.vault import SecretValue

OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"


class KeyCheckError(Exception):
    """Fournisseur injoignable : on ne peut conclure ni à une clé valide, ni à une clé refusée."""


async def verify_key(provider: ProviderName, key: SecretValue, http: httpx.AsyncClient) -> bool:
    """``True`` si le fournisseur accepte la clé, ``False`` s'il la refuse."""
    if provider == "openrouter":
        try:
            response = await http.get(
                OPENROUTER_KEY_URL, headers={"Authorization": f"Bearer {key.reveal()}"}
            )
        except httpx.HTTPError as exc:
            raise KeyCheckError(type(exc).__name__) from None
        if response.status_code in (401, 403):
            return False
        if response.status_code >= 400:
            raise KeyCheckError(f"HTTP {response.status_code}")
        return True
    client = anthropic.AsyncAnthropic(api_key=key.reveal(), max_retries=1)
    try:
        await client.models.list(limit=1)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
        return False
    except anthropic.APIError as exc:
        raise KeyCheckError(type(exc).__name__) from None
    finally:
        await client.close()
    return True
