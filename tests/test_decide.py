import math
import os

import pytest

os.environ["KRILLION_FAKE_LLM"] = "1"

from server import llm  # noqa: E402
from server.graph import VALIDITY_WEIGHTS, decide, p_valid_from_probs  # noqa: E402


def test_choice_probs_normalizes_over_letters_and_ignores_other_tokens():
    top = [("D", math.log(0.6)), (" D", math.log(0.2)), ("A", math.log(0.1)), ("The", math.log(0.05))]
    probs = llm.choice_probs(top)
    assert probs["D"] == pytest.approx(0.8 / 0.9) and probs["A"] == pytest.approx(0.1 / 0.9)
    assert probs["B"] == 0.0 and sum(probs.values()) == pytest.approx(1.0)


def test_choice_probs_raises_without_any_letter():
    with pytest.raises(llm.NoLogprobs):
        llm.choice_probs([("The", -0.1), ("I", -2.0)])


def test_p_valid_weights():
    assert p_valid_from_probs({"A": 1.0}) == 1.0
    assert p_valid_from_probs({"D": 1.0}) == 0.0
    assert p_valid_from_probs({"A": 0.5, "B": 0.5, "C": 0, "D": 0}) == pytest.approx(0.5 * VALIDITY_WEIGHTS["A"] + 0.5 * VALIDITY_WEIGHTS["B"])


def _state(*answers):
    return {"prompt": "p", "category": "open_category", "calls": [],
            "candidates": [{"answer": a, "why": "", "rank": i} for i, a in enumerate(answers, 1)]}


def test_decide_ranks_by_expected_points_and_drops_invalid(monkeypatch):
    dist = {"Alpha": {"B": 1.0}, "Bravo": {"A": 1.0}, "Charlie": {"D": 1.0}}
    monkeypatch.setattr(llm, "_fake_choice", lambda user: dist[user.split("Candidate: ")[1]])
    out = decide(_state("Alpha", "Bravo", "Charlie"))
    # Alpha at position 0: 0.7*85 = 59.5; Bravo at position 1: 1.0*85 = 85 -> Bravo first
    assert [c["answer"] for c in out["candidates"]] == ["Bravo", "Alpha"]
    assert out["candidates"][0]["expected_pts"] == 85.0 and out["candidates"][1]["expected_pts"] == 59.5
    assert [c["answer"] for c in out["dropped"]] == ["Charlie"]
    assert [c["node"] for c in out["calls"]] == ["decide"] * 3


def test_decide_fails_open_on_api_error_but_not_on_missing_logprobs(monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("x")
    monkeypatch.setattr(llm, "_fake_choice", boom)
    out = decide(_state("Alpha", "Bravo"))
    assert [c["answer"] for c in out["candidates"]] == ["Alpha", "Bravo"]
    assert all(c["p_valid"] is None and c["expected_pts"] is None for c in out["candidates"])

    def nolp(*a, **k):
        raise llm.NoLogprobs("none")
    monkeypatch.setattr(llm, "_fake_choice", nolp)
    with pytest.raises(llm.NoLogprobs):
        decide(_state("Alpha"))


def test_decide_skips_when_no_candidates():
    assert decide(_state()) == {}
