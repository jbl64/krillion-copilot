import os
import sqlite3

os.environ["KRILLION_FAKE_LLM"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from server import db, llm  # noqa: E402
from server.app import app  # noqa: E402
from server.graph import rule_check  # noqa: E402
from server.rules import build_rule, normalize, passes  # noqa: E402

client = TestClient(app)
Q_OR_NO_E = "q|^[^e]*$"


def test_normalize():
    assert normalize("São Tomé & Príncipe") == "saotomeprincipe"
    assert normalize("R2-D2") == "rd"


def test_q_or_no_e_rule_matches_the_dominica_case():
    assert passes(Q_OR_NO_E, "Dominica")      # no E
    assert passes(Q_OR_NO_E, "Mozambique")    # has E but also Q
    assert passes(Q_OR_NO_E, "Iraq")
    assert not passes(Q_OR_NO_E, "Egypt")     # E and no Q


def test_build_rule_statuses():
    good = {"regex": Q_OR_NO_E, "pass": ["Iraq", "Fiji"], "fail": ["Egypt", "Chile"]}
    assert build_rule(good) == {"regex": Q_OR_NO_E, "status": "ok"}
    assert build_rule(None)["status"] == "missing"
    assert build_rule({"regex": "", "pass": [], "fail": []})["status"] == "missing"
    assert build_rule({**good, "regex": "("})["status"] == "invalid"
    assert build_rule({**good, "fail": ["Iraq", "Egypt"]})["status"] == "selftest_failed"  # sample disagrees
    assert build_rule({**good, "pass": []})["status"] == "selftest_failed"  # no samples: not trusted


def test_rule_check_drops_failures_only_when_rule_ok():
    cands = [{"answer": a, "why": "", "rank": i} for i, a in enumerate(["Dominica", "Egypt", "Qatar"], 1)]
    base = {"category": "constraint", "candidates": cands}
    out = rule_check({**base, "rule": {"regex": Q_OR_NO_E, "status": "ok"}})
    assert [c["answer"] for c in out["candidates"]] == ["Dominica", "Qatar"]
    assert out["dropped"][0]["answer"] == "Egypt" and "rule" in out["dropped"][0]["valid_reason"]
    # rule not enforced: nothing dropped, candidates unchanged (only truncated)
    assert rule_check({**base, "rule": {"regex": Q_OR_NO_E, "status": "selftest_failed"}}) == {"candidates": cands}
    assert rule_check({**base}) == {"candidates": cands}
    assert rule_check({**base, "category": "entity", "rule": {"regex": Q_OR_NO_E, "status": "ok"}}) == {}


def test_suggest_constraint_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    real = llm._fake_reply
    seen = {}

    def fake(node, user):
        if node == "route":
            return {"category": "constraint", "plan": "x", "subject": "a country",
                    "rule": {"regex": Q_OR_NO_E, "pass": ["Iraq", "Fiji"], "fail": ["Egypt", "Chile"]}}
        if node == "generate":
            return {"candidates": [{"answer": a, "why": "x"} for a in ["Dominica", "Egypt", "Mozambique"]]}
        seen[node] = user
        return real(node, user)

    monkeypatch.setattr(llm, "_fake_reply", fake)
    _record_decide(monkeypatch, seen)
    data = client.post("/suggest", json={"prompt": "Name a country with Q or no E", "mode": "t"}).json()
    assert [c["answer"] for c in data["candidates"]] == ["Dominica", "Mozambique"]
    assert [c["answer"] for c in data["dropped"]] == ["Egypt"]
    assert data["rule"]["status"] == "ok"
    assert "a country" in seen["decide"] and "Egypt" not in seen["decide"]
    assert "Q or no E" not in seen["decide"]  # the letter rule never reaches the validator
    row = sqlite3.connect(tmp_path / "t.db").execute("SELECT rule FROM rounds").fetchone()
    assert '"status": "ok"' in row[0]
    rows = sqlite3.connect(tmp_path / "t.db").execute("SELECT answer, dropped FROM candidates").fetchall()
    assert ("Egypt", 1) in rows and len(rows) == 3


def test_failed_selftest_falls_back_to_validator(monkeypatch):
    real = llm._fake_reply
    monkeypatch.setattr(llm, "_fake_reply", lambda node, user: (
        {"category": "constraint", "plan": "x", "rule": {"regex": "^q", "pass": ["Iraq"], "fail": ["Egypt"]}}
        if node == "route" else real(node, user)))
    data = client.post("/suggest", json={"prompt": "starts with Q", "mode": "t"}).json()
    assert data["rule"]["status"] == "selftest_failed"
    assert len(data["candidates"]) == 3 and data["dropped"] == []


def _record_decide(monkeypatch, seen):
    real_choice = llm._fake_choice

    def rec(user):
        seen["decide"] = seen.get("decide", "") + user + "\n"
        return real_choice(user)

    monkeypatch.setattr(llm, "_fake_choice", rec)


def _route_fake(monkeypatch, first_rule, repaired_rule, seen, subject=None):
    real = llm._fake_reply
    _record_decide(monkeypatch, seen)

    def fake(node, user):
        if node == "route":
            out = {"category": "constraint", "plan": "x", "rule": first_rule}
            if subject:
                out["subject"] = subject
            return out
        if node == "rule_repair":
            seen["repair_user"] = user
            return repaired_rule
        if node == "generate":
            return {"candidates": [{"answer": a, "why": "x"} for a in ["otter", "tiger", "quoll", "elephant"]]}
        seen[node] = user
        return real(node, user)

    monkeypatch.setattr(llm, "_fake_reply", fake)


def test_repair_fixes_mislabeled_sample_and_enforces_rule(monkeypatch):
    seen = {}
    _route_fake(monkeypatch,
                {"regex": "^[a-z]{5}$", "pass": ["otter", "zebra"], "fail": ["tiger", "elephant"]},  # tiger mislabeled
                {"regex": "^[a-z]{5}$", "pass": ["otter", "zebra"], "fail": ["elephant", "giraffe"]}, seen)
    data = client.post("/suggest", json={"prompt": "5 letter animal", "mode": "t"}).json()
    assert data["rule"] == {"regex": "^[a-z]{5}$", "status": "ok", "repaired": True}
    assert '"tiger" (you labeled it fail)' in seen["repair_user"] and "MATCHES" in seen["repair_user"]
    assert [c["node"] for c in data["calls"]].count("rule_repair") == 1
    assert [c["answer"] for c in data["candidates"]] == ["otter", "tiger", "quoll"]
    assert [c["answer"] for c in data["dropped"]] == ["elephant"]


def test_failed_repair_keeps_original_status_and_validator_does_not_judge_letters(monkeypatch):
    seen = {}
    _route_fake(monkeypatch,
                {"regex": "^[a-z]{5}$", "pass": ["otter"], "fail": ["tiger"]}, {"regex": "((", "pass": [], "fail": []}, seen)
    data = client.post("/suggest", json={"prompt": "5 letter animal", "mode": "t"}).json()
    assert data["rule"]["status"] == "selftest_failed" and data["rule"]["repaired"] is True
    assert len(data["candidates"]) == 4 and data["dropped"] == []  # nothing dropped on letter claims
    assert "5 letter animal" in seen["decide"]  # no subject returned -> full-prompt fallback


def test_validator_gets_subject_not_rule_even_when_rule_unchecked(monkeypatch):
    seen = {}
    _route_fake(monkeypatch,
                {"regex": "^[a-z]{5}$", "pass": ["otter"], "fail": ["tiger"]}, {"regex": "((", "pass": [], "fail": []},
                seen, subject="an animal")
    data = client.post("/suggest", json={"prompt": "animal with exactly 5 letters", "mode": "t"}).json()
    assert data["rule"]["status"] == "selftest_failed"
    assert "an animal" in seen["decide"] and "5 letters" not in seen["decide"]


def test_no_repair_call_when_rule_ok(monkeypatch):
    seen = {}
    _route_fake(monkeypatch,
                {"regex": "^[a-z]{5}$", "pass": ["otter"], "fail": ["elephant"]}, {}, seen)
    data = client.post("/suggest", json={"prompt": "5 letter animal", "mode": "t"}).json()
    assert data["rule"] == {"regex": "^[a-z]{5}$", "status": "ok"}
    assert "rule_repair" not in {c["node"] for c in data["calls"]}
