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
        return list(await self.get(path, params))

    async def procedures(self) -> list[dict[str, Any]]:
        return list(await self.get("procedures"))

    async def farm_profile(self, harvest_year: int) -> dict[str, Any]:
        return dict(await self.get(f"farm_profiles/{harvest_year}"))

    # --- Lectures ajoutées pour Aiffel (PR ekylibre#2701) ---

    async def intervention_records(self, **params: Any) -> dict[str, Any]:
        return dict(await self.get("intervention_records", params))

    async def activity_productions(self, campaign: int, **params: Any) -> dict[str, Any]:
        return dict(await self.get("activity_productions", {"campaign": campaign, **params}))

    async def input_totals(self, campaign: int, **params: Any) -> dict[str, Any]:
        return dict(await self.get("input_totals", {"campaign": campaign, **params}))
