"""Datos de ESPN (públicos, sin clave): calendario, cuotas, forma, alineaciones y titulares.

El plan gratis de API-Football no cubre la temporada actual, así que ESPN es la
fuente principal. Esta clase expone la misma interfaz que ApiFootball para que el
resto del pipeline no cambie.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from . import http

log = logging.getLogger("predictor")
BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
LEAGUE = "uefa.champions"

POSITION = {"goalkeeper": "G", "defender": "D", "midfielder": "M", "forward": "F"}


def american_to_decimal(ml) -> float | None:
    try:
        ml = float(ml)
    except (TypeError, ValueError):
        return None
    if ml == 0:
        return None
    return round(1 + (ml / 100 if ml > 0 else 100 / abs(ml)), 3)


def _ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _status(st: dict) -> str:
    t = (st or {}).get("type", {})
    if t.get("completed"):
        return "FT"
    if t.get("state") == "in":
        return "2H"
    if t.get("name") in ("STATUS_POSTPONED", "STATUS_CANCELED"):
        return "PST"
    return "NS"


def _pos(p: dict) -> str | None:
    name = ((p or {}).get("name") or "").lower()
    for k, v in POSITION.items():
        if k in name:
            return v
    ab = ((p or {}).get("abbreviation") or "").upper()
    return {"G": "G", "GK": "G"}.get(ab, None)


class EspnApi:
    """Interfaz compatible con ApiFootball, alimentada por ESPN."""

    def __init__(self):
        self.used = 0
        self.budget = 0
        self._summaries: dict[int, dict] = {}
        self._fixtures: dict[int, dict] = {}
        self._last5: dict[int, list[dict]] = {}

    # --- llamadas -------------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> dict | None:
        self.used += 1
        r = http.get(f"{BASE}/{path}", "espn", params=params, timeout=25)
        if r is None:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    def summary(self, event_id: int) -> dict:
        if event_id not in self._summaries:
            self._summaries[event_id] = self._get(f"{LEAGUE}/summary", {"event": event_id}) or {}
            self._index_last5(event_id)
        return self._summaries[event_id]

    def _index_last5(self, event_id: int) -> None:
        s = self._summaries.get(event_id) or {}
        for block in s.get("lastFiveGames") or []:
            team = block.get("team") or {}
            tid = int(team.get("id", 0))
            out = []
            for ev in block.get("events") or []:
                try:
                    ts = _ts(ev["gameDate"])
                    hs, as_ = int(ev["homeTeamScore"]), int(ev["awayTeamScore"])
                except (KeyError, ValueError, TypeError):
                    continue
                opp = ev.get("opponent") or {}
                home_id = int(ev.get("homeTeamId", 0))
                me = {"id": tid, "name": team.get("displayName", "?")}
                them = {"id": int(opp.get("id", 0)), "name": opp.get("displayName", "?")}
                home, away = (me, them) if home_id == tid else (them, me)
                out.append({
                    "fixture": {"id": int(ev["id"]), "date": ts.isoformat(), "timestamp": int(ts.timestamp()),
                                "status": {"short": "FT"}},
                    "league": {"name": ev.get("leagueName") or ev.get("competitionName") or ""},
                    "teams": {"home": home, "away": away},
                    "goals": {"home": hs, "away": as_},
                })
            self._last5[tid] = out

    # --- interfaz tipo ApiFootball ----------------------------------------
    def current_season(self, league_id) -> int:
        return date.today().year if date.today().month >= 7 else date.today().year - 1

    def fixtures_range(self, league_id, season, date_from: str, date_to: str) -> list[dict]:
        d0, d1 = date.fromisoformat(date_from), date.fromisoformat(date_to)
        out = []
        d = d0
        while d <= d1:
            j = self._get(f"{LEAGUE}/scoreboard", {"dates": d.strftime("%Y%m%d")}) or {}
            for ev in j.get("events", []):
                fx = self._event_to_fixture(ev)
                if fx:
                    out.append(fx)
                    self._fixtures[fx["fixture"]["id"]] = fx
            d += timedelta(days=1)
        return out

    def _event_to_fixture(self, ev: dict) -> dict | None:
        comp = (ev.get("competitions") or [{}])[0]
        teams = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        if "home" not in teams or "away" not in teams:
            return None
        ts = _ts(ev["date"])
        status = _status(ev.get("status") or comp.get("status"))

        def side(c):
            t = c.get("team", {})
            return {"id": int(t.get("id", 0)), "name": t.get("displayName") or t.get("name", "?"),
                    "logo": t.get("logo")}

        def goals(c):
            try:
                return int(c.get("score")) if status in ("FT", "2H") else None
            except (TypeError, ValueError):
                return None

        return {
            "fixture": {"id": int(ev["id"]), "date": ts.isoformat(), "timestamp": int(ts.timestamp()),
                        "status": {"short": status}, "venue": {"name": (comp.get("venue") or {}).get("fullName")}},
            "league": {"name": "UEFA Champions League", "round": ""},
            "teams": {"home": side(teams["home"]), "away": side(teams["away"])},
            "goals": {"home": goals(teams["home"]), "away": goals(teams["away"])},
        }

    def team_last_fixtures(self, team_id: int, n: int = 5) -> list[dict]:
        return self._last5.get(int(team_id), [])[:n]

    def fixture_players(self, fixture_id: int) -> list[dict]:
        """Quién fue titular, quién salió y quién entró en un partido ya jugado (cualquier liga)."""
        j = self._get("all/summary", {"event": fixture_id}) or {}
        out = []
        for block in j.get("rosters") or []:
            tid = int((block.get("team") or {}).get("id", 0))
            players = []
            for p in block.get("roster") or []:
                ath = p.get("athlete") or {}
                stats = {s.get("name"): s.get("value") for s in p.get("stats") or []}
                starter = bool(p.get("starter"))
                if starter:
                    minutes = 65 if p.get("subbedOut") else 90
                elif p.get("subbedIn"):
                    minutes = 25
                else:
                    minutes = 0
                players.append({
                    "player": {"id": int(ath.get("id", 0) or 0), "name": ath.get("displayName", "?")},
                    "statistics": [{
                        "games": {"minutes": minutes, "position": _pos(p.get("position")),
                                  "substitute": not starter, "rating": None},
                        "goals": {"total": int(stats.get("totalGoals") or 0),
                                  "assists": int(stats.get("goalAssists") or 0)},
                    }],
                })
            out.append({"team": {"id": tid}, "players": players})
        return out

    def lineups(self, fixture_id: int) -> list[dict]:
        s = self.summary(fixture_id)
        out = []
        for block in s.get("rosters") or []:
            starters = [{"player": {"name": (p.get("athlete") or {}).get("displayName", "?")}}
                        for p in block.get("roster") or [] if p.get("starter")]
            if len(starters) >= 11:
                out.append({"team": {"name": (block.get("team") or {}).get("displayName")}, "startXI": starters})
        return out

    def injuries(self, fixture_id: int) -> list[dict]:
        return []  # ESPN no publica bajas: las saca el agente de noticias

    def head_to_head(self, a: int, b: int) -> list[dict]:
        return []

    def odds_by_date(self, league_id, season, day: str) -> list[dict]:
        out = []
        for fid, fx in self._fixtures.items():
            if fx["fixture"]["date"][:10] != day or fx["fixture"]["status"]["short"] != "NS":
                continue
            s = self.summary(fid)
            o = (s.get("odds") or s.get("pickcenter") or [None])[0]
            if not o:
                continue
            h = american_to_decimal((o.get("homeTeamOdds") or {}).get("moneyLine"))
            a = american_to_decimal((o.get("awayTeamOdds") or {}).get("moneyLine"))
            d = american_to_decimal((o.get("drawOdds") or {}).get("moneyLine"))
            bets = []
            if h and a and d:
                bets.append({"name": "Match Winner", "values": [
                    {"value": "Home", "odd": h}, {"value": "Draw", "odd": d}, {"value": "Away", "odd": a}]})
            line = o.get("overUnder")
            ov, un = american_to_decimal(o.get("overOdds")), american_to_decimal(o.get("underOdds"))
            if line and ov and un:
                bets.append({"name": "Goals Over/Under", "values": [
                    {"value": f"Over {line}", "odd": ov}, {"value": f"Under {line}", "odd": un}]})
            if bets:
                book = (o.get("provider") or {}).get("name", "ESPN")
                out.append({"fixture": {"id": fid}, "bookmakers": [{"id": 8, "name": book, "bets": bets}]})
        return out

    def match_news(self, fixture_id: int) -> list[dict]:
        s = self.summary(fixture_id)
        now = datetime.now().astimezone()
        out = []
        for a in (s.get("news") or {}).get("articles") or []:
            title = a.get("headline")
            if not title:
                continue
            try:
                ts = _ts(a.get("published") or a.get("lastModified"))
                hours = (now - ts).total_seconds() / 3600
            except (TypeError, ValueError):
                ts, hours = now, 0
            out.append({"title": title, "source": "ESPN", "url": ((a.get("links") or {}).get("web") or {}).get("href", ""),
                        "ts": ts.timestamp(), "hours_ago": round(hours, 1), "lang": "en"})
        return out

    def save(self) -> None:
        pass
