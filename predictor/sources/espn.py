"""Calendario de respaldo desde el marcador público de ESPN (sin clave).

Se usa solo si API-Football no devuelve partidos (por ejemplo, si el plan gratis
no cubre la temporada). Convierte los eventos al mismo formato de API-Football.
"""
from __future__ import annotations

from datetime import datetime

from . import http

URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.champions/scoreboard"
ID_OFFSET = 10_000_000  # para no chocar con ids de API-Football


def _status(st: dict) -> str:
    t = (st or {}).get("type", {})
    if t.get("completed"):
        return "FT"
    if t.get("state") == "in":
        return "2H"
    return "NS"


def fixtures(date_from: str, date_to: str) -> list[dict]:
    rng = f"{date_from.replace('-', '')}-{date_to.replace('-', '')}"
    r = http.get(URL, "espn", params={"dates": rng, "limit": 200})
    if r is None:
        return []
    out = []
    for ev in r.json().get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        teams = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        if "home" not in teams or "away" not in teams:
            continue
        ts = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
        status = _status(ev.get("status") or comp.get("status"))

        def side(c):
            t = c.get("team", {})
            return {"id": ID_OFFSET + int(t.get("id", 0)), "name": t.get("displayName") or t.get("name", "?"),
                    "logo": t.get("logo")}

        def goals(c):
            try:
                return int(c.get("score")) if status != "NS" else None
            except (TypeError, ValueError):
                return None

        out.append({
            "fixture": {"id": ID_OFFSET + int(ev["id"]), "date": ts.isoformat(), "timestamp": int(ts.timestamp()),
                        "status": {"short": status}, "venue": {"name": (comp.get("venue") or {}).get("fullName")}},
            "league": {"name": "UEFA Champions League", "round": ""},
            "teams": {"home": side(teams["home"]), "away": side(teams["away"])},
            "goals": {"home": goals(teams["home"]), "away": goals(teams["away"])},
            "_source": "espn",
        })
    return out
