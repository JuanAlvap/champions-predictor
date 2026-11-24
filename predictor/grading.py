"""Archivo de predicciones y medición de qué tan confiable es el modelo."""
from __future__ import annotations

import json
import time

from .config import HISTORY_DIR

PRED_FILE = HISTORY_DIR / "predictions.json"
PARLAY_FILE = HISTORY_DIR / "parlays.json"
CALIB_FILE = HISTORY_DIR.parent / "calibration.json"

TRACKED = ["1", "X", "2", "1X", "X2", "over_1.5", "over_2.5", "under_2.5", "over_3.5", "btts_yes", "btts_no"]


def outcome(key: str, gh: int, ga: int) -> bool:
    tot = gh + ga
    res = "1" if gh > ga else "X" if gh == ga else "2"
    btts = gh > 0 and ga > 0
    simple = {
        "1": res == "1", "X": res == "X", "2": res == "2",
        "1X": res in ("1", "X"), "X2": res in ("X", "2"), "12": res in ("1", "2"),
        "btts_yes": btts, "btts_no": not btts,
        "home_over_0.5": gh >= 1, "away_over_0.5": ga >= 1,
        "home_over_1.5": gh >= 2, "away_over_1.5": ga >= 2,
        "1_over_1.5": res == "1" and tot >= 2, "2_over_1.5": res == "2" and tot >= 2,
        "1_btts_yes": res == "1" and btts, "2_btts_yes": res == "2" and btts,
        "1X_under_3.5": res in ("1", "X") and tot <= 3, "X2_under_3.5": res in ("X", "2") and tot <= 3,
        "1X_btts_yes": res in ("1", "X") and btts, "X2_btts_yes": res in ("X", "2") and btts,
    }
    if key in simple:
        return simple[key]
    if key.startswith("over_"):
        return tot > float(key[5:])
    if key.startswith("under_"):
        return tot < float(key[6:])
    raise KeyError(key)


def _load(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _save(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def archive(matches: list[dict], parlays: dict) -> None:
    preds = _load(PRED_FILE, {})
    now = time.time()
    for m in matches:
        if m["status"] != "NS":
            continue
        preds[str(m["id"])] = {
            "t": now,
            "kickoff": m["kickoff"],
            "match": f"{m['home']['name']} vs {m['away']['name']}",
            "p": {k: round(m["markets"][k]["p"], 4) for k in TRACKED if k in m["markets"]},
            "p_model": {k: round(m["markets"][k]["p_model"], 4) for k in TRACKED if k in m["markets"]},
        }
    _save(PRED_FILE, preds)

    store = _load(PARLAY_FILE, {})
    for day, tiers in parlays.get("by_day", {}).items():
        for tier, plist in tiers.items():
            for p in plist:
                store.setdefault(p["id"], {**p, "day": day, "t": now})
    _save(PARLAY_FILE, store)


def grade(results: dict[int, tuple[int, int]]) -> dict:
    """results: {fixture_id: (goles_local, goles_visitante)} de partidos terminados."""
    preds = _load(PRED_FILE, {})
    for mid, pr in preds.items():
        r = results.get(int(mid))
        if r and "result" not in pr:
            pr["result"] = list(r)
    _save(PRED_FILE, preds)

    store = _load(PARLAY_FILE, {})
    for p in store.values():
        if p.get("graded"):
            continue
        status = []
        for leg in p["legs"]:
            r = results.get(int(leg["match_id"]))
            if r is None:
                pr = preds.get(str(leg["match_id"]), {})
                r = tuple(pr["result"]) if pr.get("result") else None
            if r is None:
                status.append(None)
            else:
                leg["hit"] = outcome(leg["key"], *r)
                status.append(leg["hit"])
        if all(s is not None for s in status):
            p["graded"] = True
            p["hit"] = all(status)
            p["legs_hit"] = sum(1 for s in status if s)
    _save(PARLAY_FILE, store)
    return calibration(preds, store)


def calibration(preds: dict, store: dict) -> dict:
    buckets = [(i / 10, (i + 1) / 10) for i in range(10)]
    agg = [{"lo": lo, "hi": hi, "n": 0, "hits": 0, "p_sum": 0.0} for lo, hi in buckets]
    brier_sum = brier_n = 0
    graded_matches = 0
    for pr in preds.values():
        if "result" not in pr:
            continue
        graded_matches += 1
        gh, ga = pr["result"]
        for key, p in pr["p"].items():
            hit = outcome(key, gh, ga)
            idx = min(int(p * 10), 9)
            b = agg[idx]
            b["n"] += 1
            b["hits"] += int(hit)
            b["p_sum"] += p
            brier_sum += (p - hit) ** 2
            brier_n += 1
    for b in agg:
        b["p_avg"] = round(b["p_sum"] / b["n"], 3) if b["n"] else None
        b["hit_rate"] = round(b["hits"] / b["n"], 3) if b["n"] else None
        del b["p_sum"]

    tiers: dict[str, dict] = {}
    for p in store.values():
        if not p.get("graded"):
            continue
        t = tiers.setdefault(p["tier"], {"n": 0, "hits": 0, "p_sum": 0.0, "legs": 0, "legs_hit": 0,
                                           "units": 0.0})
        t["n"] += 1
        t["hits"] += int(p["hit"])
        t["p_sum"] += p["p"]
        t["legs"] += len(p["legs"])
        t["legs_hit"] += p["legs_hit"]
        t["units"] += (p["odds"] - 1) if p["hit"] else -1
    for t in tiers.values():
        t["expected_hits"] = round(t.pop("p_sum"), 2)
        t["units"] = round(t["units"], 2)

    recent = sorted((p for p in store.values() if p.get("graded")), key=lambda p: -p["t"])[:30]
    out = {
        "updated": time.time(),
        "matches_graded": graded_matches,
        "brier": round(brier_sum / brier_n, 4) if brier_n else None,
        "buckets": agg,
        "parlay_tiers": tiers,
        "recent_parlays": [{k: p[k] for k in ("id", "tier", "tier_label", "day", "p", "odds", "hit",
                                               "legs_hit", "legs")} for p in recent],
    }
    _save(CALIB_FILE, out)
    return out
