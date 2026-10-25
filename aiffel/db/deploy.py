"""Préparation de la base au déploiement : migrations Alembic puis mot de passe du rôle applicatif.

Appelé par le service ``migrate`` du compose de production avant l'app et le worker. Idempotent :
rejouable à chaque déploiement. Utilise ``AIFFEL_MIGRATION_DATABASE_URL`` (propriétaire) et
``AIFFEL_APP_PASSWORD`` (mot de passe donné au rôle ``aiffel_app`` créé ``NOLOGIN``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"Variable manquante : {name}", file=sys.stderr)
        sys.exit(2)
    return value


def _escape_literal(value: str) -> str:
    # ``ALTER ROLE`` n'accepte pas de paramètre lié ; on quote le mot de passe à la main.
    return "'" + value.replace("'", "''") + "'"


def main() -> None:
    url = _env("AIFFEL_MIGRATION_DATABASE_URL")
    password = _env("AIFFEL_APP_PASSWORD")

    # env.py lit AIFFEL_MIGRATION_DATABASE_URL via Settings ; alembic.ini sert au script_location.
    repo_root = Path(__file__).resolve().parents[2]
    command.upgrade(Config(str(repo_root / "alembic.ini")), "head")

    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f"ALTER ROLE aiffel_app LOGIN PASSWORD {_escape_literal(password)}"))
    finally:
        engine.dispose()
    print("Base prête et rôle aiffel_app configuré.")


if __name__ == "__main__":
    main()
