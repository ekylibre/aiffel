"""Régénère ``aiffel/connectors/lyvre/tool_schemas.json`` depuis un clone de Lyvre.

Seuls les outils de **lecture** sont gardés : Aiffel n'écrit jamais dans Lyvre (T4, ADR-10).
À lancer avec l'interpréteur d'un environnement où les dépendances de Lyvre sont installées :

    <venv-lyvre>/bin/python -I scripts/sync_lyvre_tools.py ../lyvre > /tmp/tools.json
    python scripts/sync_lyvre_tools.py --write /tmp/tools.json

La première forme interroge le registre ``app.services.agent_tools`` de Lyvre ; la seconde écrit
l'instantané trié (relire le diff : chaque nouvel outil devient un outil de l'agent).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "aiffel" / "connectors" / "lyvre" / "tool_schemas.json"


def dump(lyvre_root: str) -> None:
    sys.path.insert(0, lyvre_root)
    from app.services import agent_tools  # type: ignore[import-not-found]

    tools = [
        {**t.definition(), "domain": t.domain, "access": t.access} for t in agent_tools.registry()
    ]
    print(json.dumps(tools, ensure_ascii=False))


def write(dump_file: str) -> None:
    tools = json.loads(Path(dump_file).read_text(encoding="utf-8"))
    snapshot = {
        t["name"]: {
            "title": t["title"],
            "domain": t["domain"],
            "description": t["description"],
            "input_schema": t["inputSchema"],
        }
        for t in sorted(tools, key=lambda t: t["name"])
        if t["access"] == "read"
    }
    TARGET.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(snapshot)} outils de lecture écrits dans {TARGET}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--write":
        write(sys.argv[2])
    elif len(sys.argv) == 2:
        dump(sys.argv[1])
    else:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
