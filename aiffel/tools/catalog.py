"""Outils de la phase 1 : lecture Ekylibre, référentiel Lexicon, calculs, savoir-faire.

Toutes les quantités et les sommes passent par des outils déterministes (NF-IAC) : le modèle
choisit quoi calculer, le code calcule.
"""

from __future__ import annotations

import json
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from aiffel.connectors.ekylibre.client import EkylibreError, EkylibreUnauthorized
from aiffel.connectors.lexicon.client import LexiconError
from aiffel.tools.registry import ToolAbort, ToolContext, ToolFailure, ToolOutput, ToolSpec

EKYLIBRE = "Ekylibre"
RECONNECT_REASON = "ekylibre_unauthorized"
MAX_PAGES = 5


def _norm(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in folded if not unicodedata.combining(c))


async def _ekylibre(ctx: ToolContext, method: str, *args: Any, **kwargs: Any) -> Any:
    if ctx.ekylibre is None:
        raise ToolFailure("Ekylibre n'est pas connecté pour cette ferme.")
    try:
        return await getattr(ctx.ekylibre, method)(*args, **kwargs)
    except EkylibreUnauthorized:
        raise ToolAbort(RECONNECT_REASON) from None
    except EkylibreError as exc:
        raise ToolFailure(f"Ekylibre : {exc}") from None


async def _all_pages(ctx: ToolContext, method: str, **params: Any) -> dict[str, Any]:
    """Concatène jusqu'à MAX_PAGES pages de l'enveloppe ``pagination``/``data``."""
    rows: list[Any] = []
    pagination: dict[str, Any] = {}
    for page in range(1, MAX_PAGES + 1):
        body = await _ekylibre(ctx, method, page=page, per_page=100, **params)
        rows += body.get("data", [])
        pagination = body.get("pagination", {})
        if page >= int(pagination.get("page_count") or 1):
            break
    total = int(pagination.get("total_elements") or len(rows))
    return {"total": total, "returned": len(rows), "complete": len(rows) >= total, "rows": rows}


# --- Ekylibre ---


class ZonesInput(BaseModel):
    query: str | None = Field(None, description="Partie du nom ou du numéro de parcelle")


async def _zones(ctx: ToolContext, args: ZonesInput) -> ToolOutput:
    zones = await _ekylibre(ctx, "cultivable_zones")
    if args.query:
        q = _norm(args.query)
        alias = next((v for k, v in ctx.aliases.items() if _norm(k) == q), None)
        zones = [
            z
            for z in zones
            if q in _norm(str(z.get("name") or ""))
            or q in _norm(str(z.get("work_number") or ""))
            or (alias and alias in (str(z.get("name")), str(z.get("work_number"))))
        ]
    return ToolOutput({"parcelles": zones, "nombre": len(zones)}, EKYLIBRE)


class CampaignInput(BaseModel):
    campaign: int = Field(..., description="Année de récolte, par exemple 2026", ge=2000, le=2100)
    activity_id: int | None = None
    cultivable_zone_id: int | None = None


async def _productions(ctx: ToolContext, args: CampaignInput) -> ToolOutput:
    data = await _all_pages(
        ctx,
        "activity_productions",
        campaign=args.campaign,
        activity_id=args.activity_id,
        cultivable_zone_id=args.cultivable_zone_id,
    )
    return ToolOutput(data, f"{EKYLIBRE}, productions {args.campaign}")


class InterventionsInput(BaseModel):
    campaign: int | None = Field(None, ge=2000, le=2100, description="Année de récolte")
    started_after: date | None = None
    started_before: date | None = None
    procedure_name: str | None = Field(None, description="Nom de procédure, par exemple spraying")
    target_id: int | None = Field(None, description="Produit ciblé (champ target_product_ids)")
    input_product_id: int | None = None


async def _interventions(ctx: ToolContext, args: InterventionsInput) -> ToolOutput:
    data = await _all_pages(ctx, "intervention_records", **args.model_dump(mode="json"))
    return ToolOutput(data, f"{EKYLIBRE}, interventions réalisées")


class InputTotalsInput(BaseModel):
    campaign: int = Field(..., ge=2000, le=2100, description="Année de récolte")
    activity_production_id: int | None = None
    activity_id: int | None = None
    input_product_id: int | None = None
    procedure_name: str | None = None
    started_after: date | None = None
    started_before: date | None = None


async def _input_totals(ctx: ToolContext, args: InputTotalsInput) -> ToolOutput:
    params = args.model_dump(mode="json")
    campaign = params.pop("campaign")
    data = await _all_pages(ctx, "input_totals", campaign=campaign, **params)
    return ToolOutput(data, f"{EKYLIBRE}, totaux d'intrants {campaign}")


ProductType = Literal["Matter", "Equipment", "Worker", "Plant", "LandParcel", "Animal"]


class ProductsInput(BaseModel):
    product_type: ProductType = Field(..., description="Matter = intrants et stocks")
    query: str | None = Field(None, description="Partie du nom")


async def _products(ctx: ToolContext, args: ProductsInput) -> ToolOutput:
    products = await _ekylibre(ctx, "products", args.product_type)
    if args.query:
        q = _norm(args.query)
        products = [p for p in products if q in _norm(str(p.get("name") or ""))]
    return ToolOutput({"produits": products[:100], "nombre": len(products)}, EKYLIBRE)


class NoInput(BaseModel):
    pass


async def _procedures(ctx: ToolContext, _args: NoInput) -> ToolOutput:
    procedures = await _ekylibre(ctx, "procedures")
    slim = [{k: p.get(k) for k in ("name", "label", "categories") if k in p} for p in procedures]
    return ToolOutput(slim, EKYLIBRE)


# --- Lexicon (référentiel public, lecture seule) ---

_LEXICON_SCHEMAS: dict[str, dict[str, Any]] = json.loads(
    (
        Path(__file__).resolve().parents[1] / "connectors" / "lexicon" / "tool_schemas.json"
    ).read_text()
)


def _lexicon_handler(remote_name: str) -> Any:
    async def handler(ctx: ToolContext, args: dict[str, Any]) -> ToolOutput:
        if ctx.lexicon is None:
            raise ToolFailure("Lexicon n'est pas disponible.")
        try:
            data = await ctx.lexicon.call(remote_name, args)
        except LexiconError as exc:
            raise ToolFailure(str(exc)) from None
        return ToolOutput(
            data,
            "Lexicon (OSFarm)",
            licence="Licence de chaque jeu indiquée par la source ; citer le fournisseur.",
        )

    return handler


def lexicon_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name=f"lexicon_{name}",
            description=spec["description"],
            input_schema=spec["input_schema"],
            handler=_lexicon_handler(name),
            requires="lexicon",
        )
        for name, spec in _LEXICON_SCHEMAS.items()
    ]


# --- Calculs déterministes ---

_FACTORS = {
    "mass": {"g": 0.001, "kg": 1.0, "q": 100.0, "t": 1000.0},
    "volume": {"ml": 0.001, "l": 1.0, "hl": 100.0, "m3": 1000.0},
    "area": {"m2": 0.0001, "a": 0.01, "ha": 1.0},
}
Unit = Literal["g", "kg", "q", "t", "ml", "l", "hl", "m3", "m2", "a", "ha"]


def _dimension(unit: str) -> str:
    for dim, units in _FACTORS.items():
        if unit in units:
            return dim
    raise ToolFailure(f"Unité inconnue : {unit}")


class ConvertInput(BaseModel):
    value: float
    from_unit: Unit
    to_unit: Unit


async def _convert(_ctx: ToolContext, args: ConvertInput) -> ToolOutput:
    dim = _dimension(args.from_unit)
    if _dimension(args.to_unit) != dim:
        raise ToolFailure("Conversion impossible entre ces deux unités.")
    value = args.value * _FACTORS[dim][args.from_unit] / _FACTORS[dim][args.to_unit]
    return ToolOutput({"value": round(value, 6), "unit": args.to_unit}, "calcul Aiffel")


class Quantity(BaseModel):
    value: float
    unit: Unit


class SumInput(BaseModel):
    quantities: list[Quantity] = Field(..., min_length=1, max_length=500)
    to_unit: Unit


async def _sum(_ctx: ToolContext, args: SumInput) -> ToolOutput:
    dim = _dimension(args.to_unit)
    total = 0.0
    for q in args.quantities:
        if _dimension(q.unit) != dim:
            raise ToolFailure("Les quantités n'ont pas toutes la même dimension.")
        total += q.value * _FACTORS[dim][q.unit]
    total /= _FACTORS[dim][args.to_unit]
    return ToolOutput(
        {"total": round(total, 6), "unit": args.to_unit, "count": len(args.quantities)},
        "calcul Aiffel",
    )


class DoseInput(BaseModel):
    quantity: float = Field(..., ge=0)
    unit: Unit = Field(..., description="Unité de la quantité totale (kg, l…)")
    area_ha: float = Field(..., gt=0, description="Surface traitée en hectares")


async def _dose(_ctx: ToolContext, args: DoseInput) -> ToolOutput:
    if _dimension(args.unit) == "area":
        raise ToolFailure("La quantité doit être une masse ou un volume.")
    return ToolOutput(
        {"dose": round(args.quantity / args.area_ha, 6), "unit": f"{args.unit}/ha"}, "calcul Aiffel"
    )


# --- Savoir-faire (ECC) ---


class SkillInput(BaseModel):
    name: str


async def _load_skill(ctx: ToolContext, args: SkillInput) -> ToolOutput:
    body = ctx.skills.get(args.name)
    if body is None:
        raise ToolFailure(
            f"Savoir-faire inconnu : {args.name}. Disponibles : {', '.join(ctx.skills)}"
        )
    return ToolOutput({"skill": args.name, "instructions": body}, "Aiffel (savoir-faire)")


def phase1_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            "ekylibre_parcelles",
            "Parcelles cultivables de la ferme (nom, numéro). Filtre facultatif par nom.",
            _zones,
            ZonesInput,
            requires="ekylibre",
        ),
        ToolSpec(
            "ekylibre_productions",
            "Productions d'une campagne (culture, surface, parcelle support, produits cibles).",
            _productions,
            CampaignInput,
            requires="ekylibre",
        ),
        ToolSpec(
            "ekylibre_interventions",
            "Interventions réalisées (date, procédure, cibles, intrants et quantités), filtrables.",
            _interventions,
            InterventionsInput,
            requires="ekylibre",
        ),
        ToolSpec(
            "ekylibre_totaux_intrants",
            "Totaux d'intrants par production pour une campagne : quantités, passages, surface "
            "traitée, unités d'azote quand elles sont connues.",
            _input_totals,
            InputTotalsInput,
            requires="ekylibre",
        ),
        ToolSpec(
            "ekylibre_produits",
            "Produits de la ferme par type (intrants, matériel, personnes, plantes, animaux).",
            _products,
            ProductsInput,
            requires="ekylibre",
        ),
        ToolSpec(
            "ekylibre_procedures",
            "Liste des procédures d'intervention connues d'Ekylibre.",
            _procedures,
            NoInput,
            requires="ekylibre",
        ),
        *lexicon_tools(),
        ToolSpec(
            "calc_conversion",
            "Convertit une quantité (masse, volume, surface).",
            _convert,
            ConvertInput,
        ),
        ToolSpec("calc_somme", "Additionne des quantités de même dimension.", _sum, SumInput),
        ToolSpec(
            "calc_dose_par_hectare", "Dose par hectare = quantité / surface.", _dose, DoseInput
        ),
        ToolSpec(
            "load_skill", "Charge les instructions d'un savoir-faire.", _load_skill, SkillInput
        ),
    ]
