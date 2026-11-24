"""Code-side letter/spelling rule check for `constraint` prompts. The LLM supplies a regex plus sample answers
(in the route call); code self-tests the regex on the samples, then applies it deterministically."""
import re
import unicodedata


def normalize(answer: str, keep_digits: bool = False) -> str:
    """Lowercase, strip accents, keep only a-z (no spaces/punctuation). Letter rules are written for this form;
    keep_digits=True also keeps 0-9 (used for deduping, where 'Apollo 11' != 'Apollo 13')."""
    s = unicodedata.normalize("NFKD", answer)
    return re.sub(r"[^a-z0-9]" if keep_digits else r"[^a-z]", "", s.lower())


def passes(regex: str, answer: str) -> bool:
    return re.search(regex, normalize(answer)) is not None


def build_rule(raw) -> dict:
    """Return {"regex": str | None, "status": ok | missing | invalid | selftest_failed}. Only 'ok' is enforced."""
    if not isinstance(raw, dict) or not isinstance(raw.get("regex"), str) or not raw["regex"]:
        return {"regex": None, "status": "missing"}
    regex = raw["regex"]
    good = [s for s in raw.get("pass", []) if isinstance(s, str)]
    bad = [s for s in raw.get("fail", []) if isinstance(s, str)]
    try:
        re.compile(regex)
    except re.error:
        return {"regex": regex, "status": "invalid"}
    if not good or not bad or not all(passes(regex, s) for s in good) or any(passes(regex, s) for s in bad):
        return {"regex": regex, "status": "selftest_failed"}
    return {"regex": regex, "status": "ok"}


def report(raw) -> str:
    """Per-sample verdicts from code, to show the LLM when its rule fails the self-test."""
    if not isinstance(raw, dict) or not isinstance(raw.get("regex"), str):
        return "no usable regex"
    try:
        re.compile(raw["regex"])
    except re.error as e:
        return f"the regex does not compile ({e})"
    lines = []
    for label, key in (("pass", "pass"), ("fail", "fail")):
        for sample in raw.get(key, []):
            if isinstance(sample, str):
                lines.append(f'- "{sample}" (you labeled it {label}): normalized "{normalize(sample)}" '
                             f'-> regex {"MATCHES" if passes(raw["regex"], sample) else "does NOT match"}')
    return "\n".join(lines) or "no samples were given"
