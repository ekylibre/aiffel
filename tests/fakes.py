"""Faux serveur Ekylibre (API v2) pour les tests, branché sur ``httpx.MockTransport``."""

import json
from dataclasses import dataclass, field
from typing import Any

import httpx


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
        return httpx.Response(404, json={"message": "Not found"})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
