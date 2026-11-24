"""Motor de probabilidades.

Idea general:
1. La fuerza base de cada equipo sale de su Elo (ClubElo), más la ventaja de local.
2. La diferencia de Elo se convierte en goles esperados (lambda) de cada equipo.
   La constante de conversión se calibra para que el modelo de goles reproduzca
   la expectativa de resultado del propio Elo.
3. Los goles esperados se ajustan con multiplicadores de ataque/defensa
   (bajas, forma, rotaciones).
4. Con un Poisson bivariado con corrección Dixon-Coles se obtiene la matriz de
   marcadores, y de ella todas las probabilidades de mercado de forma exacta,
   incluidas las combinadas dentro de un mismo partido.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

MAX_GOALS = 10


def elo_expectancy(dr: float) -> float:
    """Puntuación esperada (victoria=1, empate=0.5) para una diferencia de Elo."""
    return 1.0 / (1.0 + 10 ** (-dr / 400.0))


def _poisson_pmf(lam: float, n: int = MAX_GOALS) -> np.ndarray:
    k = np.arange(n + 1)
    logp = -lam + k * math.log(max(lam, 1e-9)) - np.array([math.lgamma(i + 1) for i in k])
    return np.exp(logp)


def score_matrix(lam_h: float, lam_a: float, rho: float = -0.06) -> np.ndarray:
    """Matriz P[i, j] = probabilidad de que el local marque i y el visitante j."""
    ph = _poisson_pmf(lam_h)
    pa = _poisson_pmf(lam_a)
    m = np.outer(ph, pa)
    # Corrección Dixon-Coles para marcadores bajos
    m[0, 0] *= max(1 - lam_h * lam_a * rho, 0)
    m[0, 1] *= max(1 + lam_h * rho, 0)
    m[1, 0] *= max(1 + lam_a * rho, 0)
    m[1, 1] *= max(1 - rho, 0)
    return m / m.sum()


def lambdas_from_dr(dr: float, base_total: float, k: float) -> tuple[float, float]:
    half = base_total / 2.0
    x = dr / 100.0
    return half * math.exp(k * x), half * math.exp(-k * x)


@lru_cache(maxsize=16)
def calibrate_k(base_total: float = 2.95, rho: float = -0.06) -> float:
    """Busca k para que P(1) + 0.5*P(X) del modelo de goles coincida con el Elo."""
    grid = np.linspace(-450, 450, 37)
    best_k, best_err = 0.2, float("inf")
    for k in np.linspace(0.05, 0.45, 81):
        err = 0.0
        for dr in grid:
            lh, la = lambdas_from_dr(dr, base_total, k)
            m = score_matrix(lh, la, rho)
            ph = np.tril(m, -1).sum()
            pd = np.trace(m)
            err += (ph + 0.5 * pd - elo_expectancy(dr)) ** 2
        if err < best_err:
            best_k, best_err = float(k), err
    return best_k


@dataclass
class TeamAdjust:
    """Multiplicadores sobre goles. attack<1 marca menos; defense>1 recibe más."""

    attack: float = 1.0
    defense: float = 1.0
    reasons: list[str] = field(default_factory=list)

    def combine(self, attack: float = 1.0, defense: float = 1.0, reason: str | None = None):
        self.attack *= attack
        self.defense *= defense
        if reason:
            self.reasons.append(reason)

    def clamp(self, lo: float = 0.72, hi: float = 1.30):
        self.attack = min(max(self.attack, lo), hi)
        self.defense = min(max(self.defense, 1 / hi), 1 / lo)


@dataclass
class MatchModel:
    lam_home: float
    lam_away: float
    matrix: np.ndarray
    dr: float


def build_match(
    elo_home: float,
    elo_away: float,
    adj_home: TeamAdjust | None = None,
    adj_away: TeamAdjust | None = None,
    home_advantage: float = 65.0,
    base_total: float = 2.95,
    rho: float = -0.06,
    neutral: bool = False,
) -> MatchModel:
    adj_home = adj_home or TeamAdjust()
    adj_away = adj_away or TeamAdjust()
    dr = elo_home - elo_away + (0.0 if neutral else home_advantage)
    k = calibrate_k(base_total, rho)
    lh, la = lambdas_from_dr(dr, base_total, k)
    lh *= adj_home.attack * adj_away.defense
    la *= adj_away.attack * adj_home.defense
    lh, la = max(lh, 0.08), max(la, 0.08)
    return MatchModel(lh, la, score_matrix(lh, la, rho), dr)


# ---------------------------------------------------------------------------
# Mercados
# ---------------------------------------------------------------------------

def _masks(n: int = MAX_GOALS + 1):
    i = np.arange(n)[:, None]
    j = np.arange(n)[None, :]
    return i, j


def market_probabilities(m: np.ndarray) -> dict[str, float]:
    """Probabilidades exactas de cada mercado a partir de la matriz de marcadores."""
    i, j = _masks(m.shape[0])
    home = i > j
    draw = i == j
    away = i < j
    tot = i + j
    btts = (i > 0) & (j > 0)

    def p(mask) -> float:
        return float(m[np.broadcast_to(mask, m.shape)].sum())

    out = {
        "1": p(home),
        "X": p(draw),
        "2": p(away),
        "1X": p(home | draw),
        "X2": p(away | draw),
        "12": p(home | away),
        "btts_yes": p(btts),
        "btts_no": p(~btts),
        "home_over_0.5": p(i >= 1),
        "away_over_0.5": p(j >= 1),
        "home_over_1.5": p(i >= 2),
        "away_over_1.5": p(j >= 2),
        # Combinadas dentro del mismo partido (calculadas en conjunto, no multiplicadas)
        "1_over_1.5": p(home & (tot >= 2)),
        "2_over_1.5": p(away & (tot >= 2)),
        "1_btts_yes": p(home & btts),
        "2_btts_yes": p(away & btts),
        "1X_under_3.5": p((home | draw) & (tot <= 3)),
        "X2_under_3.5": p((away | draw) & (tot <= 3)),
        "1X_btts_yes": p((home | draw) & btts),
        "X2_btts_yes": p((away | draw) & btts),
    }
    for line in (0.5, 1.5, 2.5, 3.5, 4.5):
        out[f"over_{line}"] = p(tot > line)
        out[f"under_{line}"] = p(tot < line)
    return out


def market_label(key: str, home: str, away: str) -> str:
    labels = {
        "1": f"Gana {home}",
        "X": "Empate",
        "2": f"Gana {away}",
        "1X": f"{home} o empate",
        "X2": f"{away} o empate",
        "12": "No hay empate",
        "btts_yes": "Ambos marcan: Sí",
        "btts_no": "Ambos marcan: No",
        "home_over_0.5": f"{home} marca",
        "away_over_0.5": f"{away} marca",
        "home_over_1.5": f"{home} marca 2 o más",
        "away_over_1.5": f"{away} marca 2 o más",
        "1_over_1.5": f"Gana {home} y +1.5 goles",
        "2_over_1.5": f"Gana {away} y +1.5 goles",
        "1_btts_yes": f"Gana {home} y ambos marcan",
        "2_btts_yes": f"Gana {away} y ambos marcan",
        "1X_under_3.5": f"{home} o empate y -3.5 goles",
        "X2_under_3.5": f"{away} o empate y -3.5 goles",
        "1X_btts_yes": f"{home} o empate y ambos marcan",
        "X2_btts_yes": f"{away} o empate y ambos marcan",
    }
    if key in labels:
        return labels[key]
    if key.startswith("over_"):
        return f"Más de {key[5:]} goles"
    if key.startswith("under_"):
        return f"Menos de {key[6:]} goles"
    return key


MARKET_GROUPS = {
    "Resultado": ["1", "X", "2"],
    "Doble oportunidad": ["1X", "X2", "12"],
    "Goles": [
        "over_1.5", "under_1.5", "over_2.5", "under_2.5", "over_3.5", "under_3.5",
    ],
    "Ambos marcan": ["btts_yes", "btts_no"],
    "Goles por equipo": ["home_over_0.5", "away_over_0.5", "home_over_1.5", "away_over_1.5"],
    "Combinadas del partido": [
        "1_over_1.5", "2_over_1.5", "1_btts_yes", "2_btts_yes",
        "1X_under_3.5", "X2_under_3.5", "1X_btts_yes", "X2_btts_yes",
    ],
}


def top_scores(m: np.ndarray, n: int = 6) -> list[dict]:
    flat = [(float(m[i, j]), i, j) for i in range(m.shape[0]) for j in range(m.shape[1])]
    flat.sort(reverse=True)
    return [{"score": f"{i}-{j}", "p": round(p, 4)} for p, i, j in flat[:n]]


def remove_margin(odds: dict[str, float]) -> dict[str, float]:
    """Convierte cuotas de un mercado mutuamente excluyente en probabilidades justas."""
    inv = {k: 1.0 / v for k, v in odds.items() if v and v > 1.0}
    s = sum(inv.values())
    if s <= 0:
        return {}
    return {k: v / s for k, v in inv.items()}


def blend(p_model: float, p_market: float | None, weight_market: float) -> float:
    """Mezcla en escala logit: más estable que un promedio simple en los extremos."""
    if p_market is None:
        return p_model
    eps = 1e-4
    pm = min(max(p_model, eps), 1 - eps)
    pk = min(max(p_market, eps), 1 - eps)
    lg = (1 - weight_market) * math.log(pm / (1 - pm)) + weight_market * math.log(pk / (1 - pk))
    return 1 / (1 + math.exp(-lg))
