"""Ratings Elo de clubes desde ClubElo (gratis, sin clave)."""
from __future__ import annotations

import csv
import difflib
import io
import json
import re
import time
import unicodedata

from ..config import CACHE_DIR
from . import http

CACHE_FILE = CACHE_DIR / "clubelo.json"

# API-Football usa nombres distintos a ClubElo para algunos clubes
ALIASES = {
    "bayern munich": "bayern",
    "bayern münchen": "bayern",
    "manchester city": "man city",
    "manchester united": "man united",
    "paris saint germain": "paris sg",
    "paris saint-germain": "paris sg",
    "psg": "paris sg",
    "inter milan": "internazionale",
    "ac milan": "milan",
    "atletico madrid": "atletico",
    "atlético madrid": "atletico",
    "borussia dortmund": "dortmund",
    "bayer leverkusen": "leverkusen",
    "rb leipzig": "rb leipzig",
    "eintracht frankfurt": "frankfurt",
    "sporting cp": "sporting",
    "sporting lisbon": "sporting",
    "fc porto": "porto",
    "sl benfica": "benfica",
    "psv eindhoven": "psv",
    "psv": "psv",
    "fc barcelona": "barcelona",
    "athletic club": "bilbao",
    "athletic bilbao": "bilbao",
    "tottenham": "tottenham",
    "tottenham hotspur": "tottenham",
    "newcastle": "newcastle",
    "newcastle united": "newcastle",
    "club brugge kv": "brugge",
    "club brugge": "brugge",
    "union st. gilloise": "st gillis",
    "union saint-gilloise": "st gillis",
    "red bull salzburg": "salzburg",
    "fc copenhagen": "fc kobenhavn",
    "fc københavn": "fc kobenhavn",
    "bodo/glimt": "bodoe glimt",
    "bodø/glimt": "bodoe glimt",
    "olympiakos piraeus": "olympiakos",
    "olympiacos": "olympiakos",
    "galatasaray": "galatasaray",
    "fenerbahce": "fenerbahce",
    "slavia praha": "slavia praha",
    "sparta praha": "sparta praha",
    "dinamo zagreb": "dinamo zagreb",
    "shakhtar donetsk": "shakhtar",
    "crvena zvezda": "crvena zvezda",
    "fk crvena zvezda": "crvena zvezda",
    "qarabag": "karabakh agdam",
    "qarabağ": "karabakh agdam",
    "kairat almaty": "kairat",
    "pafos": "pafos",
    "ajax": "ajax",
    "marseille": "marseille",
    "olympique marseille": "marseille",
    "monaco": "monaco",
    "as monaco": "monaco",
    "villarreal": "villarreal",
    "napoli": "napoli",
    "ssc napoli": "napoli",
    "juventus": "juventus",
    "atalanta": "atalanta",
    "liverpool": "liverpool",
    "arsenal": "arsenal",
    "chelsea": "chelsea",
    "real madrid": "real madrid",
    "internazionale": "internazionale",
    "paris saint germain": "paris sg",
    "slavia prague": "slavia praha",
    "real betis": "betis",
    "vfb stuttgart": "stuttgart",
    "as roma": "roma",
    "feyenoord rotterdam": "feyenoord",
    "feyenoord": "feyenoord",
    "bodo/glimt": "bodo glimt",
    "lask linz": "lask",
    "lask": "lask",
    "slovan bratislava": "slovan bratislava",
    "viking fk": "viking",
    "sabah fk": "sabah",
    "aek athens": "aek",
    "lille": "lille",
    "lens": "lens",
    "como": "como",
    "aston villa": "aston villa",
}


def normalize(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(fc|cf|sc|ac|afc|club|de|sk|fk|kv)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


ELO_RE = re.compile(r'"Elo":\s*([\d.]+),.*?"Name":\s*"((?:[^"\\]|\\.)*)"', re.S)


def _parse_ranking_html(text: str) -> dict[str, float]:
    ratings = {}
    for elo, name in ELO_RE.findall(text):
        try:
            name = json.loads(f'"{name}"')
            ratings.setdefault(normalize(name), float(elo))
        except ValueError:
            continue
    return ratings


def fetch_ratings(date: str) -> dict[str, float]:
    """Devuelve {nombre_normalizado: elo}. Prueba la API y, si no responde, la página de ranking."""
    try:
        cache = json.loads(CACHE_FILE.read_text())
    except (OSError, ValueError):
        cache = {}
    if cache.get("date") == date and cache.get("ratings"):
        return cache["ratings"]
    ratings: dict[str, float] = {}
    r = http.get(f"http://api.clubelo.com/{date}", "clubelo", timeout=12, retries=0)
    if r is not None:
        for row in csv.DictReader(io.StringIO(r.text)):
            try:
                ratings[normalize(row["Club"])] = float(row["Elo"])
            except (KeyError, ValueError):
                continue
    if not ratings:
        r = http.get("https://clubelo.com/Ranking", "clubelo-web", timeout=30, retries=1)
        if r is not None:
            ratings = _parse_ranking_html(r.text)
    if not ratings:
        return cache.get("ratings", {})  # último Elo conocido
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps({"date": date, "t": time.time(), "ratings": ratings}))
    return ratings


def lookup(team: str, ratings: dict[str, float]) -> tuple[float | None, str | None]:
    """Busca el Elo de un equipo tolerando diferencias de nombre."""
    if not ratings:
        return None, None
    raw = team.lower().strip()
    candidates = [ALIASES.get(raw), ALIASES.get(normalize(team)), normalize(team)]
    for c in candidates:
        if c and normalize(c) in ratings:
            key = normalize(c)
            return ratings[key], key
    match = difflib.get_close_matches(normalize(team), list(ratings), n=1, cutoff=0.72)
    if match:
        return ratings[match[0]], match[0]
    # Intento por palabra distintiva más larga
    words = sorted(normalize(team).split(), key=len, reverse=True)
    for w in words:
        if len(w) >= 5:
            hits = [k for k in ratings if w in k.split()]
            if len(hits) == 1:
                return ratings[hits[0]], hits[0]
    return None, None
