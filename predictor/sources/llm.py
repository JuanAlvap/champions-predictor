"""IA gratuita para leer noticias: varios modelos de Groq en rotación y Gemini de respaldo.

Los planes gratis limitan tokens por minuto, así que:
- se rota entre varios modelos de Groq (cada uno tiene su propio límite);
- si uno responde 429, se respeta su tiempo de espera y se pasa al siguiente;
- Gemini se reintenta cuando está saturado (503).
Si nada responde, el agente sigue sin interpretar titulares.
"""
from __future__ import annotations

import json
import logging
import re
import time

import requests

from ..config import SETTINGS
from . import http

log = logging.getLogger("predictor")

USAGE: dict = {"groq": 0, "gemini": 0, "failed": 0, "models": {}}

GROQ_MODELS = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
]
_cooldown: dict[str, float] = {}  # modelo -> momento en que se puede volver a usar
_dead: set[str] = set()  # modelos que no existen o sin acceso


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


def _retry_after(r: requests.Response, default: float) -> float:
    for h in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        v = r.headers.get(h)
        if not v:
            continue
        m = re.match(r"(?:(\d+)m)?([\d.]+)s?$", v.strip())
        try:
            if m:
                return float(m.group(1) or 0) * 60 + float(m.group(2))
            return float(v)
        except ValueError:
            continue
    return default


def _groq_once(model: str, system: str, user: str) -> tuple[dict | None, str]:
    """Devuelve (json, estado) con estado en {ok, rate, dead, error}."""
    try:
        r = http.SESSION.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {SETTINGS.groq_api_key}"},
            json={"model": model, "temperature": 0.1, "max_tokens": 1200,
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
            timeout=60,
        )
    except requests.RequestException as e:
        http.mark("groq", False, str(e))
        return None, "error"
    if r.status_code == 429:
        wait = _retry_after(r, 30)
        _cooldown[model] = time.time() + min(wait, 120)
        http.mark("groq", False, f"429 en {model}, espera {wait:.0f}s")
        return None, "rate"
    if r.status_code in (400, 401, 403, 404):
        body = r.text[:200]
        if r.status_code in (401, 403) or "model" in body.lower():
            _dead.add(model)
        http.mark("groq", False, f"{r.status_code} {model}: {body}")
        return None, "dead"
    if not r.ok:
        http.mark("groq", False, f"{r.status_code} {model}")
        return None, "error"
    try:
        content = r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError):
        return None, "error"
    out = _extract_json(content)
    if out is None:
        return None, "error"
    http.mark("groq", True)
    return out, "ok"


def _groq(system: str, user: str) -> tuple[dict | None, str | None]:
    if not SETTINGS.groq_api_key:
        return None, None
    models = [m for m in ([SETTINGS.groq_model] + GROQ_MODELS) if m not in _dead]
    models = list(dict.fromkeys(models))
    for _round in range(3):
        ready = [m for m in models if _cooldown.get(m, 0) <= time.time() and m not in _dead]
        for m in ready:
            out, st = _groq_once(m, system, user)
            if st == "ok":
                return out, m
        alive = [m for m in models if m not in _dead]
        if not alive:
            return None, None
        wait = min(_cooldown.get(m, 0) for m in alive) - time.time()
        if wait > 45:
            return None, None  # todos esperando mucho: que responda Gemini
        if wait > 0:
            time.sleep(wait + 0.5)
    return None, None


def _gemini(system: str, user: str) -> dict | None:
    if not SETTINGS.gemini_api_key or "gemini" in _dead:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{SETTINGS.gemini_model}:generateContent"
    for attempt in range(3):
        if _cooldown.get("gemini", 0) > time.time():
            return None
        try:
            r = http.SESSION.post(url, headers={"x-goog-api-key": SETTINGS.gemini_api_key}, timeout=60, json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
            })
        except requests.RequestException as e:
            http.mark("gemini", False, str(e))
            time.sleep(3)
            continue
        if r.status_code in (500, 502, 503, 504):
            http.mark("gemini", False, f"{r.status_code} saturado")
            time.sleep(4 * (attempt + 1))
            continue
        if r.status_code == 429:
            _cooldown["gemini"] = time.time() + min(_retry_after(r, 30), 90)
            http.mark("gemini", False, "429 sin cuota por ahora")
            return None
        if r.status_code in (400, 401, 403, 404):
            _dead.add("gemini")
            http.mark("gemini", False, f"{r.status_code}: {r.text[:200]}")
            return None
        try:
            parts = r.json()["candidates"][0]["content"]["parts"]
            out = _extract_json("".join(p.get("text", "") for p in parts))
        except (KeyError, IndexError, ValueError):
            out = None
        if out is not None:
            http.mark("gemini", True)
            return out
    return None


def ask_json(system: str, user: str) -> tuple[dict | None, str | None]:
    """Devuelve (respuesta, proveedor usado)."""
    out, model = _groq(system, user)
    if out is not None:
        USAGE["groq"] += 1
        USAGE["models"][model] = USAGE["models"].get(model, 0) + 1
        return out, "groq"
    out = _gemini(system, user)
    if out is not None:
        USAGE["gemini"] += 1
        return out, "gemini"
    # Último intento: esperar a que se libere algún modelo de Groq
    alive = [m for m in GROQ_MODELS if m not in _dead]
    if SETTINGS.groq_api_key and alive:
        wait = min(_cooldown.get(m, 0) for m in alive) - time.time()
        if 0 < wait <= 70:
            time.sleep(wait + 0.5)
            out, model = _groq(system, user)
            if out is not None:
                USAGE["groq"] += 1
                USAGE["models"][model] = USAGE["models"].get(model, 0) + 1
                return out, "groq"
    USAGE["failed"] += 1
    return None, None


def available() -> bool:
    return bool(SETTINGS.groq_api_key or SETTINGS.gemini_api_key)
