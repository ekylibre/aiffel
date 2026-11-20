"""Étanchéité du cœur public d'Aiffel vis-à-vis des extensions de facturation (ADR-10).

Le cœur ne doit **jamais** importer directement un paquet de facturation (ni l'historique
``aiffel_billing``, ni son successeur ``ekylibre_billing_client``). L'intégration passe
exclusivement par les entry points chargés dans ``aiffel/extensions.py``, qui est la seule
porte autorisée. Le test est **bloquant en CI** : il protège le mode installation libre
(décision D-BYO) et la séparation des deux dépôts (ADR-10).
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parents[2] / "aiffel"
# ``extensions.py`` est le seul module du cœur autorisé à nommer un paquet de facturation
# (il expose les protocoles consommés par le client et pourrait, à terme, référencer la
# convention de nommage dans un commentaire ou un test de contrat).
ALLOWED_FILES = {CORE_ROOT / "extensions.py"}
# Paquets dont l'import est interdit dans le cœur. ``aiffel_billing`` est l'ancien nom (renommé
# en ``ekylibre_billing`` lors du pivot ADR-10) ; on le garde par défense en profondeur, pour
# attraper toute résurgence dans une PR ancienne.
FORBIDDEN_TOP_LEVEL = {"aiffel_billing", "ekylibre_billing", "ekylibre_billing_client"}


def _python_files() -> list[Path]:
    return [p for p in CORE_ROOT.rglob("*.py") if p not in ALLOWED_FILES]


def _top_level(name: str) -> str:
    return name.split(".", 1)[0]


def _forbidden_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _top_level(alias.name) in FORBIDDEN_TOP_LEVEL:
                    found.append(alias.name)
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and _top_level(node.module) in FORBIDDEN_TOP_LEVEL
        ):
            found.append(node.module)
    return found


def test_core_never_imports_billing() -> None:
    """Aucun module d'``aiffel/`` (hors ``extensions.py``) ne doit importer la facturation."""
    offenders: dict[Path, list[str]] = {}
    for path in _python_files():
        imports = _forbidden_imports(path)
        if imports:
            offenders[path.relative_to(CORE_ROOT.parent)] = imports
    assert not offenders, (
        "Le cœur public d'Aiffel importe un paquet de facturation (ADR-10) :\n"
        + "\n".join(f"  {path} → {', '.join(names)}" for path, names in offenders.items())
    )
