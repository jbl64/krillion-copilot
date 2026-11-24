"""MANUAL, real-API check (not collected by default: filename does not match test_*.py).
Run by hand:  KRILLION_REAL_LLM=1 python -m pytest tests/manual_decide_real.py -s
Costs ~10 one-token calls. Checks that D dominates for invented Q-words and A/B for real ones."""
import os

import pytest

if os.getenv("KRILLION_REAL_LLM") != "1":
    pytest.skip("set KRILLION_REAL_LLM=1 to run against the real API", allow_module_level=True)

os.environ["KRILLION_FAKE_LLM"] = "0"

from server import llm  # noqa: E402
from server.graph import DECIDE_SYSTEM, p_valid_from_probs  # noqa: E402

PROMPT = "Prompt: a country in the world"
FAKES = ["Qatarz", "Qumbaland", "Qeloria"]
REALS = ["Qatar", "Chile", "Mozambique"]


def probs(answer):
    p, _ = llm.call_choice("decide", DECIDE_SYSTEM, f"{PROMPT}\nCandidate: {answer}")
    print(f"{answer:12s} " + " ".join(f"{k}={v:.2f}" for k, v in p.items()) + f"  P(valid)={p_valid_from_probs(p):.2f}")
    return p


def test_fake_words_get_d_and_real_words_do_not():
    fake = {a: probs(a) for a in FAKES}
    real = {a: probs(a) for a in REALS}
    for a, p in fake.items():
        assert max(p, key=p.get) == "D", a
    for a, p in real.items():
        assert max(p, key=p.get) in "AB", a
