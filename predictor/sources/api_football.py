"""Cliente de API-Football (plan gratis: 100 peticiones/día).

Cada petición pasa por un caché en disco (data/cache/api_football.json) que el
workflow guarda en el repo, y por un presupuesto por ejecución, para no quemar
la cuota diaria.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from ..config import CACHE_DIR, SETTINGS
from . import http

log = logging.getLogger("predictor")
BASE = "https://v3.football.api-sports.io"
CACHE_FILE = CACHE_DIR / "api_football.json"

# Tiempo de vida del caché por endpoint, en horas
TTL = {
    "leagues": 24 * 7,
    "fixtures": 2,
    "fixtures_team": 10,
    "fixtures/players": 24 * 365,
    "fixtures/lineups": 0.25,
    "injuries": 2,
    "odds": 1.5,
    "fixtures/headtohead": 24 * 7,
}


class ApiFootball:
    def __init__(self, key: str | None = None, budget: int | None = None, cache_file: Path = CACHE_FILE):
        self.key = key if key is not None else SETTINGS.api_football_key
        self.budget = budget if budget is not None else SETTINGS.api_budget
        self.used = 0
        self.cache_file = cache_file
        try:
            self.cache = json.loads(cache_file.read_text())
        except (OSError, ValueError):
            self.cache = {}

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def save(self) -> None:
        # Limpia entradas viejas (más de 30 días) para que el archivo no crezca sin control
        cutoff = time.time() - 30 * 86400
        self.cache = {k: v for k, v in self.cache.items() if v.get("t", 0) > cutoff}
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_text(json.dumps(self.cache, separators=(",", ":")))

    def _get(self, endpoint: str, params: dict, ttl_key: str | None = None, force: bool = False):
        ttl_h = TTL.get(ttl_key or endpoint, 2)
        ck = endpoint + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        hit = self.cache.get(ck)
        if hit and not force and (time.time() - hit["t"]) < ttl_h * 3600:
            return hit["data"]
        if not self.enabled:
            return hit["data"] if hit else None
        if self.used >= self.budget:
            log.info("Presupuesto de API-Football agotado; uso caché para %s", ck)
            return hit["data"] if hit else None
        self.used += 1
        r = http.get(f"{BASE}/{endpoint}", "api-football", params=params,
                     headers={"x-apisports-key": self.key})
        if r is None:
            return hit["data"] if hit else None
        body = r.json()
        errors = body.get("errors")
        if errors and (isinstance(errors, dict) and errors or isinstance(errors, list) and errors):
            http.mark("api-football", False, json.dumps(errors)[:300])
            log.warning("API-Football devolvió errores para %s: %s", ck, errors)
            return hit["data"] if hit else None
        data = body.get("response", [])
        self.cache[ck] = {"t": time.time(), "data": data}
        return data

    # ------------------------------------------------------------------
    def current_season(self, league_id: int) -> int | None:
        data = self._get("leagues", {"id": league_id, "current": "true"})
        try:
            return int(data[0]["seasons"][0]["year"])
        except (TypeError, IndexError, KeyError):
            return None

    def fixtures_range(self, league_id: int, season: int, date_from: str, date_to: str):
        return self._get("fixtures", {"league": league_id, "season": season,
                                      "from": date_from, "to": date_to}) or []

    def team_last_fixtures(self, team_id: int, n: int = 5):
        return self._get("fixtures", {"team": team_id, "last": n}, ttl_key="fixtures_team") or []

    def fixture_players(self, fixture_id: int):
        return self._get("fixtures/players", {"fixture": fixture_id}) or []

    def lineups(self, fixture_id: int):
        return self._get("fixtures/lineups", {"fixture": fixture_id}) or []

    def injuries(self, fixture_id: int):
        return self._get("injuries", {"fixture": fixture_id}) or []

    def head_to_head(self, team_a: int, team_b: int, last: int = 10):
        a, b = sorted((team_a, team_b))
        return self._get("fixtures/headtohead", {"h2h": f"{a}-{b}", "last": last}) or []

    def odds_by_date(self, league_id: int, season: int, date: str):
        """Cuotas pre-partido de todos los partidos de una fecha (paginado)."""
        out = []
        page = 1
        while page <= 3:
            params = {"league": league_id, "season": season, "date": date}
            if page > 1:
                params["page"] = page
            data = self._get("odds", params)
            if not data:
                break
            out.extend(data)
            if len(data) < 10:
                break
            page += 1
        return out


# ----------------------------------------------------------------------
# Lectura de cuotas
# ----------------------------------------------------------------------
PREFERRED_BOOKMAKERS = [8, 6, 11, 1]  # Bet365, Bwin, 1xBet, 10Bet


def parse_odds(odds_entry: dict) -> dict[str, float]:
    """Convierte la respuesta de /odds en {clave_mercado: cuota} usando la mejor casa disponible."""
    books = odds_entry.get("bookmakers") or []
    if not books:
        return {}
    books.sort(key=lambda b: PREFERRED_BOOKMAKERS.index(b["id"]) if b.get("id") in PREFERRED_BOOKMAKERS else 99)
    out: dict[str, float] = {}
    for book in books:
        for bet in book.get("bets", []):
            name = (bet.get("name") or "").lower()
            for v in bet.get("values", []):
                val = str(v.get("value", ""))
                try:
                    odd = float(v.get("odd"))
                except (TypeError, ValueError):
                    continue
                key = None
                if name == "match winner":
                    key = {"Home": "1", "Draw": "X", "Away": "2"}.get(val)
                elif name == "double chance":
                    key = {"Home/Draw": "1X", "Draw/Away": "X2", "Home/Away": "12"}.get(val)
                elif name == "goals over/under":
                    parts = val.split()
                    if len(parts) == 2 and parts[1] in {"0.5", "1.5", "2.5", "3.5", "4.5"}:
                        key = f"{parts[0].lower()}_{parts[1]}"
                elif name == "both teams score":
                    key = {"Yes": "btts_yes", "No": "btts_no"}.get(val)
                if key and key not in out:
                    out[key] = odd
        if {"1", "X", "2"} <= out.keys():
            break  # ya tenemos lo principal de la casa preferida
    return out
