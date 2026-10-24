"""Re-note un rapport CSV avec la notation actuelle, sans rappeler le modèle.

uv run python -m tests.eval.rescore tests/eval/reports/<rapport>.csv --today 2026-10-24
"""

import argparse
import csv
import json
from datetime import date
from pathlib import Path

from tests.eval.golden import (
    DUKE_CORPUS,
    Row,
    build_farm,
    load_corpus,
    load_procedures,
    rescore,
    summarize,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--today", type=date.fromisoformat, required=True)
    parser.add_argument("--corpus", type=Path, default=DUKE_CORPUS)
    args = parser.parse_args()

    corpus = load_corpus(args.corpus)
    by_text = {entry.text: entry for entry in corpus}
    farm = build_farm(corpus, load_procedures())
    rows = []
    with args.report.open(encoding="utf-8") as handle:
        for line in csv.DictReader(handle):
            entry = by_text[line["phrase"]]
            predicted, scores, procedure = rescore(
                entry, json.loads(line["brut"]), farm, args.today
            )
            rows.append(
                Row(
                    text=entry.text,
                    expected_intent=entry.group,
                    predicted_intent=predicted,
                    scores=scores,
                    procedure=procedure,
                    proposal=line["proposition"],
                    latency_s=float(line["latence_s"]),
                    cost_usd=float(line["cout_usd"]),
                )
            )
    print(json.dumps(summarize(rows), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
