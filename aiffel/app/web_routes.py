"""Mini page web de raccordement à Ekylibre (``/connect``).

Le mot de passe ne vit que le temps de la requête : jamais stocké, journalisé ni renvoyé dans la
page. Le code d'appairage est dans l'URL : la page interdit l'envoi du Referer, et le journal
d'accès d'uvicorn est désactivé dans l'image Docker.
"""

import hmac
import secrets
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from pathlib import Path

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from aiffel.connectors.ekylibre.auth import EkylibreAuthError
from aiffel.connectors.ekylibre.instances import InvalidInstance, parse_instance
from aiffel.pairing import PairingError, PairingResult, PairingService
from aiffel.vault import SecretValue

log = structlog.get_logger()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

CSRF_COOKIE = "aiffel_csrf"
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
    "frame-ancestors 'none'; base-uri 'none'",
}

OnPaired = Callable[[PairingResult], Awaitable[None]]


class RateLimiter:
    """Fenêtre glissante en mémoire, par adresse IP (un seul processus web en phase 1)."""

    def __init__(self, limit: int = 10, window_s: float = 60.0) -> None:
        self.limit, self.window_s = limit, window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window_s:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True


def _page(request: Request, name: str, status: int = 200, **context: object) -> HTMLResponse:
    response = templates.TemplateResponse(request, name, context, status_code=status)
    response.headers.update(SECURITY_HEADERS)
    return response


def web_router(
    pairing: PairingService,
    allowed_domains: tuple[str, ...],
    on_paired: OnPaired,
    *,
    secure_cookies: bool = True,
    limiter: RateLimiter | None = None,
) -> APIRouter:
    router = APIRouter()
    limiter = limiter or RateLimiter()

    def expired(request: Request, message: str) -> HTMLResponse:
        return _page(request, "message.html", 410, title="Lien expiré", message=message)

    def form(
        request: Request,
        code: str,
        *,
        error: str | None = None,
        instance: str = "",
        email: str = "",
        status: int = 200,
    ) -> HTMLResponse:
        csrf = secrets.token_urlsafe(16)
        response = _page(
            request,
            "connect.html",
            status,
            code=code,
            csrf=csrf,
            error=error,
            instance=instance,
            email=email,
        )
        response.set_cookie(
            CSRF_COOKIE,
            csrf,
            httponly=True,
            samesite="strict",
            secure=secure_cookies,
            max_age=1800,
            path="/connect",
        )
        return response

    @router.get("/connect", response_class=HTMLResponse)
    async def connect_form(request: Request, c: str = "") -> HTMLResponse:
        try:
            await pairing.check_code(c)
        except PairingError as exc:
            return expired(request, str(exc))
        return form(request, c)

    @router.post("/connect", response_class=HTMLResponse)
    async def connect_submit(
        request: Request,
        c: str = Form(""),
        csrf: str = Form(""),
        instance: str = Form(""),
        email: str = Form(""),
        password: str = Form(""),
    ) -> HTMLResponse:
        secret = SecretValue(password)
        del password
        client_ip = request.client.host if request.client else "inconnu"
        if not limiter.allow(client_ip):
            return _page(
                request,
                "message.html",
                429,
                title="Trop d'essais",
                message="Patientez une minute avant de réessayer.",
            )
        cookie = request.cookies.get(CSRF_COOKIE, "")
        if not cookie or not hmac.compare_digest(cookie.encode(), csrf.encode()):
            return _page(
                request,
                "message.html",
                400,
                title="Session expirée",
                message="Rechargez la page depuis le lien reçu sur Telegram.",
            )
        try:
            await pairing.check_code(c)
            ekylibre = parse_instance(instance, allowed_domains)
        except PairingError as exc:
            return expired(request, str(exc))
        except InvalidInstance as exc:
            return form(request, c, error=str(exc), instance=instance, email=email, status=422)

        try:
            result = await pairing.complete(c, ekylibre, email.strip(), secret)
        except EkylibreAuthError as exc:
            await pairing.record_failure(c)
            log.info("pairing.failed", reason="ekylibre_auth")
            return form(request, c, error=str(exc), instance=instance, email=email, status=401)
        except PairingError as exc:
            return expired(request, str(exc))

        log.info("pairing.completed", farm_created=result.farm_created, role=result.role)
        try:
            await on_paired(result)
        except Exception:
            log.warning("pairing.notify_failed")
        return _page(request, "done.html", farm_name=result.farm_name)

    return router
