"""Validation de l'adresse d'une ferme Ekylibre saisie par l'utilisateur.

Aiffel appelle ensuite cette adresse depuis le serveur : pour éviter qu'on lui fasse joindre un
service interne (SSRF), seuls les sous-domaines de domaines Ekylibre autorisés sont acceptés,
en HTTPS, sans port, ni identifiants, ni chemin.
"""

import ipaddress
import re
from urllib.parse import urlsplit

from aiffel.connectors.ekylibre.auth import EkylibreInstance

_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class InvalidInstance(ValueError):
    pass


def parse_instance(raw: str, allowed_domains: tuple[str, ...]) -> EkylibreInstance:
    """``demo.ekylibre-dev.com`` ou ``https://demo.ekylibre-dev.com/`` → tenant ``demo``."""
    value = raw.strip().lower()
    if "://" not in value:
        value = f"https://{value}"
    parts = urlsplit(value)
    host = parts.hostname or ""
    if parts.scheme != "https":
        raise InvalidInstance("L'adresse doit être en https.")
    if parts.username or parts.password or parts.port or parts.query or parts.fragment:
        raise InvalidInstance(
            "Indiquez seulement l'adresse de la ferme, par exemple demo.ekylibre.com."
        )
    if parts.path not in ("", "/") and not parts.path.startswith("/backend"):
        raise InvalidInstance(
            "Indiquez seulement l'adresse de la ferme, par exemple demo.ekylibre.com."
        )
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise InvalidInstance("Une adresse IP n'est pas acceptée.")

    for domain in allowed_domains:
        suffix = f".{domain}"
        if host.endswith(suffix):
            tenant = host[: -len(suffix)]
            if _LABEL.match(tenant):
                return EkylibreInstance(base_url=f"https://{host}", tenant=tenant)
    raise InvalidInstance(
        "Adresse non reconnue : indiquez l'adresse de votre ferme Ekylibre "
        f"(sous-domaine de {', '.join(allowed_domains)})."
    )
