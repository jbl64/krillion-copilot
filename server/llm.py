"""Thin LLM wrapper: JSON-mode calls, timing, and a fake mode for tests."""
import json
import os
import re
import time

from dotenv import load_dotenv

load_dotenv()

MODEL = os.getenv("KRILLION_MODEL", "gpt-4.1-mini")
FAKE = os.getenv("KRILLION_FAKE_LLM", "0") == "1"

_client = None


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI()  # reads OPENAI_API_KEY from the environment
    return _client


def _fake_reply(node: str, user: str) -> dict:
    if node == "route":
        return {"category": "open_category", "plan": "Explore regional and technical variants."}
    if node == "validate":
        n = len(re.findall(r"^\d+\. ", user, flags=re.M))
        return {"results": [{"i": i, "p_valid": 0.9, "reason": "fake mode"} for i in range(1, n + 1)]}
    return {
        "candidates": [
            {"answer": "Example niche answer A", "why": "fake mode"},
            {"answer": "Example niche answer B", "why": "fake mode"},
            {"answer": "Example niche answer C", "why": "fake mode"},
        ]
    }


def parse_json(text: str) -> dict:
    """json.loads, but if the reply was cut off, salvage the complete objects before the cut; {} if hopeless."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    end = len(text)
    for _ in range(40):
        end = text.rfind("}", 0, end)
        if end == -1:
            return {}
        for closer in ("]}", "}"):
            try:
                return json.loads(text[: end + 1] + closer)
            except json.JSONDecodeError:
                continue
    return {}


def call_json(node: str, system: str, user: str, temperature: float = 0.7,
              max_tokens: int | None = None) -> tuple[dict, dict]:
    """Return (parsed_json, call_info). call_info has node, model, latency_ms, tokens."""
    t0 = time.perf_counter()
    if FAKE:
        out = _fake_reply(node, user)
        tokens = 0
    else:
        resp = _get_client().chat.completions.create(
            model=MODEL,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        out = parse_json(resp.choices[0].message.content or "")
        tokens = resp.usage.total_tokens if resp.usage else 0
    info = {
        "node": node,
        "model": "fake" if FAKE else MODEL,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "tokens": tokens,
    }
    return out, info
