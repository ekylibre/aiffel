"""Client de l'API Pl@ntNet (https://my.plantnet.org), en lecture seule.

Deux identifications à partir de photos : les maladies (``/v2/diseases/identify``) et les
espèces végétales (``/v2/identify/all``). Les photos sont envoyées depuis la mémoire, jamais
écrites sur disque. La clé de la plateforme passe en paramètre ``api-key`` : elle n'apparaît ni
dans les messages d'erreur, ni dans les journaux (les erreurs réseau ne gardent que leur type).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from aiffel.vault import SecretValue

log = structlog.get_logger()

BASE_URL = "https://my-api.plantnet.org"
MAX_IMAGES = 5
MAX_RESULTS = 5
SOURCE = "Pl@ntNet (my.plantnet.org)"

NO_RESULT = (
    "Pl@ntNet n'a rien reconnu sur cette photo. Une photo plus nette, de plus près, de la feuille "
    "ou de l'organe atteint, aidera."
)
QUOTA = "Le quota d'identifications Pl@ntNet est atteint pour aujourd'hui. Réessayez demain."
UNAVAILABLE = "Pl@ntNet est indisponible pour le moment. Réessayez plus tard."
REJECTED = "Pl@ntNet n'accepte pas cette image (format ou taille)."


class PlantNetError(Exception):
    """Échec d'une identification ; le message s'adresse à l'utilisateur."""


@dataclass(frozen=True)
class Match:
    name: str
    score: float
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"nom": self.name, "score": round(self.score, 3), **self.details}


def _diseases(body: dict[str, Any]) -> list[Match]:
    matches = []
    for row in body.get("results") or []:
        # « label » (nom lisible) dans les réponses observées ; « name » est le code EPPO.
        name = row.get("label") or row.get("description") or row.get("name")
        if not name:
            continue
        details = {"code": row["name"]} if row.get("name") and row["name"] != name else {}
        matches.append(Match(str(name), float(row.get("score") or 0.0), details))
    return matches


def _species(body: dict[str, Any]) -> list[Match]:
    matches = []
    for row in body.get("results") or []:
        species = row.get("species") or {}
        name = species.get("scientificNameWithoutAuthor")
        if not name:
            continue
        details: dict[str, Any] = {
            "noms_communs": (species.get("commonNames") or [])[:3],
            "famille": (species.get("family") or {}).get("scientificNameWithoutAuthor"),
        }
        matches.append(Match(str(name), float(row.get("score") or 0.0), details))
    return matches


class PlantNetClient:
    def __init__(
        self,
        api_key: SecretValue,
        *,
        base_url: str = BASE_URL,
        lang: str = "fr",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._key = api_key
        self._lang = lang
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=30.0, follow_redirects=False, transport=transport
        )

    async def identify_disease(self, images: list[bytes]) -> list[Match]:
        return _diseases(await self._identify("/v2/diseases/identify", images))

    async def identify_species(self, images: list[bytes]) -> list[Match]:
        return _species(await self._identify("/v2/identify/all", images))

    async def _identify(self, path: str, images: list[bytes]) -> dict[str, Any]:
        if not images:
            raise PlantNetError("Aucune photo à analyser.")
        images = images[:MAX_IMAGES]
        files = [("images", (f"photo{i}.jpg", data, "image/jpeg")) for i, data in enumerate(images)]
        # Organe laissé à Pl@ntNet (« auto ») : l'utilisateur ne le précise pas.
        organs = {"organs": ["auto"] * len(images)}
        params: dict[str, str | int] = {
            "api-key": self._key.reveal(),
            "lang": self._lang,
            "nb-results": MAX_RESULTS,
        }
        try:
            response = await self._client.post(path, params=params, data=organs, files=files)
        except httpx.HTTPError as exc:
            log.warning("plantnet.network_error", error=type(exc).__name__)
            raise PlantNetError(UNAVAILABLE) from None
        if response.status_code == 404:
            return {}
        if response.status_code == 429:
            raise PlantNetError(QUOTA)
        if response.status_code in (400, 413, 415):
            raise PlantNetError(REJECTED)
        if response.status_code != 200:
            log.warning("plantnet.http_error", status=response.status_code)
            raise PlantNetError(UNAVAILABLE)
        body: dict[str, Any] = response.json()
        if (remaining := body.get("remainingIdentificationRequests")) is not None:
            log.info("plantnet.identified", remaining=remaining)
        return body

    async def aclose(self) -> None:
        await self._client.aclose()
