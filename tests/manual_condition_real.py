"""MANUAL, real-API experiment (not collected by default). Does a narrow per-condition question separate answers that
meet a written condition from ones that don't?
Run by hand:  KRILLION_REAL_LLM=1 python -m pytest tests/manual_condition_real.py -s
Costs ~40 one-token calls."""
import os

import pytest

if os.getenv("KRILLION_REAL_LLM") != "1":
    pytest.skip("set KRILLION_REAL_LLM=1 to run against the real API", allow_module_level=True)

os.environ["KRILLION_FAKE_LLM"] = "0"

from server import llm  # noqa: E402

SYSTEM = """You are a strict fact-checker. Answer one question about a candidate answer from a trivia game.
Choose one: A) Yes, clearly  B) Probably  C) Unsure  D) No. Answer with one letter."""

# (condition, [(candidate, expected: True meets / False fails)])  expectations are from my knowledge, unverified
CASES = [
    ("has its own emoji", [("taco", True), ("croissant", True), ("tequila", False), ("sake", True),
                           ("durian", False), ("mochi", False), ("edamame", False), ("chopsticks", True),
                           ("pastry", False), ("pizza", True)]),
    ("is a food or drink", [("durian", True), ("chopsticks", False), ("taco", True), ("fork", False)]),
]


def letter_probs(condition, cand):
    q = f"Does the item '{cand}' satisfy this condition: {condition}?"
    p, _ = llm.call_choice("condition", SYSTEM, q)
    return p


def test_print_condition_probabilities():
    for cond, cands in CASES:
        print(f"\ncondition: {cond}")
        for cand, expected in cands:
            p = letter_probs(cond, cand)
            top = max(p, key=p.get)
            meets = p["A"] + p["B"] > p["C"] + p["D"]
            flag = "" if meets == expected else "   <-- disagrees with my expectation"
            print(f"  {cand:11s} " + " ".join(f"{k}={v:.2f}" for k, v in p.items()) + f"  top={top}{flag}")
