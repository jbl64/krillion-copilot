"""MANUAL, real-API hallucination probe (not collected by default: filename does not match test_*.py).
Run by hand:  KRILLION_REAL_LLM=1 python -m pytest tests/manual_hallucination_real.py -s
Choose the validator with HALLUC_ARM=validate (default, the wired batched node) or HALLUC_ARM=decide.
Feeds tests/hallucination_set.json (real controls + invented answers) to the validator and reports, per kind of answer:
  kept     = share with p_valid >= 0.5 (shown in the panel)
  confident= share with p_valid >= 0.9 (ranked ahead of the unsure ones)
  mean p   = average p_valid
For the fakes, 'kept' is the hallucination leak rate (lower is better); for the reals it is the keep rate (higher is better).
About 8 prompts x 8 answers; one batched call per prompt for validate, one call per answer for decide."""
import json
import os
import random
from pathlib import Path

import pytest

if os.getenv("KRILLION_REAL_LLM") != "1":
    pytest.skip("set KRILLION_REAL_LLM=1 to run against the real API", allow_module_level=True)

os.environ["KRILLION_FAKE_LLM"] = "0"

from server import graph  # noqa: E402

ARM = os.getenv("HALLUC_ARM", "validate")
DATA = json.loads((Path(__file__).parent / "hallucination_set.json").read_text())


def score_prompt(entry):
    items = [(a, k) for k in ("real", "plausible_fake", "nonsense_fake") for a in entry[k]]
    random.Random(entry["prompt"]).shuffle(items)  # fixed order, so real and fake answers are mixed
    state = {"prompt": entry["prompt"], "category": "open_category", "calls": [],
             "candidates": [{"answer": a, "why": "", "rank": i} for i, (a, _) in enumerate(items, 1)]}
    fn = graph.validate if ARM == "validate" else graph.decide
    out = {}
    for i in range(0, len(items), 8):  # decide scores at most 8 per call
        chunk = {**state, "candidates": state["candidates"][i:i + 8]}
        r = fn(chunk)
        for c in r["candidates"] + r["dropped"]:
            out[c["answer"]] = c["p_valid"]
    return [(a, k, out.get(a)) for a, k in items]


def test_print_hallucination_rates():
    rows = [(entry["prompt"], *x) for entry in DATA["prompts"] for x in score_prompt(entry)]
    print(f"\narm: {ARM}")
    for kind in ("real", "plausible_fake", "nonsense_fake"):
        ps = [p for _, _, k, p in rows if k == kind]
        known = [p for p in ps if p is not None]
        print(f"{kind:15s} n={len(ps):3d} kept={sum(p >= 0.5 for p in known) / max(len(known), 1):4.0%} "
              f"confident={sum(p >= 0.9 for p in known) / max(len(known), 1):4.0%} "
              f"mean p={sum(known) / max(len(known), 1):.2f}  unscored={len(ps) - len(known)}")
    print("\nfakes that were kept (p_valid >= 0.5) and reals that were dropped:")
    for prompt, a, k, p in rows:
        if p is not None and ((k != "real" and p >= 0.5) or (k == "real" and p < 0.5)):
            print(f"  [{k}] {prompt[:42]:42s} {a:34s} p={p:.2f}")
