"""Explicaciones en español para cada selección, a partir de los factores del modelo."""
from __future__ import annotations


def pct(p: float) -> str:
    return f"{round(p * 100)}%"


def _absences_txt(side: dict) -> str:
    big = [a for a in side.get("absences", []) if a.get("importance") in ("key", "starter")
           and a.get("status") in ("out", "suspended", "doubt")]
    if not big:
        return ""
    names = ", ".join(f"{a['name']} ({a['status_es'].lower()})" for a in big[:3])
    return f"{side['name']} tiene bajas importantes: {names}."


def _form_txt(side: dict) -> str:
    f = side.get("form") or {}
    if not f.get("n"):
        return ""
    w = f["string"].count("G")
    return f"{side['name']} ganó {w} de sus últimos {f['n']} ({f['string']})."


def match_reasons(match: dict) -> list[str]:
    """Factores clave del partido, ordenados por importancia."""
    h, a = match["home"], match["away"]
    out = []
    if h.get("elo") and a.get("elo"):
        diff = h["elo"] - a["elo"]
        if abs(diff) >= 40:
            fav, dog = (h, a) if diff > 0 else (a, h)
            out.append(f"{fav['name']} es superior en nivel (Elo {round(fav['elo'])} vs {round(dog['elo'])}).")
        else:
            out.append(f"Equipos parejos en nivel (Elo {round(h['elo'])} vs {round(a['elo'])}).")
    out.append(f"{h['name']} juega de local.")
    for side in (h, a):
        t = _absences_txt(side)
        if t:
            out.append(t)
    for side in (h, a):
        t = _form_txt(side)
        if t:
            out.append(t)
    for side in (h, a):
        if side.get("rotation_risk", 0) >= 0.35:
            out.append(f"Posible rotación en {side['name']}: {side.get('rotation_reason') or 'gestión de carga'}.")
    h2h = match.get("h2h") or {}
    if h2h.get("n"):
        out.append(f"Historial reciente entre ambos: {h2h['summary']} (peso bajo en el modelo).")
    out.append(f"Goles esperados: {h['name']} {match['xg_home']:.2f} – {match['xg_away']:.2f} {a['name']}.")
    return out


def leg_explanation(match: dict, key: str, mk: dict) -> str:
    h, a = match["home"], match["away"]
    xh, xa = match["xg_home"], match["xg_away"]
    parts: list[str] = []
    base = key.split("_")[0] if "_" in key and key[0] in "12X" else key

    if base in ("1", "1X"):
        parts.append(_strength_line(h, a))
        parts.append(_absences_txt(a) or _form_txt(h))
    elif base in ("2", "X2"):
        parts.append(_strength_line(a, h))
        parts.append(_absences_txt(h) or _form_txt(a))
    elif base == "X":
        parts.append(f"Partido muy parejo: goles esperados {xh:.2f} – {xa:.2f}.")
    elif base == "12":
        parts.append(f"Al menos un equipo debería imponerse: goles esperados {xh:.2f} – {xa:.2f}.")

    if "over" in key or "btts_yes" in key:
        parts.append(f"Se esperan {xh + xa:.1f} goles en total ({h['name']} {xh:.2f}, {a['name']} {xa:.2f}).")
    elif "under" in key or "btts_no" in key:
        parts.append(f"Se esperan pocos goles: {xh + xa:.1f} en total.")
    if key.startswith("home_over"):
        parts.append(f"{h['name']} genera {xh:.2f} goles esperados de local.")
    if key.startswith("away_over"):
        parts.append(f"{a['name']} genera {xa:.2f} goles esperados de visitante.")

    if mk.get("p_market") is not None:
        diff = mk["p_model"] - mk["p_market"]
        if diff >= 0.03:
            parts.append(f"El modelo lo ve más probable que la casa ({pct(mk['p_model'])} vs {pct(mk['p_market'])}).")
        elif diff <= -0.03:
            parts.append(f"La casa lo ve más probable que el modelo ({pct(mk['p_market'])} vs {pct(mk['p_model'])}).")
        else:
            parts.append(f"Modelo y casa coinciden (~{pct(mk['p'])}).")
    parts = [p for p in parts if p]
    return " ".join(dict.fromkeys(parts))


def _strength_line(fav: dict, dog: dict) -> str:
    if fav.get("elo") and dog.get("elo"):
        d = fav["elo"] - dog["elo"]
        if d >= 40:
            return f"{fav['name']} tiene más nivel (Elo +{round(d)})."
        if d <= -40:
            return f"{fav['name']} tiene menos nivel en el papel (Elo {round(d)}), pero el modelo le da margen."
        return f"Nivel parejo (diferencia de Elo {round(d):+d})."
    return ""
