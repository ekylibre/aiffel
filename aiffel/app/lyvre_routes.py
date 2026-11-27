"""Page ``/lyvre`` : l'exploitant enregistre la clé d'API Lyvre de la ferme (T4).

Ouverte par un lien à usage unique de ``/lyvre`` sur Telegram ; seul l'exploitant passe. La clé
est vérifiée chez Lyvre, chiffrée, et n'est plus jamais affichée ni journalisée.
"""

from __future__ import annotations

import contextlib
import secrets
import uuid

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from aiffel.app.web_routes import Notify, RateLimiter, _csrf_ok, _page, _with_csrf
from aiffel.channels.lyvre_menu import domains_text
from aiffel.farm_context import FarmContextService
from aiffel.lyvre import LyvreConnectError, LyvreService
from aiffel.pairing import PURPOSE_LYVRE, PairingError, PairingService
from aiffel.vault import SecretValue

log = structlog.get_logger()

PATH = "/lyvre"


def lyvre_router(
    pairing: PairingService,
    farms: FarmContextService,
    lyvre: LyvreService,
    notify: Notify,
    default_url: str,
    *,
    secure_cookies: bool = True,
    limiter: RateLimiter | None = None,
) -> APIRouter:
    router = APIRouter()
    limiter = limiter or RateLimiter()

    async def owner_farm(code: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, str]:
        identity_id = await pairing.check_code(code, PURPOSE_LYVRE)
        user_id = await pairing.user_of(identity_id)
        farm = await farms.farm_of(user_id) if user_id else None
        if user_id is None or farm is None or farm.role != "owner":
            raise PairingError("Seul l'exploitant de la ferme raccorde la comptabilité Lyvre.")
        return identity_id, user_id, farm.farm_id, farm.name

    def form(
        request: Request,
        code: str,
        farm_name: str,
        *,
        url: str,
        error: str | None = None,
        status: int = 200,
    ) -> HTMLResponse:
        csrf = secrets.token_urlsafe(16)
        page = _page(
            request,
            "lyvre.html",
            status,
            code=code,
            csrf=csrf,
            error=error,
            farm_name=farm_name,
            url=url,
        )
        return _with_csrf(page, csrf, PATH, secure_cookies)

    def message(request: Request, title: str, text: str, status: int = 200) -> HTMLResponse:
        return _page(request, "message.html", status, title=title, message=text)

    @router.get(PATH, response_class=HTMLResponse)
    async def lyvre_form(request: Request, c: str = "") -> HTMLResponse:
        try:
            *_ids, farm_name = await owner_farm(c)
        except PairingError as exc:
            return message(request, "Lien expiré", str(exc), 410)
        return form(request, c, farm_name, url=default_url)

    @router.post(PATH, response_class=HTMLResponse)
    async def lyvre_submit(
        request: Request,
        c: str = Form(""),
        csrf: str = Form(""),
        url: str = Form(""),
        key: str = Form(""),
    ) -> HTMLResponse:
        secret = SecretValue(key.strip())
        del key
        client_ip = request.client.host if request.client else "inconnu"
        if not limiter.allow(client_ip):
            return message(
                request, "Trop d'essais", "Patientez une minute avant de réessayer.", 429
            )
        if not _csrf_ok(request, csrf):
            return message(
                request,
                "Session expirée",
                "Rechargez la page depuis le lien reçu sur Telegram.",
                400,
            )
        try:
            identity_id, user_id, farm_id, farm_name = await owner_farm(c)
        except PairingError as exc:
            return message(request, "Lien expiré", str(exc), 410)
        try:
            account = await lyvre.connect(farm_id, user_id, url, secret)
        except LyvreConnectError as exc:
            await pairing.record_failure(c)
            log.info("lyvre.connect_refused")
            return form(request, c, farm_name, url=url, error=str(exc), status=422)
        with contextlib.suppress(PairingError):
            await pairing.use_code(c, PURPOSE_LYVRE)
        log.info("lyvre.connected", domains=len(account.reads))
        summary = (
            f"✅ Comptabilité Lyvre raccordée : « {account.structure} ». "
            f"Domaines lus : {domains_text(account.reads)}."
        )
        if account.writes:
            summary += (
                " Cette clé peut aussi écrire dans Lyvre : Aiffel ne s'en servira pas, mais une "
                "clé en lecture seule est plus prudente."
            )
        try:
            await notify(identity_id, f"{summary} Posez-moi vos questions de comptabilité.")
        except Exception:
            log.warning("lyvre.notify_failed")
        return message(request, "Lyvre raccordé ✅", f"{summary} Vous pouvez fermer cette page.")

    return router
