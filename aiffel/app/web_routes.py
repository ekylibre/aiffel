"""Mini pages web : raccordement à Ekylibre (``/connect``) et clé d'IA de la ferme (``/cle``).

Le mot de passe ne vit que le temps de la requête : jamais stocké, journalisé ni renvoyé dans la
page. La clé d'IA n'est conservée que chiffrée dans le coffre, et n'est jamais réaffichée. Le code
à usage unique est dans l'URL : la page interdit l'envoi du Referer, et le journal d'accès
d'uvicorn est désactivé dans l'image Docker.
"""

import contextlib
import hmac
import secrets
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from pathlib import Path

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from aiffel.connectors.ekylibre.auth import EkylibreAuthError
from aiffel.connectors.ekylibre.instances import InvalidInstance, parse_instance
from aiffel.farm_context import FarmContextService
from aiffel.farm_llm import FarmLLMService, LLMKeyError
from aiffel.pairing import PURPOSE_LLM_KEY, PairingError, PairingResult, PairingService
from aiffel.providers.catalog import PROVIDERS
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
Notify = Callable[[uuid.UUID, str], Awaitable[None]]


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


def _csrf_ok(request: Request, csrf: str) -> bool:
    cookie = request.cookies.get(CSRF_COOKIE, "")
    return bool(cookie) and hmac.compare_digest(cookie.encode(), csrf.encode())


def _with_csrf(response: HTMLResponse, csrf: str, path: str, secure: bool) -> HTMLResponse:
    response.set_cookie(
        CSRF_COOKIE, csrf, httponly=True, samesite="strict", secure=secure, max_age=1800, path=path
    )
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
        return _with_csrf(response, csrf, "/connect", secure_cookies)

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
        if not _csrf_ok(request, csrf):
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


def llm_key_router(
    pairing: PairingService,
    farms: FarmContextService,
    llm: FarmLLMService,
    notify: Notify,
    *,
    secure_cookies: bool = True,
    limiter: RateLimiter | None = None,
) -> APIRouter:
    """Saisie de la clé d'IA de la ferme, par l'exploitant, depuis un lien envoyé par /modele."""
    router = APIRouter()
    limiter = limiter or RateLimiter()

    async def owner_farm(code: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, str]:
        """Identité, utilisateur, ferme et nom de ferme du code ; seul l'exploitant passe."""
        identity_id = await pairing.check_code(code, PURPOSE_LLM_KEY)
        user_id = await pairing.user_of(identity_id)
        farm = await farms.farm_of(user_id) if user_id else None
        if user_id is None or farm is None or farm.role != "owner":
            raise PairingError("Seul l'exploitant de la ferme peut enregistrer une clé.")
        return identity_id, user_id, farm.farm_id, farm.name

    def form(
        request: Request,
        code: str,
        farm_name: str,
        *,
        error: str | None = None,
        provider: str = "openrouter",
        status: int = 200,
    ) -> HTMLResponse:
        csrf = secrets.token_urlsafe(16)
        page = _page(
            request,
            "llm_key.html",
            status,
            code=code,
            csrf=csrf,
            error=error,
            farm_name=farm_name,
            provider=provider,
            providers=list(PROVIDERS.items()),
        )
        return _with_csrf(page, csrf, "/cle", secure_cookies)

    def refused(request: Request, message: str) -> HTMLResponse:
        return _page(request, "message.html", 410, title="Lien expiré", message=message)

    @router.get("/cle", response_class=HTMLResponse)
    async def key_form(request: Request, c: str = "") -> HTMLResponse:
        try:
            _identity, _user, _farm, farm_name = await owner_farm(c)
        except PairingError as exc:
            return refused(request, str(exc))
        return form(request, c, farm_name)

    @router.post("/cle", response_class=HTMLResponse)
    async def key_submit(
        request: Request,
        c: str = Form(""),
        csrf: str = Form(""),
        provider: str = Form(""),
        key: str = Form(""),
    ) -> HTMLResponse:
        secret = SecretValue(key.strip())
        del key
        client_ip = request.client.host if request.client else "inconnu"
        if not limiter.allow(client_ip):
            return _page(
                request,
                "message.html",
                429,
                title="Trop d'essais",
                message="Patientez une minute avant de réessayer.",
            )
        if not _csrf_ok(request, csrf):
            return _page(
                request,
                "message.html",
                400,
                title="Session expirée",
                message="Rechargez la page depuis le lien reçu sur Telegram.",
            )
        try:
            identity_id, user_id, farm_id, farm_name = await owner_farm(c)
        except PairingError as exc:
            return refused(request, str(exc))
        if provider not in PROVIDERS:
            return form(request, c, farm_name, error="Choisissez un fournisseur.", status=422)
        try:
            await llm.save_key(farm_id, user_id, provider, secret)
        except LLMKeyError as exc:
            await pairing.record_failure(c)
            log.info("llm_key.refused")
            return form(request, c, farm_name, error=str(exc), provider=provider, status=422)
        # Clé déjà enregistrée par une requête simultanée : le lien ne servira simplement plus.
        with contextlib.suppress(PairingError):
            await pairing.use_code(c, PURPOSE_LLM_KEY)
        log.info("llm_key.saved", provider=provider)
        try:
            await notify(
                identity_id,
                f"Clé {PROVIDERS[provider]} enregistrée ✅ Choisissez le modèle avec /modele.",
            )
        except Exception:
            log.warning("llm_key.notify_failed")
        return _page(
            request,
            "message.html",
            title="Clé enregistrée ✅",
            message="Vous pouvez fermer cette page et revenir sur Telegram.",
        )

    return router
