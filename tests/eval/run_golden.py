"""Lance l'évaluation sur le corpus de Duke avec un vrai modèle (coûte des appels à l'API).

    uv run python -m tests.eval.run_golden --limit 20
    uv run python -m tests.eval.run_golden --model claude-sonnet-5-5 --effort low
    uv run python -m tests.eval.run_golden --provider openrouter --model qwen/qwen3.8-flash

Rapport CSV par exécution dans ``tests/eval/reports/`` (modèle, coût, latence, score par champ),
et résumé affiché à la fin. Aucune écriture dans Ekylibre : voir ``golden.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
from datetime import date, datetime
from pathlib import Path

import structlog

from aiffel.app.container import make_provider
from aiffel.connectors.lexicon.client import LexiconClient
from aiffel.logging import configure_logging
from aiffel.orchestrator.agent import Orchestrator
from aiffel.orchestrator.llm import LLMError
from aiffel.orchestrator.runtime import AgentRuntime
from aiffel.settings import Settings
from aiffel.tools.catalog import phase1_tools, propose_tools
from aiffel.tools.registry import ToolGateway
from tests.eval.golden import (
    DUKE_CORPUS,
    FIELDS,
    Entry,
    Row,
    build_farm,
    load_corpus,
    load_procedures,
    run_entry,
    summarize,
)

REPORTS = Path(__file__).parent / "reports"
log = structlog.get_logger()


def pick(corpus: list[Entry], limit: int | None, seed: int) -> list[Entry]:
    """Échantillon stratifié : toutes les phrases hors enregistrement, puis des enregistrements."""
    if limit is None or limit >= len(corpus):
        return corpus
    others = [e for e in corpus if e.group != "record"]
    records = [e for e in corpus if e.group == "record"]
    random.Random(seed).shuffle(records)  # noqa: S311 (échantillon reproductible)
    keep_others = others[: max(1, limit // 4)]
    return keep_others + records[: limit - len(keep_others)]


def write_csv(rows: list[Row], path: Path, model: str, effort: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "modele",
                "effort",
                "phrase",
                "intention_attendue",
                "intention_obtenue",
                *FIELDS,
                "procedure_choisie",
                "proposition",
                "outils",
                "latence_s",
                "jetons_entree",
                "jetons_sortie",
                "cache_lu",
                "cache_ecrit",
                "cout_usd",
                "reponse",
                "brut",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    model,
                    effort,
                    r.text,
                    r.expected_intent,
                    r.predicted_intent,
                    *(r.scores[f] for f in FIELDS),
                    r.procedure,
                    r.proposal,
                    r.tools,
                    r.latency_s,
                    r.usage.input_tokens,
                    r.usage.output_tokens,
                    r.usage.cache_read_tokens,
                    r.usage.cache_write_tokens,
                    r.cost_usd,
                    r.answer.replace("\n", " ")[:500],
                    json.dumps(r.raw, ensure_ascii=False, default=str),
                ]
            )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DUKE_CORPUS)
    parser.add_argument("--limit", type=int, default=None, help="nombre de phrases (échantillon)")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--model", default=None, help="par défaut AIFFEL_LLM_MODEL")
    parser.add_argument("--effort", default=None, help="par défaut AIFFEL_LLM_EFFORT")
    parser.add_argument("--today", type=date.fromisoformat, default=date.today())
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--no-lexicon", action="store_true")
    parser.add_argument("--provider", choices=("anthropic", "openrouter"), default=None)
    parser.add_argument("--budget", type=float, default=5.0, help="plafond de dépense en $")
    args = parser.parse_args()

    settings = Settings()
    if args.provider:
        settings = settings.model_copy(update={"llm_provider": args.provider})
    configure_logging("WARNING")
    model = args.model or settings.llm_model
    effort = args.effort or settings.llm_effort
    if make_provider(settings, model, effort) is None:
        raise SystemExit(f"Clé d'API manquante pour {settings.llm_provider} dans .env")

    corpus = pick(load_corpus(args.corpus), args.limit, args.seed)
    procedures = load_procedures()
    if len(procedures) < 10:
        print("⚠️ fixtures/procedures.json absent : 2 procédures seulement (snapshot_procedures).")
    farm = build_farm(load_corpus(args.corpus), procedures)
    spent = 0.0
    lexicon = None if args.no_lexicon else LexiconClient(settings.lexicon_mcp_url)
    runtime = AgentRuntime.load()
    semaphore = asyncio.Semaphore(args.concurrency)

    async def one(entry: Entry, http: object) -> Row | None:
        nonlocal spent
        async with semaphore:
            if spent >= args.budget:
                return None  # plafond atteint : les phrases restantes ne sont pas jouées
            # Un orchestrateur par phrase : la passerelle instrumentée lui est propre.
            orchestrator = Orchestrator(
                None,  # type: ignore[arg-type]  # pas de base : ni historique ni mémoire
                None,  # type: ignore[arg-type]
                make_provider(settings, model, effort),  # type: ignore[arg-type]
                ToolGateway([*phase1_tools(), *propose_tools()]),
                runtime,
                lexicon=lexicon,
                max_iterations=settings.agent_max_iterations,
                model_label=model,
            )
            try:
                row = await run_entry(
                    orchestrator, farm, http, entry, today=args.today, model=model, lexicon=lexicon
                )  # type: ignore[arg-type]
            except LLMError as exc:
                print(f"⚠️ {exc} : {entry.text[:60]}")
                if "402" in str(exc):  # plus de crédit : inutile de continuer
                    spent = args.budget
                return None
            spent += row.cost_usd
            mark = "✓" if row.predicted_intent == row.expected_intent else "✗"
            print(f"{mark} {row.latency_s:5.1f}s ${row.cost_usd:.3f}  {entry.text[:70]}")
            return row

    async with farm.client() as http:
        results = await asyncio.gather(*(one(entry, http) for entry in corpus))
    rows = [row for row in results if row is not None]
    if len(rows) < len(corpus):
        print(f"⚠️ {len(rows)}/{len(corpus)} phrases jouées (plafond, crédit ou erreurs)")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = REPORTS / f"{stamp}-{model.replace('/', '_')}-{effort}.csv"
    write_csv(rows, path, model, effort)
    summary = summarize(rows)
    print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    print(f"Rapport : {path}")


if __name__ == "__main__":
    asyncio.run(main())
