"""Évaluation compta (sans modèle) : questions cohérentes avec l'instantané et le faux Lyvre."""

import json
import re

from aiffel.connectors.lyvre.client import TOOL_SCHEMAS
from tests.eval.compta import QUESTIONS, EvalLyvre, score

# Arguments plausibles pour interroger le faux Lyvre comme le ferait l'agent.
ARGS = {
    "get_balances": {"prefixes": "60111 641 645 512 646"},
    "list_purchases": {"status": "unpaid"},
}


def test_ten_questions_whose_figure_lyvre_can_give() -> None:
    assert len(QUESTIONS) == 10 and sum(q.summed for q in QUESTIONS) == 3
    lyvre = EvalLyvre()
    for question in QUESTIONS:
        assert question.tools <= set(TOOL_SCHEMAS), question.text
        data = json.dumps(
            [lyvre._result(t, ARGS.get(t, {})) for t in question.tools], ensure_ascii=False
        )
        digits = re.sub(r"\[\\s ]\?", "", question.figure)
        if question.amount and not question.summed:
            assert digits in data.replace(".00", ""), question.text


def test_score() -> None:
    question = QUESTIONS[0]
    good = "8 450,00 € HT d'engrais sur l'exercice 2026.\nSource : Lyvre (Comptabilité)"
    assert score(question, ["lyvre_get_context", "lyvre_get_balances"], good) == (
        True,
        True,
        True,
        True,
    )
    assert score(question, ["lexicon_get_input_price"], "Environ 8 000 €.") == (
        False,
        False,
        False,
        False,
    )
