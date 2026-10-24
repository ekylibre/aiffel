"""Faux serveur Ekylibre (API v2) pour les tests, branché sur ``httpx.MockTransport``."""

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

PROCEDURES: dict[str, dict[str, Any]] = {
    "spraying": {
        "name": "spraying",
        "human_name": "Pulvérisation",
        "parameters": [
            {"name": "cultivation", "type": "target", "handlers": []},
            {
                "name": "plant_medicine",
                "type": "input",
                "handlers": [
                    {"name": "population"},
                    {"name": "net_volume", "indicator": "net_volume", "unit": "liter"},
                    {"name": "volume_area_density", "indicator": "volume_area_density"},
                ],
            },
            {"name": "sprayer", "type": "tool", "handlers": []},
            {"name": "driver", "type": "doer", "handlers": []},
        ],
    },
    "fertilizing": {
        "name": "fertilizing",
        "human_name": "Fertilisation",
        "parameters": [
            {"name": "cultivation", "type": "target", "handlers": []},
            {
                "name": "fertilizer",
                "type": "input",
                "handlers": [{"name": "net_mass"}, {"name": "mass_area_density"}],
            },
        ],
    },
}


@dataclass
class FakeAccount:
    id: int
    email: str
    password: str
    full_name: str
    administrator: bool
    token: str


@dataclass
class FakeEkylibre:
    """Une instance, un tenant ; ``revoked`` simule une déconnexion depuis un autre appareil."""

    host: str = "demo.ekylibre-dev.com"
    tenant: str = "demo"
    accounts: list[FakeAccount] = field(
        default_factory=lambda: [
            FakeAccount(1, "chef@demo.fr", "motdepasse-chef", "Jeanne Chef", True, "TOK-CHEF"),
            FakeAccount(2, "sal@demo.fr", "motdepasse-sal", "Paul Salarié", False, "TOK-SAL"),
        ]
    )
    zones: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"id": 1, "uuid": "u1", "work_number": "P12", "name": "Grands Champs", "shape": "…"},
            {"id": 2, "uuid": "u2", "work_number": "P03", "name": "La Noue", "shape": "…"},
        ]
    )
    revoked: set[str] = field(default_factory=set)
    requests: list[httpx.Request] = field(default_factory=list)
    products: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {
            "LandParcel": [{"id": 10, "name": "Grands Champs (sol)"}],
            "Plant": [
                {
                    "id": 11,
                    "name": "Maïs Grands Champs 2026",
                    "shape_geojson": {"type": "MultiPolygon"},
                    "shape_svg": "<path/>",
                }
            ],
            "Matter": [
                {"id": 20, "name": "ELUMIS"},
                {"id": 21, "name": "Ammonitrate 33,5"},
            ],
            "Equipment": [{"id": 30, "name": "Pulvérisateur 24 m"}],
            "Worker": [{"id": 40, "name": "Jeanne Chef"}],
        }
    )
    # Interventions créées, avec idempotence sur (vendor, name, id) du provider comme Ekylibre.
    interventions: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)
    create_status: int = 201

    def _account(self, request: httpx.Request) -> FakeAccount | None:
        parts = request.headers.get("authorization", "").split(" ")
        if len(parts) != 3 or parts[0] != "simple-token":
            return None
        for account in self.accounts:
            if (account.email, account.token) == (parts[1], parts[2]):
                return None if account.token in self.revoked else account
        return None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host != self.host or request.headers.get("x-tenant") != self.tenant:
            return httpx.Response(404, json={"message": "Not found"})
        path = request.url.path
        if path == "/api/v2/tokens" and request.method == "POST":
            body = json.loads(request.content)
            for account in self.accounts:
                if (account.email, account.password) == (body.get("email"), body.get("password")):
                    return httpx.Response(200, json={"token": account.token})
            return httpx.Response(401, json={"message": "Invalid email or password."})

        account = self._account(request)
        if account is None:
            return httpx.Response(401, json={"message": "Unauthorized."})
        if path == "/api/v2/users/me":
            return httpx.Response(
                200,
                json={
                    "id": account.id,
                    "email": account.email,
                    "full_name": account.full_name,
                    "locale": "fra",
                    "role": None,
                },
            )
        if path == "/api/v2/profile":
            return httpx.Response(200, json={"administrator": account.administrator})
        if path == "/api/v2/cultivable_zones":
            return httpx.Response(200, json=self.zones)
        if path.startswith("/api/v2/products/"):
            return httpx.Response(200, json=self.products.get(path.rsplit("/", 1)[1], []))
        if path.startswith("/api/v2/procedures/"):
            name = path.rsplit("/", 1)[1]
            if name not in PROCEDURES:
                return httpx.Response(404, json={"errors": ["not found"]})
            return httpx.Response(200, json=PROCEDURES[name])
        if path == "/api/v2/procedures":
            return httpx.Response(
                200,
                json=[{"name": n, "human_name": p["human_name"]} for n, p in PROCEDURES.items()],
            )
        if path == "/api/v2/interventions" and request.method == "POST":
            if self.create_status >= 400:
                return httpx.Response(self.create_status, json={"errors": ["Cible invalide"]})
            body = json.loads(request.content)
            provider = body.get("provider") or {}
            key = (provider.get("vendor"), provider.get("name"), provider.get("id"))
            if key in self.interventions:
                return httpx.Response(200, json={"id": self.interventions[key]["id"]})
            self.interventions[key] = {"id": 1000 + len(self.interventions), "body": body}
            return httpx.Response(201, json={"id": self.interventions[key]["id"]})
        return httpx.Response(404, json={"message": "Not found"})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
