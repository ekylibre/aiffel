"""Authentification auprès d'Ekylibre (ADR-07).

``EkylibreAuth`` isole le mécanisme : ``SimpleTokenAuth`` pendant le prototype, un jeton Keycloak
ensuite, sans toucher aux outils. Règles du ``simple-token`` :

- le mot de passe ne sert qu'à obtenir le jeton, puis il est oublié (jamais stocké ni journalisé) ;
- le jeton est **partagé** avec zero-mobile et Duke : Aiffel n'appelle jamais ``DELETE /tokens``.
"""

from dataclasses import dataclass
from typing import Protocol

import httpx

from aiffel.vault import SecretValue


class EkylibreAuthError(Exception):
    """Échec d'authentification ; le message ne contient ni identifiant ni mot de passe."""


@dataclass(frozen=True)
class EkylibreInstance:
    base_url: str  # https://demo.ekylibre-dev.com
    tenant: str  # demo


@dataclass(frozen=True)
class EkylibreCredentials:
    email: SecretValue
    token: SecretValue


class EkylibreAuth(Protocol):
    def headers(
        self, instance: EkylibreInstance, credentials: EkylibreCredentials
    ) -> dict[str, str]:
        """En-têtes d'authentification pour un appel API."""
        ...


class SimpleTokenAuth:
    def headers(
        self, instance: EkylibreInstance, credentials: EkylibreCredentials
    ) -> dict[str, str]:
        email, token = credentials.email.reveal(), credentials.token.reveal()
        return {
            "Authorization": f"simple-token {email} {token}",
            "X-Tenant": instance.tenant,
        }

    async def login(
        self,
        client: httpx.AsyncClient,
        instance: EkylibreInstance,
        email: str,
        password: SecretValue,
    ) -> EkylibreCredentials:
        """Échange email et mot de passe contre le ``simple-token`` de l'utilisateur."""
        try:
            response = await client.post(
                f"{instance.base_url}/api/v2/tokens",
                json={"email": email, "password": password.reveal()},
                headers={"X-Tenant": instance.tenant, "Accept": "application/json"},
            )
        except httpx.HTTPError:
            raise EkylibreAuthError("Ekylibre est injoignable.") from None
        if response.status_code == 401:
            raise EkylibreAuthError("Email ou mot de passe incorrect.")
        if response.status_code == 404:
            raise EkylibreAuthError("Cette ferme n'existe pas sur ce serveur Ekylibre.")
        if response.status_code != 200:
            raise EkylibreAuthError(f"Ekylibre a refusé la connexion ({response.status_code}).")
        try:
            token = str(response.json()["token"])
        except (ValueError, KeyError, TypeError):
            raise EkylibreAuthError("Réponse inattendue d'Ekylibre.") from None
        return EkylibreCredentials(email=SecretValue(email), token=SecretValue(token))
