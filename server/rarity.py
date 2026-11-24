"""Local word-frequency checks (no network, no calibration). Rarity ranking is the LLM's; these are guards only."""
from wordfreq import zipf_frequency

COMMON_ZIPF = 5.0  # everyday words (zipf >= 5) are not niche answers


def zipf_score(answer: str) -> float | None:
    """Zipf frequency for a single alphabetic word; None for multi-word answers/entities (pageviews come later)."""
    word = answer.strip()
    if not word.isalpha():
        return None
    return zipf_frequency(word.lower(), "en")


def flag(zipf: float | None) -> str | None:
    """'common' = everyday word, demoted. 'unverified' = wordfreq has never seen it: may be real but niche
    (e.g. 'trofie') or invented, so it is only labeled, never demoted. None = no signal."""
    if zipf is None:
        return None
    if zipf >= COMMON_ZIPF:
        return "common"
    if zipf == 0:
        return "unverified"
    return None
