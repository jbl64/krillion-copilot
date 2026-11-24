"""LangGraph pipeline. route -> generate -> rule_check -> rarity -> validate. Later: final ranking, prune."""
import re
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .llm import call_json
from .rarity import flag, zipf_score
from .rules import build_rule, normalize, passes, report

CATEGORIES = ["open_category", "constraint", "data_property", "entity"]

ROUTE_SYSTEM = """You classify prompts from a rare-answer trivia game. Players get a prompt such as
'Name a country in Africa' and score more for answers few other people would give, as long as the answer is valid.
Classify the prompt into exactly one category:
- open_category: name a member of a broad category (pasta shapes, rivers)
- constraint: answers must satisfy a letter or spelling rule (starts with Q, contains no vowels)
- data_property: answers defined by a measurable property (population, area, release year, rating)
- entity: named things such as brands, movies, characters, people, places
Reply as JSON: {"category": "<one of the four>", "plan": "<one sentence on how to find rare but valid answers>"}
ONLY when the category is constraint, also add "subject": what the answers are, with the letter rule removed, as a short
noun phrase (e.g. "a musical instrument", "a country", "a fruit"), and "rule": {"regex": "...", "pass": [two real answers that satisfy the rule],
"fail": [two real answers that break it]}. Code applies the regex with Python re.search to the answer lowercased with
accents removed and everything except letters a-z deleted (no spaces or punctuation), so write the regex for that form.
Examples: starts with Q -> "^q"; contains no E -> "^[^e]*$"; contains Q or has no E (either is enough) -> "q|^[^e]*$";
exactly 5 letters -> "^[a-z]{5}$". Never add "rule" for other categories."""

GENERATE_SYSTEM = """You help a player of a rare-answer trivia game. Score rises with how uncommon an answer is,
but an answer only counts if it is valid and widely recognized by dictionaries or encyclopedias.
Propose the number of answers requested below, all niche but definitely valid: explore regional, historical, technical
and subcultural corners of the category. Never invent items.
How scoring works: an answer many players type (among the first things that come to mind) scores 10-30; one only a few
players give scores 60; a real answer almost nobody thinks of scores 85-100. Aim for the last two tiers. Before including an
answer, ask whether a typical player would think of it within their first 30 seconds; if so, replace it with a less
obvious one. Obscure answers must still be real and well documented in an encyclopedia or dictionary.
Write each answer exactly as a player would type it in a few seconds: the item's short standard name (the item itself
should still be obscure), at most 4 words, with no parenthetical clarifiers, no years and no "e.g."; the game forgives
close spellings. Put explanations in "why".
Order strictly from most niche to least niche; only the first ones may be shown. Reply as JSON:
{"candidates": [{"answer": "...", "why": "<5-10 words on why it is valid and uncommon>"}]}"""

GENERATE_HINTS = {
    "open_category": "Enumerate sub-areas of the category (regions, eras, technical variants) and pick members from the long tail of each.",
    "constraint": "Every answer MUST satisfy the stated letter or spelling constraint. Double-check each one.",
    "data_property": ("Work out the exact condition in the prompt, including any reference values it mentions. Code will "
                      "check your numbers, so choose items that satisfy it rather than items that are merely obscure."),
    "entity": "Draw from long-tail sub-areas of the category; skip anything a casual fan would name first.",
}

VALIDATE_SYSTEM = """You are a strict fact-checker for a rare-answer trivia game. An answer scores 0 if it is not a real,
widely recognized item that satisfies the prompt exactly. For each numbered candidate give p_valid, the probability
(0 to 1) that a dictionary or encyclopedia would confirm it as a valid answer to the prompt. Use a low value for
anything you suspect is invented, misspelled, or only loosely fits. Never judge letter or spelling rules (such as
starts with, contains, number of letters): you cannot count letters reliably, and code checks them separately where it
can. Judge whether the answer is a real, recognized item AND meets every other condition stated in the prompt, including
parenthetical ones (for example "has an emoji", "rated 8.0 or higher on IMDb", "spans multiple countries", "red on the
inside", "not a brand"). Only conditions actually written in the prompt count; never invent one (an item is a valid
answer to "name any item"). The game accepts common usage and close variants (England counts as a country, "sake" as a
clear drink), so give p_valid below 0.3 only when an answer clearly fails a written condition, and name that condition in
the reason. If you are unsure, use 0.5 to 0.7; do not penalize what you cannot judge.
Do not reward obscurity here, only validity."""

VALIDATE_REPLY = """
Reply as compact JSON, one entry per candidate: {"results": [{"i": <candidate number>, "p_valid": <0-1>}]}. Add
"reason" (under 8 words) ONLY to entries with p_valid below 0.5; omit it otherwise."""

GENERATE_FACT = """
This prompt has a measurable condition. First restate it as numeric bounds in one unit, writing out the reference values
you use from the prompt: "condition": {"unit": "...", "min": <number or null>, "max": <number or null>, "ref": "<reference
values with numbers>"}. If it is not a simple numeric range, use null for min and max. Then give each candidate's "value":
its actual value as a plain number in that unit (null if unknown). Do not compare values yourself; code does that.
Reply as JSON: {"condition": {...}, "candidates": [{"answer": "...", "why": "<5-10 words>", "value": <number or null>}]}"""

REPAIR_SYSTEM = """You write regex rules for a letter-rule trivia game. Code applies the regex with Python re.search to
the answer lowercased with accents removed and everything except letters a-z deleted. Your earlier rule failed a code
self-test: at least one of your sample answers did not behave as you labeled it. Letter counting by hand is error-prone,
so trust the code's verdicts. Decide for each disagreement whether the REGEX is wrong or the SAMPLE was mislabeled.
Return a corrected rule with two sample answers that satisfy the rule and two real answers that break it, choosing
samples whose letters you have checked carefully. Reply as JSON: {"regex": "...", "pass": ["", ""], "fail": ["", ""]}"""

FINAL_N = 8        # candidates shown to the player
OVERGEN_N = 20     # constraint prompts: ask for extra, because code drops rule-breakers before truncating
OVERGEN_CATEGORIES = {"constraint"}
GENERATE_MAX_TOKENS = 2000  # cuts off a runaway reply (once ~100 candidates took 18 s)
VALIDATE_MAX_TOKENS = 1200

VALID_MIN = 0.5  # candidates below this are dropped (returned in `dropped`), not suggested
CONFIDENT_P = 0.9  # candidates below this (but >= VALID_MIN) are kept but ranked after the confident ones


class State(TypedDict, total=False):
    prompt: str
    category: str
    plan: str
    rule: dict
    subject: str
    candidates: list
    dropped: list
    calls: list


def route(state: State) -> State:
    out, info = call_json("route", ROUTE_SYSTEM, f"Prompt: {state['prompt']}", temperature=0.0)
    category = out.get("category")
    if category not in CATEGORIES:
        category = "open_category"
    res = {"category": category, "plan": out.get("plan", ""), "calls": state.get("calls", []) + [info]}
    if category == "constraint":
        raw = out.get("rule")
        rule = build_rule(raw)
        if rule["status"] in ("selftest_failed", "invalid"):
            user = (f"Prompt: {state['prompt']}\nYour rule: {raw}\nCode results on your samples:\n{report(raw)}")
            fixed, rinfo = call_json("rule_repair", REPAIR_SYSTEM, user, temperature=0.0)
            res["calls"].append(rinfo)
            new = build_rule(fixed)
            rule = {**(new if new["status"] == "ok" else rule), "repaired": True}
        res["rule"] = rule
        if isinstance(out.get("subject"), str) and out["subject"].strip():
            res["subject"] = out["subject"].strip()
    return res


def _num(x) -> float | None:
    try:
        return float(x) if x is not None and not isinstance(x, bool) else None
    except (TypeError, ValueError):
        return None


def generate(state: State) -> State:
    fact = state["category"] == "data_property"
    n = OVERGEN_N if state["category"] in OVERGEN_CATEGORIES else FINAL_N
    user = (
        f"Prompt: {state['prompt']}\n"
        f"Category: {state['category']}\n"
        f"Plan: {state.get('plan', '')}\n"
        f"Guidance: {GENERATE_HINTS[state['category']]}\n"
        f"Number of answers: {n}"
    )
    if state["category"] in OVERGEN_CATEGORIES:
        user += " (code will remove any that break the rule, so later ones are backups)"
    system = GENERATE_SYSTEM.split("Reply as JSON")[0] + GENERATE_FACT if fact else GENERATE_SYSTEM
    out, info = call_json("generate", system, user, temperature=0.8, max_tokens=GENERATE_MAX_TOKENS)
    cond = out.get("condition") if isinstance(out.get("condition"), dict) else {}
    lo, hi, unit = _num(cond.get("min")), _num(cond.get("max")), str(cond.get("unit", "")).strip()
    cands, dropped, seen, total = [], list(state.get("dropped", [])), set(), 0
    for c in out.get("candidates", []):
        if not (isinstance(c, dict) and c.get("answer")):
            continue
        answer = str(c["answer"]).strip()
        answer = re.sub(r"\s*\([^)]*\)\s*$", "", answer) or answer  # drop trailing clarifier, e.g. "Scooter (kick scooter)"
        key = normalize(answer, keep_digits=True) or answer.lower()
        if key in seen:
            continue
        seen.add(key)
        total += 1
        cand = {"answer": answer, "why": c.get("why", ""), "rank": total}
        val = _num(c.get("value")) if fact else None
        if val is not None:
            cand["value"] = f"{val:,.6g} {unit}".strip()
            if (lo is not None and val < lo) or (hi is not None and val > hi):  # code compares; the model only supplies numbers
                dropped.append({**cand, "p_valid": None,
                                "valid_reason": f"value {cand['value']} outside [{lo if lo is not None else '-'}, {hi if hi is not None else '-'}]"})
                if total == n:
                    break
                continue
        cands.append(cand)
        if total == n:
            break
    return {"candidates": cands[:FINAL_N] if fact else cands, "dropped": dropped, "calls": state.get("calls", []) + [info]}


def rule_check(state: State) -> State:
    """Constraint prompts only: drop candidates that break the (self-tested) letter rule, then keep the first
    FINAL_N in the generator's niche-first order. Deterministic code."""
    rule = state.get("rule")
    if state.get("category") != "constraint":
        return {}
    cands = state.get("candidates", [])
    if not rule or rule["status"] != "ok":
        return {"candidates": cands[:FINAL_N]}
    kept, dropped = [], list(state.get("dropped", []))
    for c in cands:
        if passes(rule["regex"], c["answer"]):
            kept.append(c)
        else:
            dropped.append({**c, "p_valid": None, "valid_reason": "failed letter rule (code check)"})
    return {"candidates": kept[:FINAL_N], "dropped": dropped}


def rarity(state: State) -> State:
    """Local only (wordfreq). Keeps the generator's niche-first order; only demotes everyday words (stable)."""
    scored = []
    for c in state.get("candidates", []):
        z = zipf_score(c["answer"])
        scored.append({**c, "rarity": z, "flag": flag(z)})
    scored.sort(key=lambda c: c["flag"] == "common")
    return {"candidates": scored}


def validate(state: State) -> State:
    """One batched LLM call scores P(valid) for all candidates. Drops those below VALID_MIN, then moves unsure ones
    (below CONFIDENT_P) behind confident ones.
    Fails open (keeps all, p_valid None) if the reply is unusable, so a bad reply never empties the panel."""
    cands = state.get("candidates", [])
    if not cands:
        return {}
    listing = "\n".join(f"{i}. {c['answer']}" for i, c in enumerate(cands, 1))
    subject = state.get("subject")
    if state["category"] == "constraint" and subject:
        # The letter rule is code's job (or unchecked); keep it out of the validator's prompt entirely.
        user = f"Judge whether each candidate is a real, recognized example of: {subject}\nCandidates:\n{listing}"
    else:
        user = f"Prompt: {state['prompt']}\nCategory: {state['category']}\n"
        if state.get("rule", {}).get("status") == "ok":
            user += "A code check already verified the letter/spelling rule for every candidate below.\n"
        elif state["category"] == "constraint":
            user += "The letter/spelling rule could not be code-checked; do not judge it, only whether each answer is real.\n"
        user += f"Candidates:\n{listing}"
    out, info = call_json("validate", VALIDATE_SYSTEM + VALIDATE_REPLY, user, temperature=0.0, max_tokens=VALIDATE_MAX_TOKENS)
    scores = {}
    for r in out.get("results", []):
        try:
            scores[int(r["i"])] = (min(1.0, max(0.0, float(r["p_valid"]))), str(r.get("reason", "")))
        except (KeyError, TypeError, ValueError):
            continue
    kept, dropped = [], list(state.get("dropped", []))
    for i, c in enumerate(cands, 1):
        p, reason = scores.get(i, (None, ""))
        c = {**c, "p_valid": p, "valid_reason": reason}
        (dropped if p is not None and p < VALID_MIN else kept).append(c)
    # Keep niche-first order, but a common answer ranks after uncommon ones and an unsure answer after confident ones
    # (the top pick is the one typed fastest, so it should be trustworthy). Stable sort preserves order within groups.
    kept.sort(key=lambda c: (c.get("flag") == "common", c["p_valid"] is not None and c["p_valid"] < CONFIDENT_P))
    return {"candidates": kept, "dropped": dropped, "calls": state.get("calls", []) + [info]}


def build_graph():
    g = StateGraph(State)
    g.add_node("route", route)
    g.add_node("generate", generate)
    g.add_node("rule_check", rule_check)
    g.add_node("rarity", rarity)
    g.add_edge(START, "route")
    g.add_edge("route", "generate")
    g.add_edge("generate", "rule_check")
    g.add_edge("rule_check", "rarity")
    g.add_node("validate", validate)
    g.add_edge("rarity", "validate")
    g.add_edge("validate", END)
    return g.compile()


GRAPH = build_graph()
