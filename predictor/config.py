"""Configuración central. Todo se puede ajustar con variables de entorno."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
HISTORY_DIR = DATA_DIR / "history"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Settings:
    # --- Claves (vienen de los secrets de GitHub) ---
    api_football_key: str = field(default_factory=lambda: _env("API_FOOTBALL_KEY"))
    groq_api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY"))
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))

    # --- Modelos de IA gratuitos ---
    groq_model: str = field(default_factory=lambda: _env("GROQ_MODEL", "openai/gpt-oss-120b"))
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-flash-latest"))

    # --- Competición ---
    league_id: int = 2  # UEFA Champions League en API-Football
    season: int | None = field(
        default_factory=lambda: int(_env("SEASON")) if _env("SEASON") else None
    )

    # --- Presupuesto de peticiones a API-Football (plan gratis = 100/día) ---
    api_budget: int = field(default_factory=lambda: int(_env("API_BUDGET", "75")))
    last_fixtures_for_lineup: int = 2  # partidos previos usados para estimar titulares

    # --- Ventana de análisis ---
    days_ahead: int = field(default_factory=lambda: int(_env("DAYS_AHEAD", "8")))
    deep_window_hours: int = 72  # noticias y once probable para partidos dentro de esta ventana

    # --- Parámetros del modelo ---
    home_advantage_elo: float = 65.0
    base_total_goals: float = 2.95  # media de goles por partido en Champions (fase liga)
    dixon_coles_rho: float = -0.06
    market_weight: float = 0.5  # peso del mercado al mezclar con el modelo cuando hay cuotas
    max_injury_penalty_elo: float = 90.0

    demo: bool = field(default_factory=lambda: _env("DEMO", "0") == "1")


SETTINGS = Settings()
