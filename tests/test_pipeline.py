import os

os.environ["KRILLION_FAKE_LLM"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from server.app import app  # noqa: E402

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"ok": True}


def test_suggest_and_log():
    r = client.post("/suggest", json={"prompt": "Name a pasta shape", "mode": "test"})
    assert r.status_code == 200
    data = r.json()
    assert data["category"] == "open_category"
    assert len(data["candidates"]) == 3
    assert [c["rank"] for c in data["candidates"]] == [1, 2, 3]
    assert {c["node"] for c in data["calls"]} == {"route", "generate", "validate"}
    r2 = client.post("/log", json={"round_id": data["round_id"], "typed_answer": "x", "tier": "RARE", "points": 60})
    assert r2.json() == {"ok": True}
