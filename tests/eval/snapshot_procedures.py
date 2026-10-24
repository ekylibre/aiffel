"""Instantané des procédures Ekylibre (liste et paramètres) pour l'évaluation hors ligne.

Lecture seule, via la connexion déjà raccordée en local :
``uv run python -m tests.eval.snapshot_procedures``.
"""

import asyncio
import json
from pathlib import Path

from sqlalchemy import text

from aiffel.app.container import build_services
from aiffel.db.session import global_session

OUT = Path(__file__).parent / "fixtures" / "procedures.json"


async def main() -> None:
    services = build_services()
    try:
        async with global_session(services.sessions) as session:
            user_id = (
                await session.execute(text("SELECT id FROM aiffel.users LIMIT 1"))
            ).scalar_one()
        farm = await services.farms.farm_of(user_id)
        access = await services.farms.ekylibre(farm, user_id) if farm else None
        if access is None or access.client is None:
            raise SystemExit("Aucune ferme raccordée : envoyez /start au bot d'abord.")
        procedures = await access.client.procedures()
        details = {}
        for item in procedures:
            details[item["name"]] = await access.client.procedure(item["name"])
        OUT.write_text(json.dumps(details, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        print(f"{len(details)} procédures → {OUT}")
    finally:
        await services.aclose()


if __name__ == "__main__":
    asyncio.run(main())
