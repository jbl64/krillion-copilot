import os
import sqlite3

os.environ["KRILLION_FAKE_LLM"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from server import db, llm  # noqa: E402
from server.app import app  # noqa: E402
from server.graph import validate  # noqa: E402

client = TestClient(app)
ANSWERS = ["Alpha", "Bravo", "Charlie", "Delta"]
STATE = {"prompt": "p", "category": "open_category", "calls": [],
         "candidates": [{"answer": a, "why": "", "rank": i} for i, a in enumerate(ANSWERS, 1)]}


def fake_validate(monkeypatch, reply):
    real = llm._fake_reply
    monkeypatch.setattr(llm, "_fake_reply", lambda node, user: reply if node == "validate" else real(node, user))


def test_validate_drops_low_p_valid_and_keeps_order(monkeypatch):
    fake_validate(monkeypatch, {"results": [
        {"i": 1, "p_valid": 0.9}, {"i": 2, "p_valid": 0.2, "reason": "invented"},
        {"i": 3, "p_valid": 0.5}, {"i": 4, "p_valid": 1.7}]})
    out = validate(STATE)
    assert [c["answer"] for c in out["candidates"]] == ["Alpha", "Delta", "Charlie"]  # Charlie (0.5) is unsure: last
    assert [c["answer"] for c in out["dropped"]] == ["Bravo"]
    assert out["dropped"][0]["valid_reason"] == "invented"
    assert out["candidates"][1]["p_valid"] == 1.0  # clamped (Delta)
    assert out["calls"][-1]["node"] == "validate"


def test_validate_fails_open_on_bad_reply(monkeypatch):
    fake_validate(monkeypatch, {"results": [{"i": 2, "p_valid": "nope"}, {"oops": 1}]})
    out = validate(STATE)
    assert len(out["candidates"]) == 4 and out["dropped"] == []
    assert all(c["p_valid"] is None for c in out["candidates"])


def test_validate_skips_llm_when_no_candidates():
    assert validate({"prompt": "p", "category": "open_category", "candidates": []}) == {}


def test_suggest_returns_and_logs_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    fake_validate(monkeypatch, {"results": [
        {"i": 1, "p_valid": 0.9}, {"i": 2, "p_valid": 0.1}, {"i": 3, "p_valid": 0.8}]})
    data = client.post("/suggest", json={"prompt": "Name a thing", "mode": "test"}).json()
    assert len(data["candidates"]) == 2 and len(data["dropped"]) == 1
    assert data["candidates"][0]["p_valid"] == 0.9
    rows = sqlite3.connect(tmp_path / "t.db").execute("SELECT answer, p_valid, dropped FROM candidates").fetchall()
    assert sorted(r[2] for r in rows) == [0, 0, 1]
    assert [r for r in rows if r[2] == 1][0][1] == 0.1


def test_unsure_answers_rank_after_confident_ones_but_common_answers_rank_last(monkeypatch):
    fake_validate(monkeypatch, {"results": [
        {"i": 1, "p_valid": 0.6}, {"i": 2, "p_valid": 1.0}, {"i": 3, "p_valid": 0.8}, {"i": 4, "p_valid": 0.95}]})
    cands = [{**c, "flag": "common" if c["answer"] == "Delta" else None} for c in STATE["candidates"]]
    out = validate({**STATE, "candidates": cands})
    # confident non-common (Bravo), then unsure non-common in niche order (Alpha, Charlie), then common (Delta)
    assert [c["answer"] for c in out["candidates"]] == ["Bravo", "Alpha", "Charlie", "Delta"]


def test_unknown_validity_is_not_demoted(monkeypatch):
    fake_validate(monkeypatch, {"results": []})   # unusable reply -> fail open, order unchanged
    out = validate(STATE)
    assert [c["answer"] for c in out["candidates"]] == ANSWERS
