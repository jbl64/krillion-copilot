import os

os.environ["KRILLION_FAKE_LLM"] = "1"

from server import llm  # noqa: E402
from server.graph import FINAL_N, OVERGEN_N, generate, rule_check  # noqa: E402
from server.llm import parse_json  # noqa: E402


def fake_generate(monkeypatch, answers, seen=None):
    def fake(node, user):
        if seen is not None:
            seen[node] = user
        return {"candidates": [{"answer": a, "why": "x"} for a in answers]}
    monkeypatch.setattr(llm, "_fake_reply", fake)


def state(category="open_category"):
    return {"prompt": "p", "category": category, "plan": "", "calls": []}


def test_generate_dedupes_and_truncates(monkeypatch):
    answers = [f"Item{chr(97 + i % 15)}{i % 15}" for i in range(30)] + ["ITEMA0", "  itema0 "]
    fake_generate(monkeypatch, answers)
    out = generate(state())["candidates"]
    assert len(out) == FINAL_N
    assert len({c["answer"].lower() for c in out}) == FINAL_N
    assert [c["rank"] for c in out] == list(range(1, FINAL_N + 1))
    assert [c["answer"] for c in out] == answers[:FINAL_N]  # first (most niche) kept, order preserved


def test_generate_dedupe_ignores_case_and_spacing(monkeypatch):
    fake_generate(monkeypatch, ["Sri Lanka", "sri  lanka", "SRILANKA", "Togo"])
    assert [c["answer"] for c in generate(state())["candidates"]] == ["Sri Lanka", "Togo"]


def test_constraint_overgenerates_and_rule_check_truncates_in_order(monkeypatch):
    seen = {}
    fake_generate(monkeypatch, [f"q{chr(97 + i)}{chr(97 + i)}" for i in range(26)], seen)  # all start with q, 20 kept
    out = generate(state("constraint"))
    assert f"Number of answers: {OVERGEN_N}" in seen["generate"]
    assert len(out["candidates"]) == OVERGEN_N
    rule = {"regex": "^[a-z]{3}$", "status": "ok"}
    out2 = rule_check({"category": "constraint", "rule": rule, "candidates": out["candidates"]})
    assert [c["rank"] for c in out2["candidates"]] == list(range(1, FINAL_N + 1))
    assert out2["dropped"] == []


def test_non_constraint_requests_final_n(monkeypatch):
    seen = {}
    fake_generate(monkeypatch, ["a1"], seen)
    generate(state())
    assert f"Number of answers: {FINAL_N}" in seen["generate"]


def test_parse_json_salvages_truncated_reply():
    cut = '{"candidates": [{"answer": "A", "why": "x"}, {"answer": "B", "why": "y"}, {"answer": "C", "wh'
    assert [c["answer"] for c in parse_json(cut)["candidates"]] == ["A", "B"]
    assert parse_json('{"candidates": [{"answer": "A", "why": "x"}]}')["candidates"][0]["answer"] == "A"
    assert parse_json("not json at all") == {}
    assert parse_json("") == {}


def test_generate_strips_trailing_clarifier_and_dedupes(monkeypatch):
    fake_generate(monkeypatch, ["Scooter (kick scooter)", "scooter", "City of God (2002)", "(only parens)", "Pogo stick"])
    out = [c["answer"] for c in generate(state())["candidates"]]
    assert out == ["Scooter", "City of God", "(only parens)", "Pogo stick"]


def test_data_property_keeps_numeric_value_other_categories_unchanged(monkeypatch):
    monkeypatch.setattr(llm, "_fake_reply", lambda node, user: {
        "condition": {"unit": "km2", "min": None, "max": None},
        "candidates": [{"answer": "Poland", "why": "x", "value": 312696}]})
    assert generate(state("data_property"))["candidates"][0]["value"] == "312,696 km2"
    assert "value" not in generate(state())["candidates"][0]  # other categories unchanged


def test_data_property_asks_for_final_n_and_compares_numbers_in_code(monkeypatch):
    seen = {}
    cond = {"unit": "km2", "min": 243610, "max": 1001450, "ref": "UK 243,610; Egypt 1,001,450"}
    cands = [{"answer": f"C{chr(97 + i)}", "why": "x", "value": 300000 if i % 2 == 0 else 100000} for i in range(20)]
    cands.insert(1, {"answer": "Unknown", "why": "x", "value": None})   # no number: kept, unchecked
    monkeypatch.setattr(llm, "_fake_reply", lambda node, user: (seen.__setitem__(node, user) or
                                                                  {"condition": cond, "satisfying": ["Ca"], "candidates": cands}))
    out = generate(state("data_property"))
    assert f"Number of answers: {FINAL_N}" in seen["generate"]
    assert [c["answer"] for c in out["candidates"]] == ["Ca", "Unknown", "Cc", "Ce", "Cg"]  # first 8 proposals, filtered
    assert out["candidates"][0]["value"] == "300,000 km2"
    assert [d["answer"] for d in out["dropped"]] == ["Cb", "Cd", "Cf"]
    assert "outside" in out["dropped"][0]["valid_reason"]


def test_data_property_without_numeric_condition_checks_nothing(monkeypatch):
    monkeypatch.setattr(llm, "_fake_reply", lambda node, user: {
        "condition": {"unit": "", "min": None, "max": None},
        "candidates": [{"answer": "Red beet", "why": "x", "value": 5}, {"answer": "Radish", "why": "x"}]})
    out = generate(state("data_property"))
    assert len(out["candidates"]) == 2 and out["dropped"] == []


def test_dedupe_keeps_answers_that_differ_only_by_digits(monkeypatch):
    fake_generate(monkeypatch, ["Apollo 11", "apollo-13", "APOLLO 11", "Music Disc 13"])
    assert [c["answer"] for c in generate(state())["candidates"]] == ["Apollo 11", "apollo-13", "Music Disc 13"]
