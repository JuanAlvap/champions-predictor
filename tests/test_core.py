import math

from predictor import grading, parlays
from predictor.agent import _same_player, form_from_fixtures
from predictor.model import (blend, build_match, calibrate_k, elo_expectancy, lambdas_from_dr,
                             market_probabilities, remove_margin, score_matrix)
from predictor.sources.api_football import parse_odds
from predictor.sources.clubelo import lookup, normalize


def test_matrix_sums_to_one_and_markets_consistent():
    m = score_matrix(1.6, 1.1)
    assert abs(m.sum() - 1) < 1e-9
    p = market_probabilities(m)
    assert abs(p["1"] + p["X"] + p["2"] - 1) < 1e-9
    assert abs(p["over_2.5"] + p["under_2.5"] - 1) < 1e-9
    assert abs(p["btts_yes"] + p["btts_no"] - 1) < 1e-9
    # Una combinada nunca puede ser más probable que cada parte
    assert p["1_over_1.5"] <= min(p["1"], p["over_1.5"]) + 1e-12
    assert p["1_btts_yes"] <= min(p["1"], p["btts_yes"]) + 1e-12


def test_calibration_matches_elo():
    k = calibrate_k(2.95, -0.06)
    for dr in (-200, 0, 150, 300):
        lh, la = lambdas_from_dr(dr, 2.95, k)
        p = market_probabilities(score_matrix(lh, la))
        assert abs(p["1"] + 0.5 * p["X"] - elo_expectancy(dr)) < 0.02


def test_stronger_home_team_is_favourite():
    p = market_probabilities(build_match(1950, 1750).matrix)
    assert p["1"] > p["2"] and p["1"] > 0.6


def test_remove_margin_and_blend():
    f = remove_margin({"1": 2.0, "X": 3.4, "2": 3.8})
    assert abs(sum(f.values()) - 1) < 1e-9
    assert blend(0.5, None, 0.5) == 0.5
    b = blend(0.4, 0.6, 0.5)
    assert 0.4 < b < 0.6 and abs(b - 0.5) < 1e-9


def test_outcomes():
    assert grading.outcome("1", 2, 1) and not grading.outcome("X", 2, 1)
    assert grading.outcome("over_2.5", 2, 1) and not grading.outcome("over_3.5", 2, 1)
    assert grading.outcome("btts_yes", 2, 1) and grading.outcome("1_over_1.5", 2, 1)
    assert grading.outcome("X2_under_3.5", 1, 1) and not grading.outcome("X2_under_3.5", 2, 2)
    assert grading.outcome("home_over_1.5", 2, 0) and not grading.outcome("away_over_0.5", 2, 0)


def test_parse_odds():
    entry = {"bookmakers": [{"id": 8, "bets": [
        {"name": "Match Winner", "values": [{"value": "Home", "odd": "1.80"}, {"value": "Draw", "odd": "3.6"},
                                           {"value": "Away", "odd": "4.5"}]},
        {"name": "Goals Over/Under", "values": [{"value": "Over 2.5", "odd": "1.9"},
                                                {"value": "Under 2.5", "odd": "1.95"},
                                                {"value": "Over 5.5", "odd": "20"}]},
        {"name": "Both Teams Score", "values": [{"value": "Yes", "odd": "1.7"}, {"value": "No", "odd": "2.1"}]},
    ]}]}
    o = parse_odds(entry)
    assert o == {"1": 1.8, "X": 3.6, "2": 4.5, "over_2.5": 1.9, "under_2.5": 1.95,
                 "btts_yes": 1.7, "btts_no": 2.1}


def test_team_name_matching():
    ratings = {normalize(n): e for n, e in [("Man City", 1950), ("Paris SG", 1980), ("Bayern", 2000),
                                            ("Real Madrid", 1970), ("Real Sociedad", 1750)]}
    assert lookup("Manchester City", ratings)[0] == 1950
    assert lookup("Paris Saint Germain", ratings)[0] == 1980
    assert lookup("Bayern München", ratings)[0] == 2000
    assert lookup("Real Madrid", ratings)[0] == 1970


def test_same_player():
    assert _same_player("K. Mbappé", "Kylian Mbappé")
    assert not _same_player("K. Mbappé", "E. Mbappé")


def test_form():
    fx = [{"fixture": {"id": i, "timestamp": i, "status": {"short": "FT"}, "date": "2026-11-0%d" % i},
           "league": {"name": "L"},
           "teams": {"home": {"id": 1, "name": "A"}, "away": {"id": 2, "name": "B"}},
           "goals": {"home": g[0], "away": g[1]}} for i, g in enumerate([(2, 0), (1, 1), (0, 3)], 1)]
    f = form_from_fixtures(1, fx)
    assert f["n"] == 3 and f["points"] == 4 and f["gd"] == -1 and f["string"] == "PEG"


def _match(mid, p_home):
    mk = {}
    for key in parlays.PARLAY_MARKETS:
        mk[key] = {"p": 0.5, "label": key, "odds": None, "edge": 0.0}
    mk["1"] = {"p": p_home, "label": "1", "odds": 1 / p_home * 0.95, "edge": 0.0}
    return {"id": mid, "home": {"name": f"H{mid}"}, "away": {"name": f"A{mid}"}, "kickoff": f"2026-01-0{mid}",
            "status": "NS", "markets": mk}


def test_parlays_one_leg_per_match_and_product():
    ms = [_match(i, 0.8) for i in range(1, 8)]
    res = parlays.build_tier(ms, "seguro", "x")
    assert res
    for p in res:
        ids = [l["match_id"] for l in p["legs"]]
        assert len(ids) == len(set(ids))
        assert math.isclose(p["p"], math.prod(l["p"] for l in p["legs"]), rel_tol=1e-3)
