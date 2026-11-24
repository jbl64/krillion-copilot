import json
from pathlib import Path

DATA = json.loads((Path(__file__).parent / "hallucination_set.json").read_text())


def test_hallucination_set_is_well_formed():
    assert len(DATA["prompts"]) >= 6
    seen = set()
    for e in DATA["prompts"]:
        assert len(e["real"]) >= 3 and len(e["plausible_fake"]) + len(e["nonsense_fake"]) >= 3
        answers = [a.lower() for k in ("real", "plausible_fake", "nonsense_fake") for a in e[k]]
        assert len(answers) == len(set(answers)), f"duplicate answer in {e['prompt']}"
        assert len(answers) <= 16
        seen.add(e["prompt"])
    assert len(seen) == len(DATA["prompts"])
