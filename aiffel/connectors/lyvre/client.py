"""Client de Lyvre (comptabilité, paie) en lecture seule (T4, ADR-10).

Lyvre ouvre une structure à un agent par une **clé d'API** (``lyv_…``) rattachée à un utilisateur
et limitée à des domaines (Comptabilité, Banque, Achats…). Aiffel appelle les outils d'agent de
Lyvre par REST (``GET /api/v1/agent/tools``, ``POST /api/v1/agent/tools/<outil>``), le même
registre que son serveur MCP. Règles :

- **Lecture seule** : seuls les outils de ``tool_schemas.json`` (instantané des outils de lecture
  de Lyvre) peuvent être appelés ; tout autre nom est refusé ici, avant le réseau.
- La clé n'apparaît que dans l'en-tête ``Authorization`` : jamais dans un journal, une URL ou un
  message d'erreur. Les journaux ne portent que l'outil, le statut et la durée.
- Erreurs : 401 → :class:`LyvreAuthError` (clé révoquée ou expirée : connexion à refaire) ; 403,
  404, 422, 429 → :class:`LyvreError` avec le message de Lyvre (texte destiné à l'utilisateur).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import structlog

from aiffel.vault import SecretValue

log = structlog.get_logger()

TOOL_SCHEMAS: dict[str, dict[str, Any]] = json.loads(
    (Path(__file__).resolve().parent / "tool_schemas.json").read_text(encoding="utf-8")
)
READ_TOOLS = frozenset(TOOL_SCHEMAS)
# Domaine des outils toujours ouverts (contexte du dossier, calculettes).
ALWAYS = "*"
TIMEOUT = httpx.Timeout(20.0, connect=5.0)
DOMAIN_LABELS = {
    "accounting": "Comptabilité",
    "bank": "Banque",
    "purchases": "Achats",
    "sales": "Ventes",
    "parties": "Tiers",
    "tax": "TVA et fiscal",
    "assets": "Immobilisations et stocks",
    "agronomy": "Agronomie",
    "agenda": "Échéancier",
    "documents": "Documents",
}


class LyvreError(Exception):
    """Refus ou panne ; le message peut être montré à l'utilisateur (aucun secret)."""


class LyvreAuthError(LyvreError):
    """Clé refusée (révoquée, expirée, utilisateur sans accès) : connexion à refaire."""


@dataclass(frozen=True)
class LyvreAccount:
    """Ce que la clé ouvre, relevé à la connexion."""

    structure: str
    reads: frozenset[str]
    writes: frozenset[str]


def parse_base_url(raw: str, allowed_domains: tuple[str, ...]) -> str:
    """Adresse de l'installation Lyvre, en HTTPS et sous un domaine autorisé (pas de SSRF)."""
    value = raw.strip().rstrip("/")
    if value and "://" not in value:
        value = f"https://{value}"
    parts = urlsplit(value)
    host = (parts.hostname or "").lower()
    allowed = any(host == d or host.endswith(f".{d}") for d in allowed_domains)
    if parts.scheme != "https" or not host or parts.port or parts.query or not allowed:
        raise LyvreError(
            "Adresse Lyvre non reconnue : une adresse https sous "
            f"{', '.join(allowed_domains)} est attendue (par exemple https://app.lyvre.org)."
        )
    return f"https://{host}{parts.path.rstrip('/')}"


class LyvreClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, api_key: SecretValue) -> None:
        self._http = http
        self.base_url = base_url.rstrip("/")
        self._key = api_key

    async def account(self) -> LyvreAccount:
        """Vérifie la clé et relève ses domaines et le nom de la structure."""
        listing = await self._request("GET", "/api/v1/agent/tools", "tools/list")
        reads: set[str] = set()
        writes: set[str] = set()
        for item in listing.get("items") or []:
            domain = item.get("domain")
            if not isinstance(domain, str) or domain == ALWAYS:
                continue
            (writes if item.get("access") == "write" else reads).add(domain)
        context = await self.call("get_context", {})
        structure = (context.get("structure") or {}).get("name") or "structure sans nom"
        return LyvreAccount(str(structure)[:100], frozenset(reads), frozenset(writes))

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Résultat d'un outil de lecture de Lyvre."""
        if name not in READ_TOOLS:
            # Défense en profondeur : aucun chemin d'écriture, même si la clé en a le droit.
            log.warning("lyvre.write_refused", tool=name)
            raise LyvreError(f"Outil Lyvre « {name} » non autorisé : Aiffel lit seulement.")
        body = await self._request("POST", f"/api/v1/agent/tools/{name}", name, json=arguments)
        result = body.get("result")
        if not isinstance(result, dict):
            raise LyvreError("Réponse inattendue de Lyvre.")
        return result

    async def _request(
        self, method: str, path: str, label: str, *, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        started = time.monotonic()
        try:
            response = await self._http.request(
                method,
                f"{self.base_url}{path}",
                json=json,
                headers={
                    "Authorization": f"Bearer {self._key.reveal()}",
                    "Accept": "application/json",
                },
                timeout=TIMEOUT,
            )
        except httpx.TimeoutException:
            log.warning("lyvre.timeout", tool=label)
            raise LyvreError("Lyvre n'a pas répondu à temps.") from None
        except httpx.HTTPError as exc:
            log.warning("lyvre.unreachable", tool=label, error=type(exc).__name__)
            raise LyvreError("Lyvre est injoignable.") from None
        status = response.status_code
        elapsed = int((time.monotonic() - started) * 1000)
        log.info("lyvre.call", tool=label, status=status, ms=elapsed)
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if status == 200 and isinstance(payload, dict):
            return payload
        error = payload.get("error") if isinstance(payload, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        if status == 401:
            raise LyvreAuthError(
                "La clé Lyvre est refusée (révoquée ou expirée). L'exploitant peut en enregistrer "
                "une nouvelle avec /lyvre."
            )
        if status == 429:
            wait = response.headers.get("Retry-After")
            raise LyvreError(
                f"Lyvre limite le nombre de demandes : réessayez dans {wait} s."
                if wait
                else "Lyvre limite le nombre de demandes : réessayez dans une minute."
            )
        if status in (403, 404, 422) and isinstance(message, str):
            raise LyvreError(message[:500])
        raise LyvreError(f"Lyvre a refusé la demande ({status}).")
