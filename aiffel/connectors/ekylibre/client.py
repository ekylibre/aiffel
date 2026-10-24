"""Client REST de l'API v2 d'Ekylibre, en lecture (les écritures passent par A5, après validation).

Nouvelles tentatives sur les lectures uniquement ; un ``401`` lève :class:`EkylibreUnauthorized`
pour que la connexion passe en « à reconnecter ».
"""

import asyncio
from typing import Any

import httpx

from aiffel.connectors.ekylibre.auth import EkylibreAuth, EkylibreCredentials, EkylibreInstance


class EkylibreError(Exception):
    pass


class EkylibreUnauthorized(EkylibreError):
    """Jeton refusé (déconnexion depuis un autre client, mot de passe changé…)."""


READ_RETRIES = 2


class EkylibreClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        auth: EkylibreAuth,
        instance: EkylibreInstance,
        credentials: EkylibreCredentials,
    ) -> None:
        self._http = http
        self._auth = auth
        self.instance = instance
        self._credentials = credentials

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.instance.base_url}/api/v2/{path.lstrip('/')}"
        headers = {
            **self._auth.headers(self.instance, self._credentials),
            "Accept": "application/json",
        }
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in range(READ_RETRIES + 1):
            try:
                response = await self._http.get(url, params=clean, headers=headers)
            except httpx.TransportError:
                if attempt == READ_RETRIES:
                    raise EkylibreError("Ekylibre est injoignable.") from None
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            if response.status_code == 401:
                raise EkylibreUnauthorized("Le jeton Ekylibre a été refusé.")
            if response.status_code >= 500 and attempt < READ_RETRIES:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            if response.status_code >= 400:
                raise EkylibreError(f"GET {path} : {response.status_code}")
            return response.json()
        raise EkylibreError(f"GET {path} : échec")  # pragma: no cover

    # --- Lectures existantes ---

    async def me(self) -> dict[str, Any]:
        return dict(await self.get("users/me"))

    async def profile(self) -> dict[str, Any]:
        return dict(await self.get("profile"))

    async def cultivable_zones(self) -> list[dict[str, Any]]:
        zones = await self.get("cultivable_zones")
        # Les géométries sont volumineuses et inutiles pour la conversation.
        return [{k: z.get(k) for k in ("id", "uuid", "work_number", "name")} for z in zones or []]

    async def products(
        self, product_type: str | None = None, **params: Any
    ) -> list[dict[str, Any]]:
        path = f"products/{product_type}" if product_type else "products"
        # Les plantes et parcelles portent leur contour (GeoJSON et SVG) : des dizaines de milliers
        # de jetons par appel, sans utilité pour la conversation.
        return [
            {k: v for k, v in p.items() if not k.startswith("shape")}
            for p in await self.get(path, params) or []
        ]

    async def procedures(self) -> list[dict[str, Any]]:
        return list(await self.get("procedures"))

    async def procedure(self, name: str) -> dict[str, Any]:
        return dict(await self.get(f"procedures/{name}"))

    async def farm_profile(self, harvest_year: int) -> dict[str, Any]:
        return dict(await self.get(f"farm_profiles/{harvest_year}"))

    # --- Lectures ajoutées pour Aiffel (PR ekylibre#2701) ---

    async def intervention_records(self, **params: Any) -> dict[str, Any]:
        return dict(await self.get("intervention_records", params))

    async def activity_productions(self, campaign: int, **params: Any) -> dict[str, Any]:
        return dict(await self.get("activity_productions", {"campaign": campaign, **params}))

    async def input_totals(self, campaign: int, **params: Any) -> dict[str, Any]:
        return dict(await self.get("input_totals", {"campaign": campaign, **params}))

    # --- Écriture : appelée uniquement par l'exécution d'une PendingAction validée (ADR-04) ---

    async def create_intervention(self, payload: dict[str, Any]) -> int:
        """``POST /api/v2/interventions`` ; renvoie l'identifiant créé (ou existant).

        Sûr à rejouer : ``provider.id`` (l'identifiant de la PendingAction) rend l'appel idempotent
        côté Ekylibre, d'où une seule nouvelle tentative en cas d'erreur réseau.
        """
        url = f"{self.instance.base_url}/api/v2/interventions"
        headers = {
            **self._auth.headers(self.instance, self._credentials),
            "Accept": "application/json",
        }
        for attempt in range(2):
            try:
                response = await self._http.post(url, json=payload, headers=headers)
            except httpx.TransportError:
                if attempt == 1:
                    raise EkylibreError("Ekylibre est injoignable.") from None
                await asyncio.sleep(1.0)
                continue
            if response.status_code == 401:
                raise EkylibreUnauthorized("Le jeton Ekylibre a été refusé.")
            if response.status_code in (200, 201):
                return int(response.json()["id"])
            try:
                errors = response.json().get("errors")
            except ValueError:
                errors = None
            detail = "; ".join(map(str, errors))[:300] if isinstance(errors, list) else ""
            raise EkylibreError(
                f"Ekylibre a refusé l'intervention ({response.status_code}) {detail}".strip()
            )
        raise EkylibreError("échec de l'enregistrement")  # pragma: no cover
