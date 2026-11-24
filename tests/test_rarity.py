import os
import sqlite3

os.environ["KRILLION_FAKE_LLM"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from server import db  # noqa: E402
from server.app import app  # noqa: E402
from server.graph import rarity  # noqa: E402
from server.rarity import flag, zipf_score  # noqa: E402

client = TestClient(app)


def test_zipf_score():
    assert zipf_score("the") > 6
    assert zipf_score("sesquipedalian") < 3
    assert zipf_score("Zzxqvbnm") == 0
    assert zipf_score("Example niche answer") is None
    assert zipf_score("R2-D2") is None


def test_flag():
    assert flag(None) is None
    assert flag(6.0) == "common"
    assert flag(5.0) == "common"
    assert flag(3.0) is None
    assert flag(0) == "unverified"


def test_rarity_node_keeps_llm_order_and_demotes_common():
    cands = [
        {"answer": "the", "why": "", "rank": 1},
        {"answer": "Some Movie", "why": "", "rank": 2},
        {"answer": "zzxqvbnm", "why": "", "rank": 3},
        {"answer": "sesquipedalian", "why": "", "rank": 4},
        {"answer": "water", "why": "", "rank": 5},
    ]
    out = rarity({"candidates": cands})["candidates"]
    assert [c["rank"] for c in out] == [2, 3, 4, 1, 5]  # LLM order kept, common words moved last
    by = {c["answer"]: c for c in out}
    assert by["the"]["flag"] == "common"
    assert by["zzxqvbnm"]["flag"] == "unverified"  # labeled, not demoted
    assert by["Some Movie"]["rarity"] is None and by["Some Movie"]["flag"] is None
    assert "predicted_points" not in by["the"]


def test_suggest_with_real_values_logged(tmp_path, monkeypatch):
    from server import llm

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    real = llm._fake_reply
    monkeypatch.setattr(llm, "_fake_reply", lambda node, user: (
        {"candidates": [{"answer": a, "why": "x"} for a in
                        ["the", "Mausoleum at Halicarnassus", "sesquipedalian", "zzxqvbnm"]]}
        if node == "generate" else real(node, user)))
    data = client.post("/suggest", json={"prompt": "Name a wonder", "mode": "test"}).json()
    assert {c["node"] for c in data["calls"]} == {"route", "generate", "decide"}
    assert [c["answer"] for c in data["candidates"]] == [
        "Mausoleum at Halicarnassus", "sesquipedalian", "zzxqvbnm", "the"]
    rows = sqlite3.connect(tmp_path / "t.db").execute("SELECT answer, rarity, flag FROM candidates").fetchall()
    assert ("the", rows[[r[0] for r in rows].index("the")][1], "common") in rows
    assert ("Mausoleum at Halicarnassus", None, None) in rows


def test_db_migrates_old_candidates_table(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE candidates (round_id INTEGER, rank INTEGER, answer TEXT, why TEXT)")
    old.commit()
    old.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    db.conn().close()
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(candidates)")}
    assert {"rarity", "flag"} <= cols
