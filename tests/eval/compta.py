"""Évaluation compta (T4) : 10 questions de référence posées au vrai moteur, avec un faux Lyvre.

Coûte des appels à l'API du modèle ; rien n'est écrit nulle part (Lyvre est simulé, et Aiffel
n'a de toute façon aucun outil d'écriture Lyvre).

    uv run python -m tests.eval.compta
    uv run python -m tests.eval.compta --model claude-sonnet-5-5 --effort low

Critère M-Lyvre du plan : chaque question est **réussie** si l'agent appelle un outil Lyvre
attendu, donne le chiffre de référence de la ferme simulée, et cite « Source : Lyvre ».
On note aussi si le montant précise HT ou TTC (règle du savoir-faire ``consulter-compta``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx

from aiffel.app.container import make_provider
from aiffel.connectors.lyvre.client import LyvreClient
from aiffel.logging import configure_logging
from aiffel.orchestrator.agent import Orchestrator
from aiffel.orchestrator.runtime import AgentRuntime
from aiffel.settings import Settings
from aiffel.tools.catalog import phase1_tools
from aiffel.tools.registry import ToolContext, ToolGateway
from aiffel.vault import SecretValue
from tests.eval.golden import RecordingGateway, cost
from tests.fakes import LYVRE_KEY, FakeLyvre

REPORTS = Path(__file__).resolve().parent / "reports"
TODAY = date(2026, 11, 27)
URL = "https://app.lyvre.org"


@dataclass(frozen=True)
class Question:
    text: str
    tools: frozenset[str]  # au moins un de ces outils Lyvre (sans préfixe)
    figure: str  # expression régulière du chiffre attendu dans la réponse
    # Montant soumis à TVA (achats, ventes, charges, marges) : HT ou TTC doit être précisé. Faux
    # pour un solde bancaire, des salaires, la TVA due ou les cotisations MSA.
    amount: bool = True
    # Le chiffre est une somme de plusieurs lignes renvoyées (l'agent passe par calc_somme).
    summed: bool = False


QUESTIONS: tuple[Question, ...] = (
    Question(
        "Combien j'ai dépensé en engrais cette année ?",
        frozenset({"get_balances", "list_purchases", "search_entries"}),
        r"8[\s ]?450",
    ),
    Question(
        "Qu'est-ce qu'il me reste à payer aux fournisseurs ?",
        frozenset({"list_purchases"}),
        r"3[\s ]?160",
        summed=True,
    ),
    Question(
        "Combien j'ai sur mes comptes en banque ?",
        frozenset({"get_bank_accounts"}),
        r"24[\s ]?317",
        amount=False,
    ),
    Question(
        "Combien m'a coûté la main-d'œuvre salariée sur l'exercice ?",
        frozenset({"get_balances", "search_entries"}),
        r"31[\s ]?900",
        amount=False,
        summed=True,
    ),
    Question(
        "Quelles sont mes prochaines échéances ?",
        frozenset({"get_agenda"}),
        r"(MSA|TVA|fermage)",
        amount=False,
    ),
    Question("Où en est ma TVA ?", frozenset({"get_vat"}), r"1[\s ]?842", amount=False),
    Question("Quelle est la marge de mon blé ?", frozenset({"get_margins"}), r"412"),
    Question(
        "Combien j'ai payé à la Coop de Saintonge cette année ?",
        frozenset({"list_purchases", "find_party", "search_entries"}),
        r"5[\s ]?280",
        summed=True,
    ),
    Question(
        "Quelles factures de vente ne sont pas encore payées ?",
        frozenset({"list_sales"}),
        r"6[\s ]?900",
    ),
    Question(
        "Combien je vais payer de MSA cette année ?",
        frozenset({"get_msa"}),
        r"7[\s ]?615",
        amount=False,
    ),
)


@dataclass
class EvalLyvre(FakeLyvre):
    """Dossier « GAEC des Essais » : tous les domaines lus, chiffres cohérents entre outils."""

    reads: set[str] = field(
        default_factory=lambda: {
            "accounting",
            "bank",
            "purchases",
            "sales",
            "parties",
            "tax",
            "assets",
            "agronomy",
            "agenda",
        }
    )

    def _result(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        link = f"https://{self.host}"
        prefixes = str(args.get("prefixes", ""))
        if name == "get_context":
            return {
                "structure": {"name": "GAEC des Essais", "legal_form": "GAEC", "chart": "agri"},
                "regimes": {"vat_regime": "réel simplifié agricole", "ba_regime": "réel normal"},
                "fiscal_years": [
                    {"label": "2026", "start": "2026-01-01", "end": "2026-12-31", "status": "open"}
                ],
                "today": TODAY.isoformat(),
            }
        if name == "get_balances":
            book = {
                "60111": ("Engrais et amendements", "8450.00"),
                "60113": ("Produits phytosanitaires", "6120.00"),
                "60614": ("Carburants et lubrifiants", "4310.00"),
                "641": ("Rémunérations du personnel", "24500.00"),
                "645": ("Charges de sécurité sociale", "7400.00"),
                "646": ("Cotisations de l'exploitant", "7615.00"),
                "512": ("Banque", "24317.40"),
            }
            rows = [
                {"account": code, "label": label, "balance": amount, "unit": "€ HT"}
                for code, (label, amount) in book.items()
                if any(code.startswith(p) or p.startswith(code) for p in prefixes.split())
            ]
            return {"fiscal_year": "2026", "accounts": rows, "url": f"{link}/balance"}
        if name == "list_purchases":
            items = [
                {
                    "id": 31,
                    "supplier": "Coop de Saintonge",
                    "date": "2026-03-02",
                    "total_ttc": "3120.00",
                    "remaining": "0.00",
                    "state": "comptabilisée",
                },
                {
                    "id": 44,
                    "supplier": "Coop de Saintonge",
                    "date": "2026-09-15",
                    "total_ttc": "2160.00",
                    "remaining": "2160.00",
                    "state": "comptabilisée",
                },
                {
                    "id": 47,
                    "supplier": "Garage Martin",
                    "date": "2026-10-28",
                    "total_ttc": "1000.00",
                    "remaining": "1000.00",
                    "state": "comptabilisée",
                },
            ]
            if args.get("status") in ("unpaid", "overdue"):
                items = [i for i in items if i["remaining"] != "0.00"]
            if supplier := str(args.get("supplier") or "").lower():
                items = [i for i in items if supplier in i["supplier"].lower()]
            for item in items:
                item["url"] = f"{link}/purchases/{item['id']}"
            return {"items": items, "total": len(items), "amounts": "TTC"}
        if name == "find_party":
            return {"items": [{"id": 7, "name": "Coop de Saintonge", "kind": "fournisseur"}]}
        if name == "search_entries":
            return {"items": [], "total": 0}
        if name == "get_bank_accounts":
            return {
                "items": [
                    {"name": "Crédit Agricole courant", "balance": "18917.40"},
                    {"name": "Livret", "balance": "5400.00"},
                ],
                "total_balance": "24317.40",
                "url": f"{link}/bank",
            }
        if name == "list_bank_transactions":
            return {"items": [], "total": 0}
        if name == "get_agenda":
            return {
                "items": [
                    {"date": "2026-12-05", "label": "Acompte de TVA", "amount": "620.00"},
                    {
                        "date": "2026-12-15",
                        "label": "Appel de cotisations MSA",
                        "amount": "1900.00",
                    },
                    {"date": "2026-12-31", "label": "Fermage Dupont", "amount": "4200.00"},
                ]
            }
        if name == "get_vat":
            return {
                "regime": "réel simplifié agricole",
                "year": 2026,
                "vat_due": "1842.00",
                "deductible": "9310.00",
                "collected": "11152.00",
                "url": f"{link}/vat",
            }
        if name == "get_margins":
            return {
                "activities": [
                    {
                        "name": "Blé tendre",
                        "area_ha": 42.0,
                        "gross_margin": "17304.00",
                        "gross_margin_per_ha": "412.00",
                    }
                ]
            }
        if name == "list_sales":
            return {
                "items": [
                    {
                        "number": "V-2026-018",
                        "customer": "Moulin de Pons",
                        "total_ttc": "6900.00",
                        "paid": "0.00",
                        "status": "émise",
                        "url": f"{link}/sales/18",
                    }
                ],
                "total": 1,
            }
        if name == "get_msa":
            return {"year": 2026, "contributions": "7615.00", "base": "34200.00"}
        if name in ("get_tax_result", "get_aids", "list_assets", "get_closing_checks"):
            return {"items": []}
        return super()._result(name, args)


@dataclass
class Result:
    question: str
    tools: list[str]
    tool_ok: bool
    figure_ok: bool
    source_ok: bool
    ht_ttc_ok: bool
    answer: str
    cost_usd: float
    latency_s: float

    @property
    def passed(self) -> bool:
        return self.tool_ok and self.figure_ok and self.source_ok


def score(question: Question, called: list[str], answer: str) -> tuple[bool, bool, bool, bool]:
    lyvre = {name.removeprefix("lyvre_") for name in called if name.startswith("lyvre_")}
    tool_ok = bool(lyvre & question.tools)
    figure_ok = re.search(question.figure, answer) is not None
    source_ok = re.search(r"Source\s*:.*Lyvre", answer) is not None
    ht_ttc_ok = not question.amount or re.search(r"\b(HT|TTC)\b", answer) is not None
    return tool_ok, figure_ok, source_ok, ht_ttc_ok


async def ask(
    orchestrator: Orchestrator,
    client: LyvreClient,
    lyvre: EvalLyvre,
    question: Question,
    model: str,
) -> Result:
    gateway = RecordingGateway(orchestrator.gateway)
    orchestrator.gateway = gateway  # type: ignore[assignment]
    ctx = ToolContext(
        farm_id=uuid.uuid4(),
        farm_name="GAEC des Essais",
        role="owner",
        user_id=uuid.uuid4(),
        today=TODAY,
        skills={n: d.body for n, d in orchestrator.runtime.skills.items()},
        lyvre=client,
        lyvre_domains=frozenset(lyvre.reads),
    )
    provider = orchestrator.provider
    assert provider is not None
    started = time.monotonic()
    answer, usage = await orchestrator._loop(
        provider, ctx, [{"role": "user", "content": question.text}]
    )
    orchestrator.gateway = gateway.gateway
    called = [name for name, _args, _err in gateway.calls]
    tool_ok, figure_ok, source_ok, ht_ttc_ok = score(question, called, answer)
    return Result(
        question.text,
        called,
        tool_ok,
        figure_ok,
        source_ok,
        ht_ttc_ok,
        answer,
        round(cost(model, usage), 5),
        round(time.monotonic() - started, 2),
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=None)
    parser.add_argument("--effort", default=None)
    args = parser.parse_args()
    settings = Settings()
    configure_logging("WARNING")
    model = args.model or settings.llm_model
    provider = make_provider(settings, model, args.effort or settings.llm_effort)
    if provider is None:
        raise SystemExit(f"Clé d'API manquante pour {settings.llm_provider} dans .env")
    lyvre = EvalLyvre()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lyvre.handler)) as http:
        client = LyvreClient(http, URL, SecretValue(LYVRE_KEY))
        results = []
        for question in QUESTIONS:
            orchestrator = Orchestrator(
                None,  # type: ignore[arg-type]  # pas de base : ni historique ni mémoire
                None,  # type: ignore[arg-type]
                provider,
                ToolGateway(phase1_tools()),
                AgentRuntime.load(),
                max_iterations=settings.agent_max_iterations,
                model_label=model,
            )
            result = await ask(orchestrator, client, lyvre, question, model)
            results.append(result)
            flags = "".join(
                "✓" if ok else "✗"
                for ok in (result.tool_ok, result.figure_ok, result.source_ok, result.ht_ttc_ok)
            )
            print(f"{flags}  {result.latency_s:5.1f}s ${result.cost_usd:.3f}  {question.text}")
    passed = sum(r.passed for r in results)
    ht_ttc = sum(r.ht_ttc_ok for r in results)
    total = sum(r.cost_usd for r in results)
    print(
        f"\n{passed}/{len(results)} réussies (outil, chiffre, source) ; HT/TTC précisé : "
        f"{ht_ttc}/{len(results)} ; coût ${total:.3f}  (colonnes : outil chiffre source HT/TTC)"
    )
    REPORTS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report = REPORTS / f"compta-{stamp}-{model.replace('/', '_')}.json"
    report.write_text(
        json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"Rapport : {report}")


if __name__ == "__main__":
    asyncio.run(main())
