"""Agente de noticias y alineaciones.

Junta tres fuentes por equipo:
- Lesiones/dudas oficiales de API-Football para el partido.
- Quién jugó y cómo en los últimos partidos (titular, minutos, calificación).
- Titulares de prensa recientes, interpretados por una IA gratuita.

Con eso arma su propia probabilidad de titularidad por jugador y los
multiplicadores de ataque/defensa que usa el modelo.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from .config import CACHE_DIR, SETTINGS
from .model import TeamAdjust
from .sources import llm

log = logging.getLogger("predictor")
PLAYERS_CACHE = CACHE_DIR / "players.json"

STATUS_ABSENT = {"out": 0.95, "suspended": 1.0, "doubt": 0.45, "rotation_risk": 0.35, "available": 0.0}
IMPORTANCE_W = {"key": 0.085, "starter": 0.035, "rotation": 0.01, "bench": 0.0}
POS_SPLIT = {  # (peso en ataque, peso en defensa)
    "GK": (0.0, 1.0),
    "DEF": (0.2, 0.8),
    "MID": (0.5, 0.5),
    "FWD": (0.85, 0.15),
}
POS_FROM_API = {"G": "GK", "D": "DEF", "M": "MID", "F": "FWD"}
STATUS_ES = {
    "out": "Baja", "suspended": "Sancionado", "doubt": "Duda",
    "rotation_risk": "Posible rotación", "available": "Disponible",
}
IMPORTANCE_ES = {"key": "Clave", "starter": "Titular", "rotation": "Rotación", "bench": "Suplente"}


@dataclass
class PlayerInfo:
    name: str
    pid: int | None = None
    position: str | None = None
    starts: int = 0
    apps: int = 0
    minutes: float = 0.0
    last_rating: float | None = None
    last_started: bool = False
    last_minutes: int = 0
    goals: int = 0
    assists: int = 0
    status: str = "available"
    status_reason: str = ""
    status_source: str = ""
    status_confidence: float = 0.0
    importance: str | None = None
    p_start: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "position": self.position,
            "status": self.status,
            "status_es": STATUS_ES.get(self.status, self.status),
            "reason": self.status_reason,
            "source": self.status_source,
            "confidence": round(self.status_confidence, 2),
            "importance": self.importance,
            "importance_es": IMPORTANCE_ES.get(self.importance or "", "—"),
            "p_start": round(self.p_start, 3),
            "last_rating": self.last_rating,
            "notes": self.notes,
        }


def _norm(name: str) -> str:
    return " ".join(name.lower().replace(".", " ").split())


def _same_player(a: str, b: str) -> bool:
    """'K. Mbappé' y 'Kylian Mbappé' son el mismo jugador."""
    a, b = _norm(a), _norm(b)
    if a == b:
        return True
    la, lb = a.split()[-1], b.split()[-1]
    if la != lb:
        return False
    fa, fb = a.split()[0], b.split()[0]
    return fa[0] == fb[0]


def find_player(players: dict[str, PlayerInfo], name: str) -> PlayerInfo | None:
    if name in players:
        return players[name]
    for k, p in players.items():
        if _same_player(k, name):
            return p
    return None


# ---------------------------------------------------------------------------
# 1) Uso reciente de jugadores
# ---------------------------------------------------------------------------

def recent_usage(team_id: int, fixtures_players: list[list[dict]]) -> dict[str, PlayerInfo]:
    """fixtures_players: lista (del más reciente al más viejo) de respuestas de /fixtures/players."""
    players: dict[str, PlayerInfo] = {}
    n = 0
    for idx, resp in enumerate(fixtures_players):
        block = next((b for b in resp or [] if b.get("team", {}).get("id") == team_id), None)
        if not block:
            continue
        n += 1
        for entry in block.get("players", []):
            pl = entry.get("player", {})
            st = (entry.get("statistics") or [{}])[0]
            games = st.get("games", {}) or {}
            minutes = games.get("minutes") or 0
            name = pl.get("name") or "?"
            info = players.setdefault(name, PlayerInfo(name=name, pid=pl.get("id")))
            info.position = info.position or POS_FROM_API.get(games.get("position") or "")
            if minutes:
                info.apps += 1
                info.minutes += minutes
                if not games.get("substitute"):
                    info.starts += 1
            goals = st.get("goals", {}) or {}
            info.goals += goals.get("total") or 0
            info.assists += goals.get("assists") or 0
            if idx == 0:
                try:
                    info.last_rating = float(games.get("rating")) if games.get("rating") else None
                except ValueError:
                    info.last_rating = None
                info.last_started = bool(minutes) and not games.get("substitute")
                info.last_minutes = minutes
    for p in players.values():
        p.notes = []
        if n:
            p.p_start = min(0.92, 0.08 + 0.78 * p.starts / n + 0.06 * min(p.minutes / (90 * n), 1))
            if p.starts == n and n >= 1:
                p.importance = p.importance or ("key" if (p.goals + p.assists) >= 2 else "starter")
            elif p.starts >= 1:
                p.importance = p.importance or ("starter" if p.starts / n >= 0.5 else "rotation")
            elif p.apps:
                p.importance = p.importance or "rotation"
            else:
                p.importance = p.importance or "bench"
        # Señal de "mal partido": el técnico podría sacarlo del once
        if p.last_started and p.last_rating is not None and p.last_rating < 6.3:
            p.p_start *= 0.88
            p.notes.append(f"Bajo rendimiento en el último partido (nota {p.last_rating:.1f})")
        if p.last_started and 0 < p.last_minutes < 55:
            p.p_start *= 0.9
            p.notes.append(f"Salió temprano en el último partido ({p.last_minutes}')")
    return players


# ---------------------------------------------------------------------------
# 2) Interpretación de noticias con IA
# ---------------------------------------------------------------------------

SYSTEM = (
    "Eres un analista de fútbol que extrae información verificable de titulares de prensa. "
    "Solo usas la información que te dan: nunca inventas lesiones ni jugadores. "
    "Responde únicamente con JSON válido."
)

PROMPT = """Equipo: {team}
Próximo partido: {team} vs {opp} ({competition}), fecha {date} (faltan {hours} horas).

Bajas reportadas oficialmente por la base de datos (pueden estar desactualizadas):
{injuries}

Jugadores habituales recientes (nombre, posición, titularidades en los últimos partidos):
{regulars}

Titulares de prensa recientes (de más nuevo a más viejo, con antigüedad en horas):
{headlines}

Tarea: determina el estado de cada jugador relevante del {team} para ESTE partido.
Reglas estrictas:
- Usa SOLO lo que dicen la lista oficial y los titulares. Si un titular no habla del {team}, ignóralo.
- No marques a nadie solo porque no fue titular en los últimos partidos: eso ya lo sabemos. Solo incluye jugadores que la lista oficial o un titular mencionen.
- status: "out" (baja confirmada), "suspended" (sancionado), "doubt" (duda real), "rotation_risk" (un titular o el técnico dice que podría ir al banco), "available" (estaba en duda o lesionado y la prensa dice que vuelve).
- importance: "key" (figura/indispensable), "starter" (titular habitual), "rotation" o "bench". Usa tu conocimiento general del plantel si no hay datos.
- position: "GK", "DEF", "MID" o "FWD".
- confidence de 0 a 1: alta si lo confirmó el club o el técnico; media si lo reportan varios medios; baja si es un rumor o un titular viejo.
- reason: máximo 12 palabras en español. source: el medio del titular que lo respalda ("oficial" solo si está en la lista oficial).
- rotation_risk del equipo (0 a 1): 0.1 por defecto. Súbelo solo si los titulares hablan de rotaciones, de que ya está clasificado o de que viene un partido más importante. rotation_reason en español, máximo 15 palabras.
- notes: hasta 3 notas útiles para apostar sobre el {team} (máximo 20 palabras cada una), solo si los titulares las respaldan. Lista vacía si no hay nada concreto.
- Si no hay información, devuelve "players": [].

Formato exacto:
{{"players":[{{"name":"","status":"","importance":"","position":"","confidence":0.0,"reason":"","source":""}}],"rotation_risk":0.0,"rotation_reason":"","notes":[]}}"""


def _load_player_cache() -> dict:
    try:
        return json.loads(PLAYERS_CACHE.read_text())
    except (OSError, ValueError):
        return {}


def _save_player_cache(cache: dict) -> None:
    if SETTINGS.demo:
        return
    PLAYERS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    PLAYERS_CACHE.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")))


def interpret_news(team: str, opp: str, date: str, hours: float, injuries: list[dict],
                   players: dict[str, PlayerInfo], headlines: list[dict]) -> tuple[dict | None, str | None]:
    if not llm.available():
        return None, None
    inj_txt = "\n".join(
        f"- {i['player']['name']}: {i['player'].get('type', '')} ({i['player'].get('reason', '')})"
        for i in injuries) or "- (ninguna)"
    regulars = sorted(players.values(), key=lambda p: (-p.starts, -p.minutes))[:16]
    reg_txt = "\n".join(f"- {p.name} ({p.position or '?'}, {p.starts} tit.)" for p in regulars) or "- (sin datos)"
    head_txt = "\n".join(f"- [{h['hours_ago']:.0f}h] {h['title']} — {h['source']}" for h in headlines[:18]) or "- (sin titulares)"
    prompt = PROMPT.format(team=team, opp=opp, competition="UEFA Champions League", date=date,
                           hours=round(hours), injuries=inj_txt, regulars=reg_txt, headlines=head_txt)
    return llm.ask_json(SYSTEM, prompt)


# ---------------------------------------------------------------------------
# 3) Combinar todo: estados, titularidad y ajustes del modelo
# ---------------------------------------------------------------------------

@dataclass
class TeamIntel:
    team: str
    players: dict[str, PlayerInfo]
    adjust: TeamAdjust
    rotation_risk: float = 0.0
    rotation_reason: str = ""
    notes: list[str] = field(default_factory=list)
    llm_provider: str | None = None
    headlines: list[dict] = field(default_factory=list)
    confirmed_lineup: bool = False

    def absences(self) -> list[PlayerInfo]:
        out = [p for p in self.players.values() if p.status != "available"]
        out.sort(key=lambda p: (-IMPORTANCE_W.get(p.importance or "bench", 0), p.name))
        return out

    def probable_xi(self) -> list[PlayerInfo]:
        pool = [p for p in self.players.values() if p.p_start > 0.02]
        gks = sorted([p for p in pool if p.position == "GK"], key=lambda p: -p.p_start)[:1]
        rest = sorted([p for p in pool if p.position != "GK"], key=lambda p: -p.p_start)[:10]
        return gks + rest


def apply_status(players: dict[str, PlayerInfo], name: str, status: str, *, reason: str = "",
                 source: str = "", confidence: float = 0.6, importance: str | None = None,
                 position: str | None = None) -> PlayerInfo:
    p = find_player(players, name)
    if p is None:
        p = PlayerInfo(name=name)
        p.importance = None
        players[name] = p
        p.notes.append("No jugó los últimos partidos")
    p.status = status if status in STATUS_ABSENT else "available"
    p.status_reason = reason or p.status_reason
    p.status_source = source or p.status_source
    p.status_confidence = max(0.0, min(1.0, float(confidence or 0)))
    if importance in IMPORTANCE_W and (p.importance is None or importance == "key"):
        p.importance = importance
    if position in POS_SPLIT and not p.position:
        p.position = position
    return p


def build_team_intel(team: str, opp: str, date: str, hours: float, team_id: int,
                     injuries: list[dict], fixtures_players: list[list[dict]],
                     headlines: list[dict], form: dict, lineup: list[dict] | None = None,
                     use_llm: bool = True) -> TeamIntel:
    players = recent_usage(team_id, fixtures_players)
    pcache = _load_player_cache()

    has_official = bool(injuries)
    # Lesiones oficiales (si la fuente las publica)
    for inj in injuries:
        pl = inj.get("player", {})
        typ = (pl.get("type") or "").lower()
        status = "doubt" if "question" in typ or "doubt" in typ else "out"
        reason = pl.get("reason") or ""
        if "suspen" in reason.lower() or "card" in reason.lower():
            status = "suspended"
        apply_status(players, pl.get("name", "?"), status, reason=reason, source="oficial", confidence=0.8)

    intel = TeamIntel(team=team, players=players, adjust=TeamAdjust(), headlines=headlines[:8])

    # Noticias interpretadas por IA
    if use_llm:
        resp, provider = interpret_news(team, opp, date, hours, injuries, players, headlines)
        if resp:
            intel.llm_provider = provider
            for item in resp.get("players", []) or []:
                name = (item.get("name") or "").strip()
                if not name:
                    continue
                src = item.get("source", "") or ""
                conf = item.get("confidence", 0.5)
                if src.lower() == "oficial" and not has_official:
                    src, conf = "prensa", min(float(conf or 0.5), 0.5)
                status = item.get("status", "available")
                known = find_player(players, name)
                if status == "rotation_risk" and (known is None or (known.importance or "") in ("bench", "rotation")):
                    continue  # rotar a un suplente no cambia nada
                apply_status(players, name, status, reason=item.get("reason", ""), source=src,
                             confidence=conf, importance=item.get("importance"),
                             position=item.get("position"))
                pcache[f"{team}|{_norm(name)}"] = {
                    "importance": item.get("importance"), "position": item.get("position")}
            try:
                intel.rotation_risk = max(0.0, min(1.0, float(resp.get("rotation_risk") or 0)))
            except (TypeError, ValueError):
                intel.rotation_risk = 0.0
            intel.rotation_reason = resp.get("rotation_reason") or ""
            intel.notes = [n for n in (resp.get("notes") or []) if isinstance(n, str)][:3]

    # Completa importancia/posición con lo aprendido en ejecuciones anteriores
    for p in players.values():
        c = pcache.get(f"{team}|{_norm(p.name)}")
        if c:
            p.importance = p.importance or c.get("importance")
            p.position = p.position or c.get("position")
    _save_player_cache(pcache)

    # Alineación confirmada (aparece ~1 hora antes)
    if lineup:
        block = next((b for b in lineup if b.get("team", {}).get("name") == team), None)
        if block and block.get("startXI"):
            intel.confirmed_lineup = True
            starters = {s["player"]["name"] for s in block["startXI"] if s.get("player")}
            for p in players.values():
                p.p_start = 0.0
            for name in starters:
                p = find_player(players, name) or apply_status(players, name, "available")
                p.p_start = 1.0

    # Probabilidad de titular según estado
    if not intel.confirmed_lineup:
        for p in players.values():
            if p.status in ("out", "suspended"):
                p.p_start = 0.0
            elif p.status == "doubt":
                p.p_start = min(p.p_start or 0.5, 0.9) * 0.5
            elif p.status == "rotation_risk":
                p.p_start *= 0.6
            if intel.rotation_risk > 0.3 and p.importance in ("key", "starter"):
                p.p_start *= 1 - 0.25 * intel.rotation_risk

    intel.adjust = compute_adjust(intel, form)
    return intel


def compute_adjust(intel: TeamIntel, form: dict) -> TeamAdjust:
    adj = TeamAdjust()
    att_loss = def_loss = 0.0
    big = []
    for p in intel.players.values():
        if intel.confirmed_lineup:
            p_abs = 0.0 if p.p_start >= 1 else (STATUS_ABSENT.get(p.status, 0) or (0.6 if p.importance == "key" else 0))
        else:
            p_abs = STATUS_ABSENT.get(p.status, 0.0)
            if p.status in ("doubt", "rotation_risk"):
                p_abs *= 0.6 + 0.4 * (p.status_confidence or 0.5)
        if p_abs <= 0:
            continue
        w = IMPORTANCE_W.get(p.importance or "rotation", 0.01)
        if p.apps == 0 and p.status in ("out", "suspended") and p.status_source == "oficial":
            # Baja larga: el Elo y la forma reciente ya la reflejan en parte
            w *= 0.45
        a, d = POS_SPLIT.get(p.position or "MID", (0.5, 0.5))
        att_loss += w * a * p_abs
        def_loss += w * d * p_abs
        if w >= 0.03 and p_abs >= 0.4:
            big.append(p.name)
    att_loss, def_loss = min(att_loss, 0.25), min(def_loss, 0.25)
    if att_loss or def_loss:
        reason = "Bajas: " + ", ".join(big[:4]) if big else "Bajas menores"
        adj.combine(1 - att_loss, 1 + def_loss, reason)
    if intel.rotation_risk > 0.15:
        r = intel.rotation_risk
        adj.combine(1 - 0.07 * r, 1 + 0.07 * r, f"Riesgo de rotación ({round(r * 100)}%)")
    if form.get("n"):
        ppg = form["points"] / form["n"]
        gd = form["gd"] / form["n"]
        f = max(-0.04, min(0.04, (ppg - 1.5) * 0.02 + gd * 0.01))
        if abs(f) >= 0.01:
            adj.combine(1 + f, 1 - f / 2, f"Forma reciente ({form['string']})")
    adj.clamp()
    return adj


def form_from_fixtures(team_id: int, fixtures: list[dict]) -> dict:
    """Resumen de los últimos partidos terminados: puntos, diferencia de gol y racha."""
    pts = gd = n = 0
    chars = []
    games = []
    for fx in sorted(fixtures, key=lambda f: f["fixture"]["timestamp"], reverse=True):
        if fx["fixture"]["status"]["short"] not in ("FT", "AET", "PEN"):
            continue
        home = fx["teams"]["home"]["id"] == team_id
        gf = fx["goals"]["home"] if home else fx["goals"]["away"]
        ga = fx["goals"]["away"] if home else fx["goals"]["home"]
        if gf is None or ga is None:
            continue
        n += 1
        gd += gf - ga
        res = "G" if gf > ga else "E" if gf == ga else "P"
        pts += 3 if res == "G" else 1 if res == "E" else 0
        chars.append(res)
        opp = fx["teams"]["away" if home else "home"]["name"]
        games.append({"date": fx["fixture"]["date"][:10], "opp": opp, "home": home,
                      "score": f"{gf}-{ga}", "res": res, "comp": fx["league"]["name"],
                      "fixture_id": fx["fixture"]["id"]})
    return {"n": n, "points": pts, "gd": gd, "string": "".join(chars) or "—", "games": games}
