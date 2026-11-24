"""FastAPI server. Run: uvicorn server.app:app --port 8765"""
import asyncio
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import db
from .graph import GRAPH

app = FastAPI(title="Krillion copilot")
# The userscript calls via GM_xmlhttpRequest (no CORS needed), but allow krillion.io for debugging.
app.add_middleware(CORSMiddleware, allow_origins=["https://krillion.io", "https://www.krillion.io"],
                   allow_methods=["*"], allow_headers=["*"])


class SuggestIn(BaseModel):
    prompt: str
    mode: str = "unknown"


class LogIn(BaseModel):
    round_id: int
    typed_answer: str = ""
    tier: str = ""
    points: int = 0
    timed_out: bool = False


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/suggest")
async def suggest(body: SuggestIn):
    t0 = time.perf_counter()
    # Graph nodes are synchronous (blocking LLM calls), so run them off the event loop.
    result = await asyncio.to_thread(GRAPH.invoke, {"prompt": body.prompt.strip(), "calls": []})
    round_id = db.log_round(body.prompt, body.mode, result)
    return {
        "round_id": round_id,
        "category": result["category"],
        "candidates": result["candidates"],
        "dropped": result.get("dropped", []),
        "rule": result.get("rule"),
        "total_ms": int((time.perf_counter() - t0) * 1000),
        "calls": result["calls"],
    }


@app.post("/log")
def log(body: LogIn):
    db.log_outcome(body.round_id, body.typed_answer, body.tier, body.points, body.timed_out)
    return {"ok": True}
