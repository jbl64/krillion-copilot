import pytest

from evaluation.sheets import SHEETS_DIR, load_all, norm, points, tier_of

needs_data = pytest.mark.skipif(not list(SHEETS_DIR.glob("*.html")), reason="no saved sheets")


def test_norm():
    assert norm("Mausoleum at  Halicarnassus!") == "mausoleum at halicarnassus"
    assert norm("Mac & cheese") == norm("Mac and cheese")
    assert norm("Spätzle") == "spatzle"


@needs_data
def test_sheets_parse_and_lookup():
    prompts = load_all({"2026-10-28"})
    assert len(prompts) == 7 and prompts[0]["prompt"] == "Name a pasta dish"
    p = prompts[0]
    assert tier_of(p, "carbonara") == "plankton" and tier_of(p, "Aglio e olio") == "schooler"
    assert tier_of(p, "Agnolotti") == "rare" and tier_of(p, "Pastasciutta antifascista") == "gem"
    assert tier_of(p, "definitely not a pasta") is None
    assert [points(t) for t in ("plankton", "schooler", "rare", "gem", None)] == [10, 30, 60, 100, 0]


def test_tier_of_is_fuzzy_like_the_game():
    p = {"lookup": {norm("City of God"): "rare", norm("The Moscow Times"): "rare", norm("Grand Canyon"): "schooler"}}
    assert tier_of(p, "City of God (2002)") == "rare"     # trailing parenthetical ignored
    assert tier_of(p, "Moscow Times") == "rare"           # leading 'the' optional
    assert tier_of(p, "Grand Canyon National Park") is None
    assert tier_of(p, "Grand Canyn") == "schooler"        # close spelling
    assert tier_of(p, "Eiffel Tower") is None
