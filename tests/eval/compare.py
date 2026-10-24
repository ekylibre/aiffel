"""Compare plusieurs exécutions (rapports CSV) avec la notation actuelle, sans rappeler de modèle.

    uv run python -m tests.eval.compare tests/eval/reports/*.csv --today 2026-10-24

Les rapports d'un même modèle (reprise des phrases manquantes) sont fusionnés. Un modèle n'est
comparé que sur les phrases jouées par tous, pour que les scores portent sur le même échantillon.
"""

import argparse
import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from tests.eval.golden import FIELDS, Row, build_farm, load_corpus, load_procedures, rescore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path, nargs="+")
    parser.add_argument("--today", type=date.fromisoformat, required=True)
    parser.add_argument("--common", action="store_true", help="phrases jouées par tous seulement")
    args = parser.parse_args()

    corpus = {entry.text: entry for entry in load_corpus()}
    farm = build_farm(list(corpus.values()), load_procedures())
    lines: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for report in args.reports:
        for line in csv.DictReader(report.open(encoding="utf-8")):
            if "brut" in line and line["phrase"] in corpus:
                lines[line["modele"]][line["phrase"]] = line
    phrases = set.intersection(*(set(v) for v in lines.values())) if args.common else None

    table: list[dict[str, Any]] = []
    for model, by_phrase in lines.items():
        rows = []
        for text, line in by_phrase.items():
            if phrases is not None and text not in phrases:
                continue
            predicted, scores, _ = rescore(corpus[text], json.loads(line["brut"]), farm, args.today)
            rows.append(
                Row(
                    text=text,
                    expected_intent=corpus[text].group,
                    predicted_intent=predicted,
                    scores=scores,
                    proposal=line["proposition"],
                    latency_s=float(line["latence_s"]),
                    cost_usd=float(line["cout_usd"]),
                )
            )
        records = [r for r in rows if r.expected_intent == "record"]
        intent = sum(r.predicted_intent == r.expected_intent for r in rows) / len(rows)
        proposals = sum(r.proposal != "non" for r in records) / max(1, len(records))

        def rate(name: str, rows: list[Row] = rows) -> str:
            got = [r.scores[name] for r in rows if r.scores[name]]
            return f"{sum(s == 'ok' for s in got) / len(got):.0%}" if got else "-"

        table.append(
            {
                "modèle": model,
                "phrases": len(rows),
                "intention": f"{intent:.0%}",
                "propositions": f"{proposals:.0%}",
                **{name: rate(name) for name in FIELDS},
                "coût/phrase $": f"{sum(r.cost_usd for r in rows) / len(rows):.4f}",
                "latence s": f"{sum(r.latency_s for r in rows) / len(rows):.1f}",
            }
        )
    table.sort(key=lambda row: row["modèle"])
    headers = list(table[0])
    print("| " + " | ".join(headers) + " |")
    print("|" + "---|" * len(headers))
    for row in table:
        print("| " + " | ".join(str(row[h]) for h in headers) + " |")


if __name__ == "__main__":
    main()
