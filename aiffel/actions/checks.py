"""Contrôles avant écriture (hook ``pre_write``, ADR-04) : du code déterministe, hors modèle.

Niveaux : ``block`` empêche la proposition, ``warn`` est affiché et tracé, ``info`` est affiché.
Pour les produits phytosanitaires, la référence est E-Phy via Lexicon ; l'étiquette fait foi.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from aiffel.actions.mapping import UNITS, Draft

Level = Literal["block", "warn", "info"]
PHYTO_INPUT_HINTS = ("plant_medicine", "phytosanitary", "herbicide", "fungicide", "insecticide")
LexiconSearch = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class Check:
    level: Level
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def blocking(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c.level == "block"]


def _norm(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in folded if not unicodedata.combining(c)).strip()


def _per_hectare(value: float, unit: str) -> tuple[float, str] | None:
    """Dose ramenée en l/ha ou kg/ha, ou ``None`` si l'unité n'est pas une densité."""
    factors = {
        "l/ha": (1.0, "l/ha"),
        "hl/ha": (100.0, "l/ha"),
        "kg/ha": (1.0, "kg/ha"),
        "g/ha": (0.001, "kg/ha"),
        "t/ha": (1000.0, "kg/ha"),
    }
    key = unit.strip().lower()
    if key not in factors:
        return None
    factor, base = factors[key]
    return value * factor, base


def basic_checks(draft: Draft, products: dict[int, dict[str, Any]], now: datetime) -> list[Check]:
    checks: list[Check] = []
    for pid in (
        *draft.target_ids,
        *(i.product_id for i in draft.inputs),
        *draft.tool_ids,
        *draft.doer_ids,
    ):
        if pid not in products:
            checks.append(
                Check("block", "unknown_product", f"Le produit n°{pid} n'existe pas dans la ferme.")
            )
    if draft.started_at > now + timedelta(hours=12):
        checks.append(
            Check(
                "block",
                "future",
                "La date est dans le futur : indiquez quand l'intervention a eu lieu.",
            )
        )
    elif draft.started_at < now - timedelta(days=365):
        checks.append(Check("warn", "old", "Intervention datée de plus d'un an."))
    for item in draft.inputs:
        if item.unit not in UNITS:
            checks.append(Check("block", "unit", f"Unité inconnue : {item.unit}."))
    return checks


def is_phyto(input_slot_name: str | None, procedure_name: str) -> bool:
    name = (input_slot_name or "").lower()
    return procedure_name == "spraying" or any(hint in name for hint in PHYTO_INPUT_HINTS)


async def phyto_checks(
    product_name: str,
    quantity: float,
    unit: str,
    crop_ephy: str | None,
    search: LexiconSearch | None,
) -> list[Check]:
    """Autorisation, usage sur la culture et dose maximale d'un produit phytosanitaire."""
    if search is None:
        return [
            Check(
                "warn",
                "phyto_unchecked",
                f"{product_name} : référentiel E-Phy indisponible, dose non vérifiée.",
            )
        ]
    params: dict[str, Any] = {"name": product_name}
    if crop_ephy:
        params["crop"] = crop_ephy
    try:
        result = await search(params)
        products = list((result or {}).get("products") or [])
        if not products and " " in product_name:  # « ELUMIS 6L » → « ELUMIS »
            result = await search({**params, "name": product_name.split()[0]})
            products = list((result or {}).get("products") or [])
    except Exception:
        return [
            Check(
                "warn", "phyto_unchecked", f"{product_name} : E-Phy injoignable, dose non vérifiée."
            )
        ]

    if not products:
        level: Level = "warn"
        return [
            Check(
                level,
                "phyto_unknown",
                f"{product_name} : introuvable dans E-Phy (produit autorisé ?).",
            )
        ]
    product = products[0]
    label = f"{product.get('name')} (AMM {product.get('authorisation-number')})"
    if not crop_ephy:
        return [
            Check(
                "warn",
                "phyto_crop_unknown",
                f"{label} : culture non précisée, usage et dose non vérifiés.",
            )
        ]

    usages = [
        u for u in product.get("usages") or [] if _norm(str(u.get("crop", ""))) == _norm(crop_ephy)
    ]
    if not usages:
        return [
            Check(
                "block",
                "phyto_no_usage",
                f"{label} n'est pas autorisé sur {crop_ephy} selon E-Phy.",
            )
        ]

    checks: list[Check] = []
    dose = _per_hectare(quantity, unit)
    comparable = [
        (u, ref)
        for u in usages
        if u.get("dose") is not None
        and (ref := _per_hectare(float(u["dose"]), str(u.get("dose-unit", ""))))
    ]
    if dose is None:
        checks.append(
            Check(
                "warn",
                "phyto_dose_unchecked",
                f"{label} : quantité totale, dose par hectare non vérifiée.",
            )
        )
    elif not comparable or any(ref[1] != dose[1] for _u, ref in comparable):
        checks.append(
            Check(
                "warn", "phyto_dose_unchecked", f"{label} : unités différentes, dose non vérifiée."
            )
        )
    else:
        max_dose = max(ref[0] for _u, ref in comparable)
        if dose[0] > max_dose + 1e-9:
            checks.append(
                Check(
                    "block",
                    "phyto_overdose",
                    f"{label} : {dose[0]:g} {dose[1]} dépasse la dose homologuée sur "
                    f"{crop_ephy} ({max_dose:g} {dose[1]}).",
                )
            )
        else:
            checks.append(
                Check(
                    "info",
                    "phyto_dose_ok",
                    f"{label} : dose conforme (max {max_dose:g} {dose[1]}).",
                )
            )
    dar = [u["pre-harvest-days"] for u in usages if u.get("pre-harvest-days") is not None]
    znt = [u["water-buffer-metres"] for u in usages if u.get("water-buffer-metres") is not None]
    if dar:
        checks.append(Check("info", "phyto_dar", f"Délai avant récolte : {max(dar)} jours."))
    if znt:
        checks.append(Check("info", "phyto_znt", f"Zone non traitée près de l'eau : {max(znt)} m."))
    return checks
