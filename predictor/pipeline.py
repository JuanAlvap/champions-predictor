"""Ejecución completa: datos -> agente -> modelo -> parleys -> data/latest.json."""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import agent, explain, grading, parlays
from .config import DATA_DIR, SETTINGS
from .model import (MARKET_GROUPS, TeamAdjust, blend, build_match, market_label,
                    market_probabilities, remove_margin, top_scores)
import math
import sys

from .sources import clubelo, http, llm, news
from .sources.api_football import parse_odds
from .sources.espn import EspnApi

log = logging.getLogger("predictor")
TZ = ZoneInfo("America/Bogota")
DEFAULT_ELO = 1700.0


def _guess_season(now: datetime) -> int:
    return now.year if now.month >= 7 else now.year - 1


def _h2h_from_fixtures(home_id: int, fixtures: list[dict]) -> dict:
    fx = [f for f in fixtures if f["fixture"]["status"]["short"] in ("FT", "AET", "PEN")]
    fx.sort(key=lambda f: f["fixture"]["timestamp"], reverse=True)
    w = d = l = 0
    games = []
    for f in fx[:6]:
        gh, ga = f["goals"]["home"], f["goals"]["away"]
        if gh is None:
            continue
        mine = gh if f["teams"]["home"]["id"] == home_id else ga
        theirs = ga if f["teams"]["home"]["id"] == home_id else gh
        w += mine > theirs
        d += mine == theirs
        l += mine < theirs
        games.append({"date": f["fixture"]["date"][:10],
                      "score": f"{f['teams']['home']['name']} {gh}-{ga} {f['teams']['away']['name']}"})
    n = w + d + l
    return {"n": n, "w": w, "d": d, "l": l, "games": games,
            "summary": f"{w}G {d}E {l}P para el local en {n} partidos" if n else ""}


def _team_side(team: dict, elo: float | None, elo_key: str | None, intel: agent.TeamIntel | None,
               form: dict) -> dict:
    side = {
        "id": team["id"], "name": team["name"], "logo": team.get("logo"),
        "elo": round(elo, 1) if elo else None, "elo_found": elo_key is not None,
        "form": form, "absences": [], "probable_xi": [], "rotation_risk": 0.0,
        "rotation_reason": "", "notes": [], "headlines": [], "confirmed_lineup": False,
        "adjust": {"attack": 1.0, "defense": 1.0, "reasons": []},
    }
    if intel:
        side.update({
            "absences": [p.to_dict() for p in intel.absences()],
            "probable_xi": [p.to_dict() for p in intel.probable_xi()],
            "rotation_risk": round(intel.rotation_risk, 2),
            "rotation_reason": intel.rotation_reason,
            "notes": intel.notes,
            "headlines": [{k: h[k] for k in ("title", "source", "url", "hours_ago")} for h in intel.headlines],
            "confirmed_lineup": intel.confirmed_lineup,
            "llm": intel.llm_provider,
            "adjust": {"attack": round(intel.adjust.attack, 3), "defense": round(intel.adjust.defense, 3),
                       "reasons": intel.adjust.reasons},
        })
    return side


def _markets(mm, odds: dict[str, float], home: str, away: str) -> dict:
    probs = market_probabilities(mm.matrix)
    fair: dict[str, float] = {}
    if {"1", "X", "2"} <= odds.keys():
        f = remove_margin({k: odds[k] for k in ("1", "X", "2")})
        fair.update(f)
        fair["1X"], fair["X2"], fair["12"] = f["1"] + f["X"], f["X"] + f["2"], f["1"] + f["2"]
    for line in ("0.5", "1.5", "2.5", "3.5", "4.5"):
        o, u = odds.get(f"over_{line}"), odds.get(f"under_{line}")
        if o and u:
            f = remove_margin({"o": o, "u": u})
            fair[f"over_{line}"], fair[f"under_{line}"] = f["o"], f["u"]
    if odds.get("btts_yes") and odds.get("btts_no"):
        f = remove_margin({"y": odds["btts_yes"], "n": odds["btts_no"]})
        fair["btts_yes"], fair["btts_no"] = f["y"], f["n"]

    group_of = {k: g for g, ks in MARKET_GROUPS.items() for k in ks}
    out = {}
    for key, pm in probs.items():
        pk = fair.get(key)
        p = blend(pm, pk, SETTINGS.market_weight)
        o = odds.get(key)
        out[key] = {
            "label": market_label(key, home, away),
            "group": group_of.get(key, "Otros"),
            "p": round(p, 4),
            "p_model": round(pm, 4),
            "p_market": round(pk, 4) if pk is not None else None,
            "odds": o,
            "fair_odds": round(1 / p, 2) if p > 0 else None,
            "edge": round(pm - pk, 4) if pk is not None else 0.0,
            "ev": round(p * o - 1, 4) if o else None,
        }
    return out


def _fill_elo(elo_h, elo_a, odds):
    """Si falta el Elo de un equipo (ClubElo solo publica el top 100), lo deduce de las cuotas."""
    if elo_h and elo_a:
        return elo_h, elo_a, "elo"
    if {"1", "X", "2"} <= odds.keys():
        f = remove_margin({k: odds[k] for k in ("1", "X", "2")})
        e = min(max(f["1"] + 0.5 * f["X"], 0.02), 0.98)
        dr = -400 * math.log10(1 / e - 1) - SETTINGS.home_advantage_elo
        if elo_h and not elo_a:
            return elo_h, elo_h - dr, "mercado"
        if elo_a and not elo_h:
            return elo_a + dr, elo_a, "mercado"
        return 1750 + dr / 2, 1750 - dr / 2, "mercado"
    return elo_h, elo_a, "promedio"


def _data_quality(side_h: dict, side_a: dict, has_odds: bool, deep: bool, elo_src: str = "elo") -> dict:
    items = []
    score = 30
    if elo_src == "elo":
        score += 25
        items.append("Nivel de ambos equipos (Elo)")
    elif elo_src == "mercado":
        score += 10
        items.append("Un equipo no tiene Elo publicado: su nivel se dedujo de las cuotas")
    else:
        items.append("Falta el Elo de algún equipo: se usó un valor promedio")
    if has_odds:
        score += 20
        items.append("Cuotas reales de casas de apuestas")
    if deep:
        score += 10
        items.append("Forma reciente y once probable según los últimos partidos")
        if side_h.get("llm") or side_a.get("llm"):
            score += 10
            items.append("Noticias de prensa interpretadas por IA")
        if side_h["confirmed_lineup"] and side_a["confirmed_lineup"]:
            score += 5
            items.append("Alineaciones confirmadas")
    else:
        items.append("Análisis profundo (bajas, noticias) disponible el día antes del partido")
    return {"score": min(score, 100), "items": items}


def run(api=None, *, elo_override: dict | None = None,
        headlines_fn=None, use_llm: bool = True, now: datetime | None = None) -> dict:
    t0 = time.time()
    now = now or datetime.now(timezone.utc)
    api = api or EspnApi()
    headlines_fn = headlines_fn or news.team_headlines

    season = SETTINGS.season or api.current_season(SETTINGS.league_id) or _guess_season(now)
    date_from = (now - timedelta(days=4)).date().isoformat()
    date_to = (now + timedelta(days=SETTINGS.days_ahead)).date().isoformat()
    fixtures = api.fixtures_range(SETTINGS.league_id, season, date_from, date_to)
    if not fixtures:
        log.error("No se encontraron partidos entre %s y %s; no se sobrescriben los datos.", date_from, date_to)
        sys.exit(1)
    log.info("Temporada %s: %d partidos entre %s y %s", season, len(fixtures), date_from, date_to)

    results = {f["fixture"]["id"]: (f["goals"]["home"], f["goals"]["away"]) for f in fixtures
               if f["fixture"]["status"]["short"] in ("FT", "AET", "PEN") and f["goals"]["home"] is not None}
    calib = grading.grade(results) if not SETTINGS.demo else {}

    upcoming = [f for f in fixtures if f["fixture"]["status"]["short"] in ("NS", "TBD", "1H", "HT", "2H", "ET", "P")
                or (f["fixture"]["timestamp"] > now.timestamp() - 3 * 3600)]
    upcoming.sort(key=lambda f: f["fixture"]["timestamp"])

    ratings = elo_override if elo_override is not None else clubelo.fetch_ratings(now.date().isoformat())

    # Cuotas por fecha (pocas peticiones para muchos partidos)
    odds_map: dict[int, dict] = {}
    for d in sorted({f["fixture"]["date"][:10] for f in upcoming if f["fixture"]["status"]["short"] == "NS"}):
        for entry in api.odds_by_date(SETTINGS.league_id, season, d):
            fid = entry.get("fixture", {}).get("id")
            if fid:
                odds_map[fid] = parse_odds(entry)

    matches = []
    deep_cut = now.timestamp() + SETTINGS.deep_window_hours * 3600
    for f in upcoming:
        fx, teams = f["fixture"], f["teams"]
        fid = fx["id"]
        status = fx["status"]["short"]
        hours_to = (fx["timestamp"] - now.timestamp()) / 3600
        kickoff_local = datetime.fromtimestamp(fx["timestamp"], TZ)
        deep = status in ("NS", "TBD") and fx["timestamp"] <= deep_cut

        elo_h, key_h = clubelo.lookup(teams["home"]["name"], ratings)
        elo_a, key_a = clubelo.lookup(teams["away"]["name"], ratings)

        intel_h = intel_a = None
        form_h = form_a = {"n": 0, "points": 0, "gd": 0, "string": "—", "games": []}
        h2h = {"n": 0}
        if deep:
            injuries = api.injuries(fid)
            match_news = api.match_news(fid) if hasattr(api, "match_news") else []
            sides = {}
            for side_key in ("home", "away"):
                team = teams[side_key]
                opp = teams["away" if side_key == "home" else "home"]
                last = api.team_last_fixtures(team["id"], 5)
                form = agent.form_from_fixtures(team["id"], last)
                fps = []
                for g in form["games"][:SETTINGS.last_fixtures_for_lineup]:
                    fps.append(api.fixture_players(g["fixture_id"]))
                sides[side_key] = (team, opp, form, fps, last)
            lineup = api.lineups(fid) if 0 < hours_to <= 2 else None
            # Historial directo (1 petición, en caché una semana); si falla, usa los últimos partidos
            h2h_fx = api.head_to_head(teams["home"]["id"], teams["away"]["id"])
            if not h2h_fx:
                pool = sides["home"][4] + sides["away"][4]
                ids = {teams["home"]["id"], teams["away"]["id"]}
                uniq = {x["fixture"]["id"]: x for x in pool
                        if {x["teams"]["home"]["id"], x["teams"]["away"]["id"]} == ids}
                h2h_fx = list(uniq.values())
            h2h = _h2h_from_fixtures(teams["home"]["id"], h2h_fx)
            for side_key, (team, opp, form, fps, _last) in sides.items():
                inj = [i for i in injuries if i.get("team", {}).get("id") == team["id"]]
                heads = headlines_fn(team["name"]) + match_news
                intel = agent.build_team_intel(
                    team["name"], opp["name"], kickoff_local.strftime("%Y-%m-%d %H:%M"), hours_to,
                    team["id"], inj, fps, heads, form, lineup, use_llm=use_llm)
                if side_key == "home":
                    intel_h, form_h = intel, form
                else:
                    intel_a, form_a = intel, form

        adj_h = intel_h.adjust if intel_h else TeamAdjust()
        adj_a = intel_a.adjust if intel_a else TeamAdjust()
        if h2h.get("n", 0) >= 3:
            tilt = max(-0.02, min(0.02, (h2h["w"] - h2h["l"]) / h2h["n"] * 0.02))
            adj_h.combine(1 + tilt, 1, None)
            adj_a.combine(1 - tilt, 1, None)

        odds = odds_map.get(fid, {})
        elo_h, elo_a, elo_src = _fill_elo(elo_h, elo_a, odds)
        mm = build_match(elo_h or DEFAULT_ELO, elo_a or DEFAULT_ELO, adj_h, adj_a,
                         SETTINGS.home_advantage_elo, SETTINGS.base_total_goals, SETTINGS.dixon_coles_rho)
        side_h = _team_side(teams["home"], elo_h, key_h, intel_h, form_h)
        side_a = _team_side(teams["away"], elo_a, key_a, intel_a, form_a)
        m = {
            "id": fid,
            "kickoff": datetime.fromtimestamp(fx["timestamp"], timezone.utc).isoformat(),
            "kickoff_local": kickoff_local.isoformat(),
            "day": kickoff_local.date().isoformat(),
            "round": f.get("league", {}).get("round", ""),
            "status": "NS" if status in ("NS", "TBD") else status,
            "score": {"home": f["goals"]["home"], "away": f["goals"]["away"]},
            "venue": (fx.get("venue") or {}).get("name"),
            "home": side_h,
            "away": side_a,
            "xg_home": round(mm.lam_home, 3),
            "xg_away": round(mm.lam_away, 3),
            "markets": _markets(mm, odds, teams["home"]["name"], teams["away"]["name"]),
            "top_scores": top_scores(mm.matrix),
            "h2h": h2h,
            "deep": deep,
            "has_odds": bool(odds),
        }
        for key, mk in m["markets"].items():
            mk["explanation"] = explain.leg_explanation(m, key, mk)
        m["reasons"] = explain.match_reasons(m)
        m["data_quality"] = _data_quality(side_h, side_a, bool(odds), deep, elo_src)
        m["alerts"] = _alerts(m)
        m["highlights"] = _highlights(m)
        matches.append(m)

    days: dict[str, list[dict]] = {}
    for m in matches:
        days.setdefault(m["day"], []).append(m)
    parl = parlays.build_all(matches, days)
    if not SETTINGS.demo:
        grading.archive(matches, parl)

    api.save()
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_local": datetime.now(TZ).isoformat(),
        "season": season,
        "fixtures_source": "espn" if isinstance(api, EspnApi) else "api",
        "demo": SETTINGS.demo,
        "days": sorted(days),
        "matches": matches,
        "market_groups": MARKET_GROUPS,
        "parlays": parl,
        "tiers": parlays.tiers_meta(),
        "calibration_summary": {"matches_graded": calib.get("matches_graded"), "brier": calib.get("brier")},
        "run": {
            "seconds": round(time.time() - t0, 1),
            "api_requests": api.used,
            "api_budget": api.budget,
            "llm": dict(llm.USAGE),
            "sources": http.STATUS,
        },
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "latest.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    log.info("Listo: %d partidos, %d peticiones a fuentes de datos, IA: %s", len(matches), api.used, llm.USAGE)
    return out


def _alerts(m: dict) -> list[str]:
    alerts = []
    for side in (m["home"], m["away"]):
        for a in side["absences"]:
            if a.get("importance") == "key" and a["status"] in ("out", "suspended"):
                alerts.append(f"{side['name']}: sin {a['name']} ({a['status_es'].lower()})")
            elif a.get("importance") == "key" and a["status"] == "doubt":
                alerts.append(f"{side['name']}: {a['name']} en duda")
    return alerts[:4]


def _highlights(m: dict) -> dict:
    mk = m["markets"]
    main = ["1", "X", "2", "1X", "X2", "over_1.5", "over_2.5", "under_2.5", "over_3.5", "under_3.5",
            "btts_yes", "btts_no"]
    likely = max((k for k in main if mk[k]["p"] <= 0.92), key=lambda k: mk[k]["p"])
    value = None
    cands = [k for k in mk if mk[k].get("ev") is not None and mk[k]["ev"] > 0.03 and mk[k]["p"] >= 0.25]
    if cands:
        value = max(cands, key=lambda k: mk[k]["ev"])
    return {"likely": likely, "value": value}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="Datos de ejemplo, sin llamar APIs")
    args = ap.parse_args()
    if args.demo or SETTINGS.demo or not SETTINGS.api_football_key:
        from .demo import run_demo
        SETTINGS.demo = True
        run_demo()
    else:
        run()


if __name__ == "__main__":
    main()
