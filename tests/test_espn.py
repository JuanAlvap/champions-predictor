"""Prueba la conversión de ESPN con respuestas reales recortadas (24-nov-2026)."""
from predictor.sources import espn as espn_mod
from predictor.sources.api_football import parse_odds
from predictor.sources.espn import EspnApi, american_to_decimal

SCOREBOARD = {"events": [{
    "id": "401915379", "date": "2026-11-24T20:00Z",
    "status": {"type": {"name": "STATUS_SCHEDULED", "state": "pre", "completed": False}},
    "competitions": [{"venue": {"fullName": "Etihad Stadium"}, "competitors": [
        {"homeAway": "home", "score": "0", "team": {"id": "382", "displayName": "Manchester City", "logo": "x"}},
        {"homeAway": "away", "score": "0", "team": {"id": "114", "displayName": "Napoli", "logo": "y"}}]}]}]}

SUMMARY = {
    "odds": [{"provider": {"name": "DraftKings"}, "overUnder": 3.5, "overOdds": -125, "underOdds": 100,
              "homeTeamOdds": {"moneyLine": -350}, "awayTeamOdds": {"moneyLine": 800}, "drawOdds": {"moneyLine": 550}}],
    "lastFiveGames": [{"team": {"id": "382", "displayName": "Manchester City"}, "events": [
        {"id": "401879245", "gameDate": "2026-10-31T15:00Z", "homeTeamId": "382", "homeTeamScore": "0",
         "awayTeamScore": "1", "leagueName": "English Premier League",
         "opponent": {"id": "331", "displayName": "Brighton & Hove Albion"}},
        {"id": "401879233", "gameDate": "2026-11-21T12:30Z", "homeTeamId": "382", "homeTeamScore": "1",
         "awayTeamScore": "1", "leagueName": "English Premier League", "opponent": {"id": "370", "displayName": "Fulham"}},
    ]}],
    "rosters": [],
    "news": {"articles": [{"headline": "Champions League Power Rankings before MD5", "published": "2026-11-23T20:00:56Z",
                           "links": {"web": {"href": "https://espn.com/x"}}}]},
}

PLAYERS = {"rosters": [{"team": {"id": "382"}, "roster": [
    {"starter": True, "subbedOut": False, "athlete": {"id": "1", "displayName": "Gerónimo Rulli"},
     "position": {"name": "Goalkeeper", "abbreviation": "G"}, "stats": []},
    {"starter": True, "subbedOut": True, "athlete": {"id": "2", "displayName": "Phil Foden"},
     "position": {"name": "Attacking Midfielder Right", "abbreviation": "AM-R"},
     "stats": [{"name": "goalAssists", "value": 1}, {"name": "totalGoals", "value": 0}]},
    {"starter": False, "subbedIn": True, "athlete": {"id": "3", "displayName": "Jérémy Doku"},
     "position": {"name": "Substitute", "abbreviation": "SUB"}, "stats": []},
]}]}


class FakeResp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


def fake_get(url, source, params=None, **kw):
    if url.endswith("scoreboard"):
        return FakeResp(SCOREBOARD if params["dates"] == "20261124" else {"events": []})
    if url.endswith("all/summary"):
        return FakeResp(PLAYERS)
    return FakeResp(SUMMARY)


def test_espn_adapter(monkeypatch):
    monkeypatch.setattr(espn_mod.http, "get", fake_get)
    api = EspnApi()
    fx = api.fixtures_range(2, 2026, "2026-11-23", "2026-11-25")
    assert len(fx) == 1 and fx[0]["teams"]["home"]["name"] == "Manchester City"
    assert fx[0]["fixture"]["status"]["short"] == "NS" and fx[0]["goals"]["home"] is None

    odds = api.odds_by_date(2, 2026, "2026-11-24")
    o = parse_odds(odds[0])
    assert o["1"] == american_to_decimal(-350) and o["X"] == 6.5 and o["2"] == 9.0
    assert o["over_3.5"] == 1.8 and o["under_3.5"] == 2.0

    last = api.team_last_fixtures(382)
    assert len(last) == 2 and last[1]["goals"] == {"home": 1, "away": 1}

    ps = api.fixture_players(401879233)[0]["players"]
    by = {p["player"]["name"]: p["statistics"][0]["games"] for p in ps}
    assert by["Gerónimo Rulli"]["position"] == "G" and by["Gerónimo Rulli"]["minutes"] == 90
    assert by["Phil Foden"]["minutes"] == 65 and by["Phil Foden"]["position"] == "M"
    assert by["Jérémy Doku"]["substitute"] and by["Jérémy Doku"]["minutes"] == 25

    assert api.match_news(401915379)[0]["source"] == "ESPN"
    assert api.lineups(401915379) == []


def test_american_odds():
    assert american_to_decimal(100) == 2.0
    assert american_to_decimal(-200) == 1.5
    assert american_to_decimal(None) is None
