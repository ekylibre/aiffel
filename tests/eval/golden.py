"""Évaluation de l'extraction d'Aiffel sur le corpus de Duke (A7), sans aucune écriture.

Chaque phrase de ``golden_phrases.yaml`` est soumise au vrai moteur d'agent (prompt, règles,
savoir-faire, outils). La ferme est un faux Ekylibre construit à partir des entités du corpus
(parcelles, produits, matériel, intervenants), avec les vraies procédures Ekylibre
(``fixtures/procedures.json``). L'outil de proposition est le vrai (contrôles et mapping
Procedo), mais il enregistre dans un faux ``ActionService`` : rien ne part vers Ekylibre.

Score par champ, uniquement quand le corpus attend ce champ :
- **intention** : enregistrement (proposition tentée ou savoir-faire chargé), question
  (lecture de données) ou autre ;
- **procédure** : la procédure choisie correspond au verbe de la phrase (racine ou synonyme) ;
- **parcelle**, **intrant** : noms des cibles et des intrants proposés ;
- **dose** : nombre de la quantité ;
- **date** : expressions relatives courantes (« ce matin », « hier », « lundi »…).

Le corpus de Duke est fait de messages isolés, souvent incomplets (« labour »). Quand l'agent
demande ce qui manque au lieu de proposer, une réponse simulée de l'agriculteur lui donne la
parcelle, la date et la durée (``follow_up``) ; l'intention est jugée sur le premier message, les
champs sur la dernière proposition.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import yaml

from aiffel.actions.service import Proposal, payload_hash
from aiffel.connectors.ekylibre.auth import (
    EkylibreCredentials,
    EkylibreInstance,
    SimpleTokenAuth,
)
from aiffel.connectors.ekylibre.client import EkylibreClient
from aiffel.orchestrator.agent import Orchestrator
from aiffel.orchestrator.llm import Usage
from aiffel.tools.registry import ToolContext, ToolGateway, ToolResult
from aiffel.vault import SecretValue
from tests.fakes import PROCEDURES, FakeEkylibre

DUKE_CORPUS = Path("/home/djoulin/projects/duke/tests/fixtures/golden_phrases.yaml")
PROCEDURES_FIXTURE = Path(__file__).parent / "fixtures" / "procedures.json"
PARIS = ZoneInfo("Europe/Paris")
PROPOSE = "ekylibre_proposer_intervention"
RECORD_SKILL = "enregistrer-intervention"
FIELDS = ("procedure", "parcel", "product", "quantity", "date")

# Prix par million de jetons (entrée, sortie, lecture du cache, écriture du cache 5 min).
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}

# Verbes du corpus dont la racine n'apparaît pas dans le nom de la procédure Ekylibre.
SYNONYMS = {
    "trait": ("pulveris", "spraying"),
    "desherb": ("pulveris", "desherb", "spraying", "weeding", "hoeing"),
    "epand": ("fertilis", "epand", "spreading", "fertiliz"),
    "recolt": ("recolt", "harvest", "moisson"),
    "moisson": ("recolt", "harvest"),
    "vendang": ("vendang", "harvest", "grape"),
    "fauch": ("fauch", "mowing"),
    "sem": ("semi", "sowing"),
    "labour": ("labour", "plough", "plow"),
    "irrig": ("irrig", "arros", "watering"),
    "arros": ("irrig", "arros", "watering"),
    "taill": ("taill", "pruning"),
    "retir": ("retrait", "removing"),
}
ANIMAL_WORDS = ("vache", "troupeau", "animaux", "veau", "brebis", "agneau", "cheptel")
DEFAULT_PARCEL = "Bel Air"
WORKER_PRONOUNS = {"j'ai", "je", "j", "on", "nous", "moi"}


def norm(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold().replace("\u2019", "'"))
    return " ".join("".join(c for c in folded if not unicodedata.combining(c)).split())


# --- Corpus ---


@dataclass(frozen=True)
class Entry:
    text: str
    intent: str
    entities: dict[str, tuple[str, ...]]

    def spans(self, label: str) -> tuple[str, ...]:
        return self.entities.get(label, ())

    @property
    def group(self) -> str:
        if self.intent == "record_intervention":
            return "record"
        if self.intent.startswith("qa_"):
            return "qa"
        return "other"


def load_corpus(path: Path = DUKE_CORPUS) -> list[Entry]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = []
    for item in raw:
        entities: dict[str, list[str]] = defaultdict(list)
        for entity in item.get("entities") or []:
            entities[entity["label"].removeprefix("DUKE_")].append(entity["span"])
        entries.append(
            Entry(item["text"], item["intent"], {k: tuple(v) for k, v in entities.items()})
        )
    return entries


# --- Ferme d'évaluation ---


def load_procedures(path: Path = PROCEDURES_FIXTURE) -> dict[str, dict[str, Any]]:
    """Procédures Ekylibre réelles si l'instantané existe, sinon les deux procédures des tests."""
    if path.exists():
        return dict(json.loads(path.read_text(encoding="utf-8")))
    return PROCEDURES


@dataclass
class EvalEkylibre(FakeEkylibre):
    procedures: dict[str, dict[str, Any]] = field(default_factory=dict)
    productions: list[dict[str, Any]] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if self._account(request) is not None:
            if path == "/api/v2/procedures":
                return httpx.Response(
                    200,
                    json=[
                        {"name": n, "label": p.get("label") or p.get("human_name")}
                        for n, p in self.procedures.items()
                    ],
                )
            if path.startswith("/api/v2/procedures/"):
                name = path.rsplit("/", 1)[1]
                if name in self.procedures:
                    return httpx.Response(200, json=self.procedures[name])
                return httpx.Response(404, json={"errors": ["not found"]})
            if path == "/api/v2/activity_productions":
                return httpx.Response(200, json=_page(self.productions))
            if path in ("/api/v2/intervention_records", "/api/v2/input_totals"):
                return httpx.Response(200, json=_page([]))
        return super().handler(request)


def _page(data: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(data)
    pagination = {"elements": n, "page": 1, "per_page": 50, "total_elements": n, "page_count": 1}
    return {"pagination": pagination, "data": data[:50]}


def build_farm(corpus: list[Entry], procedures: dict[str, dict[str, Any]]) -> EvalEkylibre:
    """Une seule ferme qui contient toutes les entités du corpus, dédoublonnées."""

    def unique(label: str, skip: set[str] | None = None) -> list[str]:
        seen: dict[str, str] = {}
        for entry in corpus:
            for span in entry.spans(label):
                if norm(span) not in (skip or set()):
                    seen.setdefault(norm(span), span)
        return list(seen.values())

    parcels = unique("PARCEL")
    next_id = iter(range(100, 100_000))
    zones, land, plants, productions = [], [], [], []
    for index, parcel in enumerate(parcels):
        zones.append(
            {"id": index + 1, "uuid": f"z{index}", "work_number": f"P{index:03d}", "name": parcel}
        )
        land.append({"id": next(next_id), "name": parcel})
        plant_id = next(next_id)
        plants.append(
            {"id": plant_id, "name": f"Culture {parcel} 2026", "activity_name": "Culture"}
        )
        productions.append(
            {
                "id": index + 1,
                "name": f"Culture {parcel} 2026",
                "started_on": "2025-09-01",
                "stopped_on": "2027-08-31",
                "state": "opened",
                "activity": {"name": "Culture"},
                "support": {"name": parcel},
                "plants": [{"id": plant_id, "name": f"Culture {parcel} 2026"}],
            }
        )
    farm = EvalEkylibre(procedures=procedures, productions=productions)
    farm.zones = zones
    farm.products = {
        "LandParcel": land,
        "Plant": plants,
        "Matter": [{"id": next(next_id), "name": p} for p in unique("PRODUCT")],
        "Equipment": [{"id": next(next_id), "name": t} for t in unique("TOOL")],
        "Worker": [{"id": next(next_id), "name": w} for w in unique("WORKER", skip=WORKER_PRONOUNS)]
        + [{"id": next(next_id), "name": "Jeanne Chef"}],
        "Animal": [
            {"id": next(next_id), "name": name}
            for name in ("Vaches laitières troupeau 14", "Génisses zone 3", "Brebis lot 2")
        ],
        "BuildingDivision": [
            {"id": next(next_id), "name": name} for name in ("Stabulation", "Bergerie")
        ],
    }
    return farm


class FakeActions:
    """Remplace ``ActionService`` : garde les propositions en mémoire, n'écrit rien."""

    def __init__(self) -> None:
        self.proposals: list[dict[str, Any]] = []

    async def propose(self, *_args: Any, payload: dict[str, Any], summary: str, **_: Any) -> Any:
        self.proposals.append(payload)
        return Proposal(uuid.uuid4(), payload_hash(payload), summary)

    async def open_proposal(self, *_args: Any) -> None:
        return None


class RecordingGateway:
    """Passerelle réelle, avec la trace des appels (nom, arguments, erreur)."""

    def __init__(self, gateway: ToolGateway) -> None:
        self.gateway = gateway
        self.calls: list[tuple[str, dict[str, Any], bool]] = []

    def definitions(self, ctx: ToolContext) -> list[dict[str, Any]]:
        return self.gateway.definitions(ctx)

    async def execute(self, ctx: ToolContext, name: str, raw_input: dict[str, Any]) -> ToolResult:
        result = await self.gateway.execute(ctx, name, raw_input)
        self.calls.append((name, raw_input, result.is_error))
        return result


# --- Score ---

_DAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


def expected_date(text: str, today: date) -> date | None:
    t = norm(text)
    if m := re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", t):
        return date(int(m[3]), int(m[2]), int(m[1]))
    if "avant-hier" in t or "avant hier" in t:
        return today - timedelta(days=2)
    if re.search(r"\bhier\b", t):
        return today - timedelta(days=1)
    if m := re.search(r"il y a (\d+|un|une|deux|trois) (jour|jours|semaine|semaines)", t):
        words = {"un": 1, "une": 1, "deux": 2, "trois": 3}
        n = int(m[1]) if m[1].isdigit() else words[m[1]]
        return today - timedelta(days=n * (7 if m[2].startswith("semaine") else 1))
    if re.search(r"\b(aujourd'hui|ce matin|cet apres-midi|ce soir|tout a l'heure)\b", t):
        return today
    for index, day in enumerate(_DAYS):
        if re.search(rf"\b{day}\b", t) and "prochain" not in t:
            delta = (today.weekday() - index) % 7 or 7
            return today - timedelta(days=delta)
    return None


def _number(span: str) -> float | None:
    m = re.search(r"\d+(?:[.,]\d+)?", span)
    return float(m[0].replace(",", ".")) if m else None


def procedure_matches(span: str, name: str, label: str) -> bool:
    """Un mot significatif du verbe (racine, synonyme ou faute de frappe) dans la procédure."""
    haystack = norm(f"{name} {label}").replace("_", " ").replace("/", " ")
    hay_words = re.findall(r"[a-z']+", haystack)
    for word in re.findall(r"[a-z]+", norm(span)):
        if len(word) < 4:
            continue
        synonyms = [targets for stem, targets in SYNONYMS.items() if word.startswith(stem)]
        if any(target in haystack for targets in synonyms for target in targets):
            return True
        if word[: max(4, len(word) - 2)] in haystack:
            return True
        if any(SequenceMatcher(None, word, h).ratio() >= 0.8 for h in hay_words if len(h) >= 4):
            return True
    return False


# L'agent demande ce qui manque pour préparer une intervention.
ASKS_FOR_INTERVENTION = re.compile(
    r"type d'intervention|quel(le)? (parcelle|culture|procédure)|qu'avez-vous fait|"
    r"pour (la |le )?pr[ée]parer|je (peux )?pr[ée]pare",
    re.IGNORECASE,
)


def predicted_intent(calls: list[tuple[str, dict[str, Any], bool]], answer: str) -> str:
    """Enregistrement : proposition, savoir-faire d'enregistrement, ou éléments demandés."""
    names = [name for name, _a, _e in calls]
    if (
        PROPOSE in names
        or any(n == "load_skill" and RECORD_SKILL in json.dumps(a) for n, a, _e in calls)
        or ASKS_FOR_INTERVENTION.search(answer.replace("\u2019", "'"))
    ):
        return "record"
    if any(name.startswith(("ekylibre_", "lexicon_")) for name in names):
        return "qa"
    return "other"


def follow_up(entry: Entry, today: date) -> tuple[str, bool]:
    """Réponse simulée de l'agriculteur quand l'agent demande ce qui manque ; date fournie ?"""
    parts = []
    if not entry.spans("PARCEL"):
        if any(word in norm(entry.text) for word in ANIMAL_WORDS):
            parts.append("Ce sont les vaches laitières du troupeau 14.")
        else:
            parts.append(f"C'est sur {DEFAULT_PARCEL}.")
    with_date = expected_date(entry.text, today) is None
    if with_date:
        parts.append("C'était aujourd'hui à 9 h, pendant 1 h.")
    parts.append("Pour le reste, fais au plus simple et prépare la proposition.")
    return " ".join(parts), with_date


@dataclass
class Row:
    text: str
    expected_intent: str
    predicted_intent: str
    scores: dict[str, str]
    procedure: str = ""
    proposal: str = ""
    tools: str = ""
    latency_s: float = 0.0
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    answer: str = ""
    # Données brutes pour re-noter sans relancer le modèle (``rescore``).
    raw: dict[str, Any] = field(default_factory=dict)


def cost(model: str, usage: Usage) -> float:
    if usage.cost_usd:  # coût facturé, donné par le fournisseur (OpenRouter)
        return usage.cost_usd
    price_in, price_out, cache_read, cache_write = PRICES.get(model, (0.0, 0.0, 0.0, 0.0))
    return (
        usage.input_tokens * price_in
        + usage.output_tokens * price_out
        + usage.cache_read_tokens * cache_read
        + usage.cache_write_tokens * cache_write
    ) / 1_000_000


def score(
    entry: Entry,
    calls: list[tuple[str, dict[str, Any], bool]],
    farm: EvalEkylibre,
    today: date,
    *,
    first_turn: list[tuple[str, dict[str, Any], bool]] | None = None,
    first_answer: str = "",
    followed_up_date: bool = False,
) -> tuple[str, dict[str, str], str]:
    names = {p["id"]: p["name"] for group in farm.products.values() for p in group}
    propose_inputs = [args for name, args, _err in calls if name == PROPOSE]
    predicted = predicted_intent(calls if first_turn is None else first_turn, first_answer)

    scores = {f: "" for f in FIELDS}
    args = propose_inputs[-1] if propose_inputs else {}
    if entry.group == "record":
        procedure = str(args.get("procedure_name") or "")
        known = farm.procedures.get(procedure) or {}
        label = str(known.get("label") or known.get("human_name") or "")
        if spans := entry.spans("PROCEDURE"):
            scores["procedure"] = (
                "ok" if procedure and procedure_matches(spans[0], procedure, label) else "ko"
            )
        targets = [norm(str(names.get(i, ""))) for i in args.get("target_ids") or []]
        if spans := entry.spans("PARCEL"):
            scores["parcel"] = "ok" if any(norm(s) in t for s in spans for t in targets) else "ko"
        inputs = args.get("inputs") or []
        input_names = {norm(str(names.get(i.get("product_id"), ""))) for i in inputs}
        if spans := entry.spans("PRODUCT"):
            scores["product"] = "ok" if all(norm(s) in input_names for s in spans) else "ko"
        if spans := entry.spans("QUANTITY"):
            wanted = _number(spans[0])
            got = [float(i.get("quantity", -1)) for i in inputs]
            scores["quantity"] = (
                "ok" if wanted is not None and any(abs(g - wanted) < 1e-6 for g in got) else "ko"
            )
        when = expected_date(entry.text, today)
        if when is None and followed_up_date:
            when = today
        if when is not None:
            started = args.get("started_at")
            try:
                day = datetime.fromisoformat(str(started)).date() if started else None
            except ValueError:
                day = None
            scores["date"] = "ok" if day == when else "ko"
    return predicted, scores, str(args.get("procedure_name") or "")


# --- Exécution ---


async def run_entry(
    orchestrator: Orchestrator,
    farm: EvalEkylibre,
    http: httpx.AsyncClient,
    entry: Entry,
    *,
    today: date,
    model: str,
    lexicon: Any = None,
) -> Row:
    base = orchestrator.gateway
    gateway = RecordingGateway(base)
    orchestrator.gateway = gateway  # type: ignore[assignment]
    actions = FakeActions()
    client = EkylibreClient(
        http,
        SimpleTokenAuth(),
        EkylibreInstance(f"https://{farm.host}", farm.tenant),
        EkylibreCredentials(SecretValue("chef@demo.fr"), SecretValue("TOK-CHEF")),
    )
    ctx = ToolContext(
        farm_id=uuid.uuid4(),
        farm_name="Ferme d'évaluation",
        role="owner",
        user_id=uuid.uuid4(),
        today=today,
        ekylibre=client,
        lexicon=lexicon,
        aliases={},
        skills={name: doc.body for name, doc in orchestrator.runtime.skills.items()},
        actions=actions,  # type: ignore[arg-type]
    )
    started = time.monotonic()
    messages: list[dict[str, Any]] = [{"role": "user", "content": entry.text}]
    followed_up_date = False
    try:
        answer, usage = await orchestrator._loop(ctx, messages, None)
        first_turn, first_answer = list(gateway.calls), answer
        proposal = "1er message" if ctx.proposals else "non"
        if not ctx.proposals and entry.group == "record":
            reply, followed_up_date = follow_up(entry, today)
            messages += [
                {"role": "assistant", "content": answer},
                {"role": "user", "content": reply},
            ]
            answer, more = await orchestrator._loop(ctx, messages, None)
            usage += more
            if ctx.proposals:
                proposal = "après relance"
    finally:
        orchestrator.gateway = base
    latency = time.monotonic() - started
    predicted, scores, procedure = score(
        entry,
        gateway.calls,
        farm,
        today,
        first_turn=first_turn,
        first_answer=first_answer,
        followed_up_date=followed_up_date,
    )
    return Row(
        text=entry.text,
        expected_intent=entry.group,
        predicted_intent=predicted,
        scores=scores,
        procedure=procedure,
        proposal=proposal,
        tools=" ".join(name + ("!" if err else "") for name, _a, err in gateway.calls),
        latency_s=round(latency, 2),
        usage=usage,
        cost_usd=round(cost(model, usage), 5),
        answer=answer,
        raw={
            "calls": gateway.calls,
            "first_calls": len(first_turn),
            "first_answer": first_answer,
            "followed_up_date": followed_up_date,
        },
    )


def rescore(entry: Entry, raw: dict[str, Any], farm: EvalEkylibre, today: date) -> Any:
    calls = [tuple(c) for c in raw["calls"]]
    return score(
        entry,
        calls,  # type: ignore[arg-type]
        farm,
        today,
        first_turn=calls[: raw["first_calls"]],  # type: ignore[arg-type]
        first_answer=raw["first_answer"],
        followed_up_date=raw["followed_up_date"],
    )


def summarize(rows: list[Row]) -> dict[str, Any]:
    summary: dict[str, Any] = {"phrases": len(rows)}
    if not rows:
        return summary
    summary["intention"] = sum(r.predicted_intent == r.expected_intent for r in rows) / len(rows)
    for name in FIELDS:
        applicable = [r.scores[name] for r in rows if r.scores[name]]
        summary[name] = (
            (sum(s == "ok" for s in applicable) / len(applicable), len(applicable))
            if applicable
            else None
        )
    records = [r for r in rows if r.expected_intent == "record"]
    if records:
        summary["propositions_1er_message"] = sum(
            r.proposal == "1er message" for r in records
        ) / len(records)
        summary["propositions_après_relance"] = sum(r.proposal != "non" for r in records) / len(
            records
        )
    summary["latence_moyenne_s"] = sum(r.latency_s for r in rows) / len(rows)
    summary["coût_usd"] = sum(r.cost_usd for r in rows)
    return summary
