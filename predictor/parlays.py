"""Generador de parleys por nivel de riesgo.

Reglas importantes:
- Una sola selección por partido. Las combinadas dentro de un mismo partido ya
  vienen calculadas en conjunto desde la matriz de marcadores (por ejemplo
  "Gana X y +1.5 goles"), así que no se multiplican probabilidades correlacionadas.
- Partidos distintos se tratan como independientes: P(parley) = producto.
- La cuota se toma de la casa cuando existe; si no, se estima con un margen típico
  y se marca como "estimada".
"""
from __future__ import annotations

import hashlib
import math

TYPICAL_MARGIN = 1.07  # margen típico de las casas en mercados secundarios

PARLAY_MARKETS = [
    "1", "X", "2", "1X", "X2", "12",
    "over_1.5", "under_1.5", "over_2.5", "under_2.5", "over_3.5", "under_3.5",
    "btts_yes", "btts_no", "home_over_0.5", "away_over_0.5", "home_over_1.5", "away_over_1.5",
    "1_over_1.5", "2_over_1.5", "1_btts_yes", "2_btts_yes", "1X_under_3.5", "X2_under_3.5",
]

TIERS = {
    "seguro": {
        "label": "Seguro", "min_p": 0.70, "max_p": 0.97, "sizes": (2, 3), "min_total_p": 0.40,
        "desc": "Pocas selecciones de alta probabilidad. Paga poco, se cae poco.",
    },
    "equilibrado": {
        "label": "Equilibrado", "min_p": 0.45, "max_p": 0.80, "sizes": (3, 5), "min_total_p": 0.08,
        "desc": "Balance entre probabilidad y cuota.",
    },
    "sonador": {
        "label": "Soñador", "min_p": 0.30, "max_p": 0.80, "sizes": (6, 10), "min_total_p": 0.002,
        "desc": "Muchas selecciones y cuota grande. Se gana muy pocas veces.",
    },
}


def leg_odds(mk: dict) -> tuple[float, bool]:
    if mk.get("odds") and mk["odds"] > 1.0:
        return float(mk["odds"]), False
    return round(max(1.01, 1.0 / (mk["p"] * TYPICAL_MARGIN)), 2), True


def _leg_score(tier: str, p: float, odds: float, edge: float, real_odds: bool) -> float:
    edge = max(edge, -0.06)
    bonus = 0.04 if real_odds else 0.0  # preferimos cuotas reales: se pueden verificar en la casa
    if tier == "seguro":
        return p + 0.2 * edge + bonus
    if tier == "equilibrado":
        return 0.6 * math.log(p) + 0.4 * math.log(odds) + 2.0 * edge + bonus
    return 0.3 * math.log(p) + 0.7 * math.log(odds) + 2.0 * edge + bonus


def candidate_legs(matches: list[dict], tier: str) -> dict[int, list[dict]]:
    cfg = TIERS[tier]
    out: dict[int, list[dict]] = {}
    for m in matches:
        legs = []
        for key in PARLAY_MARKETS:
            mk = m["markets"].get(key)
            if not mk:
                continue
            p = mk["p"]
            if not (cfg["min_p"] <= p <= cfg["max_p"]):
                continue
            odds, est = leg_odds(mk)
            if odds < 1.05:
                continue
            edge = mk.get("edge") or 0.0
            legs.append({
                "match_id": m["id"],
                "match": f"{m['home']['name']} vs {m['away']['name']}",
                "kickoff": m["kickoff"],
                "key": key,
                "label": mk["label"],
                "p": round(p, 4),
                "odds": odds,
                "odds_estimated": est,
                "edge": round(edge, 4),
                "explanation": mk.get("explanation", ""),
                "_score": _leg_score(tier, p, odds, edge, not est),
            })
        legs.sort(key=lambda l: -l["_score"])
        if legs:
            out[m["id"]] = legs
    return out


def _finish(legs: list[dict], tier: str, scope: str) -> dict:
    p = math.prod(l["p"] for l in legs)
    odds = math.prod(l["odds"] for l in legs)
    clean = [{k: v for k, v in l.items() if not k.startswith("_")} for l in legs]
    clean.sort(key=lambda l: l["kickoff"])
    pid = hashlib.md5("|".join(f"{l['match_id']}:{l['key']}" for l in clean).encode()).hexdigest()[:10]
    return {
        "id": pid,
        "tier": tier,
        "tier_label": TIERS[tier]["label"],
        "scope": scope,
        "legs": clean,
        "p": round(p, 5),
        "odds": round(odds, 2),
        "ev": round(p * odds - 1, 4),
        "odds_estimated": any(l["odds_estimated"] for l in clean),
    }


def build_tier(matches: list[dict], tier: str, scope: str, variants: int = 4) -> list[dict]:
    cfg = TIERS[tier]
    min_n, max_n = cfg["sizes"]
    cands = candidate_legs(matches, tier)
    if tier == "sonador":
        min_n = max(4, min(min_n, len(cands)))
    if len(cands) < min_n:
        return []
    results: list[dict] = []
    used: dict[str, int] = {}
    seen: set[str] = set()
    for v in range(variants * 3):
        # Para cada partido, la mejor selección penalizando las ya usadas (da variedad)
        options = []
        for mid, legs in cands.items():
            best = max(legs, key=lambda l: l["_score"] - 0.35 * used.get(f"{mid}:{l['key']}", 0)
                       - (0.08 * legs.index(l) if v % 2 else 0))
            options.append(best)
        # Varía qué partidos entran
        options.sort(key=lambda l: -(l["_score"] - 0.25 * used.get(f"{l['match_id']}:{l['key']}", 0)))
        if v >= 1 and len(options) > max_n:
            skip = v % len(options)
            options = options[:skip] + options[skip + 1:] + options[skip:skip + 1]
        chosen: list[dict] = []
        p = 1.0
        for leg in options:
            if len(chosen) >= max_n:
                break
            if len(chosen) >= min_n and p * leg["p"] < cfg["min_total_p"]:
                break
            chosen.append(leg)
            p *= leg["p"]
            if tier == "seguro" and len(chosen) >= min_n and p < 0.55:
                break
            if tier == "equilibrado" and len(chosen) >= min_n and p < 0.30:
                break
        if len(chosen) < min_n or p < cfg["min_total_p"] * 0.5:
            continue
        parlay = _finish(chosen, tier, scope)
        if parlay["id"] in seen:
            for l in chosen:
                used[f"{l['match_id']}:{l['key']}"] = used.get(f"{l['match_id']}:{l['key']}", 0) + 1
            continue
        seen.add(parlay["id"])
        results.append(parlay)
        for l in chosen:
            used[f"{l['match_id']}:{l['key']}"] = used.get(f"{l['match_id']}:{l['key']}", 0) + 1
        if len(results) >= variants:
            break
    results.sort(key=lambda x: -x["p"])
    return results


def build_all(matches: list[dict], days: dict[str, list[dict]]) -> dict:
    """Parleys por día y para toda la ventana."""
    upcoming = [m for m in matches if m["status"] == "NS"]
    out = {"by_day": {}, "all": {}}
    for day, ms in days.items():
        ms = [m for m in ms if m["status"] == "NS"]
        out["by_day"][day] = {t: build_tier(ms, t, day) for t in TIERS}
    if len(days) > 1:
        out["all"] = {t: build_tier(upcoming, t, "all") for t in TIERS}
    return out


def tiers_meta() -> dict:
    return {k: {"label": v["label"], "desc": v["desc"]} for k, v in TIERS.items()}
