"""Datos de ejemplo para probar la app sin claves.

Usa nombres reales de clubes, pero jugadores, lesiones y cuotas son FICTICIOS.
La app muestra un aviso de "datos de ejemplo" cuando se usa este modo.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from . import pipeline
from .config import SETTINGS

TEAMS = [
    (541, "Real Madrid", 1985), (50, "Manchester City", 1990), (529, "Barcelona", 1975),
    (157, "Bayern München", 1980), (40, "Liverpool", 1995), (42, "Arsenal", 1970),
    (85, "Paris Saint Germain", 1965), (505, "Inter", 1955), (496, "Juventus", 1850),
    (165, "Borussia Dortmund", 1830), (530, "Atletico Madrid", 1870), (49, "Chelsea", 1880),
    (492, "Napoli", 1860), (168, "Bayer Leverkusen", 1860), (211, "Benfica", 1790),
    (228, "Sporting CP", 1800), (212, "FC Porto", 1770), (47, "Tottenham", 1820),
]
POS = ["G"] + ["D"] * 4 + ["M"] * 3 + ["F"] * 3 + ["D", "M", "F", "M", "D"]


def _logo(tid: int) -> str:
    return f"https://media.api-sports.io/football/teams/{tid}.png"


class FakeApi:
    def __init__(self, now: datetime, seed: int = 7):
        self.rng = random.Random(seed)
        self.used = 0
        self.budget = 0
        self.now = now
        self._build()

    def _build(self):
        rng = self.rng
        teams = TEAMS[:]
        rng.shuffle(teams)
        base = (self.now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        self.fixtures = []
        self.players = {}
        for i in range(0, len(teams), 2):
            (hid, hn, _), (aid, an, _) = teams[i], teams[i + 1]
            day = 0 if i < len(teams) // 2 else 1
            hour = 22 if i % 4 == 0 else 20  # UTC: 17:00 / 15:00 hora Colombia
            ts = base + timedelta(days=day, hours=hour - (2 if hour == 22 else 0))
            fid = 900000 + i
            self.fixtures.append(self._fx(fid, ts, (hid, hn), (aid, an), "NS", None, None))
        # Un partido de la jornada anterior ya jugado (para la sección de confiabilidad)
        for tid, name, _ in TEAMS:
            self.players[tid] = [
                {"id": tid * 100 + k, "name": f"{name.split()[0][:3].upper()} Jugador {k + 1}", "pos": POS[k]}
                for k in range(len(POS))]

    def _fx(self, fid, ts, home, away, status, gh, ga):
        return {
            "fixture": {"id": fid, "date": ts.isoformat(), "timestamp": int(ts.timestamp()),
                        "status": {"short": status}, "venue": {"name": f"Estadio de {home[1]}"}},
            "league": {"name": "UEFA Champions League", "round": "League Stage - 6"},
            "teams": {"home": {"id": home[0], "name": home[1], "logo": _logo(home[0])},
                      "away": {"id": away[0], "name": away[1], "logo": _logo(away[0])}},
            "goals": {"home": gh, "away": ga},
        }

    # --- Interfaz igual a ApiFootball ---
    def current_season(self, league_id):
        return 2026

    def fixtures_range(self, *a, **k):
        return self.fixtures

    def team_last_fixtures(self, team_id, n=5):
        rng = random.Random(team_id)
        out = []
        for k in range(n):
            ts = self.now - timedelta(days=4 + 4 * k)
            opp = rng.choice([t for t in TEAMS if t[0] != team_id])
            gh, ga = rng.choice([(2, 0), (1, 1), (3, 1), (0, 1), (2, 2), (1, 0), (0, 2), (4, 1)])
            out.append(self._fx(800000 + team_id * 10 + k, ts, (team_id, "X"), (opp[0], opp[1]), "FT", gh, ga))
        return out

    def fixture_players(self, fixture_id):
        team_id = (fixture_id - 800000) // 10
        rng = random.Random(fixture_id)
        ps = []
        for k, pl in enumerate(self.players.get(team_id, [])):
            starter = k < 11
            minutes = 90 if starter else (rng.choice([0, 0, 15, 25]) if k < 16 else 0)
            if starter and rng.random() < 0.15:
                minutes = rng.choice([46, 60, 70])
            ps.append({"player": {"id": pl["id"], "name": pl["name"]},
                       "statistics": [{"games": {"minutes": minutes, "position": pl["pos"],
                                                 "substitute": not starter,
                                                 "rating": f"{rng.uniform(5.9, 8.4):.1f}" if minutes else None},
                                       "goals": {"total": int(pl["pos"] == "F" and rng.random() < 0.4),
                                                 "assists": int(rng.random() < 0.1)}}]})
        return [{"team": {"id": team_id}, "players": ps}]

    def lineups(self, fixture_id):
        return []

    def injuries(self, fixture_id):
        fx = next(f for f in self.fixtures if f["fixture"]["id"] == fixture_id)
        out = []
        rng = random.Random(fixture_id)
        for side in ("home", "away"):
            t = fx["teams"][side]
            for k in rng.sample(range(1, 14), rng.choice([0, 1, 1, 2])):
                pl = self.players[t["id"]][k]
                out.append({"player": {"id": pl["id"], "name": pl["name"],
                                       "type": rng.choice(["Missing Fixture", "Questionable"]),
                                       "reason": rng.choice(["Muscle Injury", "Knee Injury", "Red Card"])},
                            "team": {"id": t["id"], "name": t["name"]}})
        return out

    def head_to_head(self, a, b):
        return []

    def odds_by_date(self, league_id, season, date):
        out = []
        elo = {t[0]: t[2] for t in TEAMS}
        from .model import build_match, market_probabilities
        for f in self.fixtures:
            if f["fixture"]["date"][:10] != date:
                continue
            h, a = f["teams"]["home"]["id"], f["teams"]["away"]["id"]
            noise = self.rng.uniform(-40, 40)
            p = market_probabilities(build_match(elo[h] + noise, elo[a]).matrix)

            def o(x, m=1.06):
                return f"{max(1.01, 1 / (x * m)):.2f}"
            out.append({"fixture": {"id": f["fixture"]["id"]}, "bookmakers": [{"id": 8, "bets": [
                {"name": "Match Winner", "values": [{"value": "Home", "odd": o(p["1"])},
                                                    {"value": "Draw", "odd": o(p["X"])},
                                                    {"value": "Away", "odd": o(p["2"])}]},
                {"name": "Goals Over/Under", "values": [{"value": f"Over {l}", "odd": o(p[f'over_{l}'])}
                                                        for l in ("1.5", "2.5", "3.5")]
                 + [{"value": f"Under {l}", "odd": o(p[f'under_{l}'])} for l in ("1.5", "2.5", "3.5")]},
                {"name": "Both Teams Score", "values": [{"value": "Yes", "odd": o(p["btts_yes"])},
                                                        {"value": "No", "odd": o(p["btts_no"])}]},
            ]}]})
        return out

    def save(self):
        pass


def demo_headlines(team: str) -> list[dict]:
    return [{"title": f"(Ejemplo) Rueda de prensa previa del {team}", "source": "Demo", "url": "",
             "ts": 0, "hours_ago": 6.0, "lang": "es"}]


def run_demo() -> dict:
    SETTINGS.demo = True
    now = datetime.now(timezone.utc)
    elo = {pipeline.clubelo.normalize(n): e for _, n, e in TEAMS}
    return pipeline.run(FakeApi(now), elo_override=elo, headlines_fn=demo_headlines, use_llm=False, now=now)
