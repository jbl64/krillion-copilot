"""SQLite logging."""
import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "krillion.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS rounds (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at REAL, mode TEXT, prompt TEXT, category TEXT, plan TEXT, rule TEXT
);
CREATE TABLE IF NOT EXISTS candidates (
  round_id INTEGER, rank INTEGER, answer TEXT, why TEXT, rarity REAL, flag TEXT, p_valid REAL, dropped INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS outcomes (
  round_id INTEGER, typed_answer TEXT, tier TEXT, points INTEGER, timed_out INTEGER, logged_at REAL
);
CREATE TABLE IF NOT EXISTS calls (
  round_id INTEGER, node TEXT, model TEXT, latency_ms INTEGER, tokens INTEGER
);
"""


def conn():
    c = sqlite3.connect(DB_PATH)
    c.executescript(SCHEMA)
    # Migrate databases created before the rarity columns existed.
    if "rule" not in {row[1] for row in c.execute("PRAGMA table_info(rounds)")}:
        c.execute("ALTER TABLE rounds ADD COLUMN rule TEXT")
    have = {row[1] for row in c.execute("PRAGMA table_info(candidates)")}
    for col, typ in (("rarity", "REAL"), ("flag", "TEXT"), ("p_valid", "REAL"), ("dropped", "INTEGER DEFAULT 0")):
        if col not in have:
            c.execute(f"ALTER TABLE candidates ADD COLUMN {col} {typ}")
    return c


def log_round(prompt: str, mode: str, result: dict) -> int:
    c = conn()
    cur = c.execute(
        "INSERT INTO rounds (started_at, mode, prompt, category, plan, rule) VALUES (?,?,?,?,?,?)",
        (time.time(), mode, prompt, result.get("category"), result.get("plan"),
         json.dumps(result["rule"]) if result.get("rule") else None),
    )
    rid = cur.lastrowid
    for dropped, group in ((0, result.get("candidates", [])), (1, result.get("dropped", []))):
        for cand in group:
            c.execute(
                "INSERT INTO candidates (round_id, rank, answer, why, rarity, flag, p_valid, dropped)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (rid, cand["rank"], cand["answer"], cand.get("why", ""), cand.get("rarity"), cand.get("flag"),
                 cand.get("p_valid"), dropped),
            )
    for call in result.get("calls", []):
        c.execute(
            "INSERT INTO calls VALUES (?,?,?,?,?)",
            (rid, call["node"], call["model"], call["latency_ms"], call["tokens"]),
        )
    c.commit()
    c.close()
    return rid


def log_outcome(round_id: int, typed: str, tier: str, points: int, timed_out: bool):
    c = conn()
    c.execute(
        "INSERT INTO outcomes VALUES (?,?,?,?,?,?)",
        (round_id, typed, tier, points, int(timed_out), time.time()),
    )
    c.commit()
    c.close()
