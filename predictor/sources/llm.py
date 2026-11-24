"""IA gratuita para leer noticias: Groq primero, Gemini de respaldo.

Si una se queda sin cuota (429) o falla, se pasa a la otra automáticamente.
Si ninguna está disponible, el agente sigue funcionando solo con los datos de
API-Football (lesiones oficiales), sin interpretar titulares.
"""
from __future__ import annotations

import json
import logging
import re

from ..config import SETTINGS
from . import http

log = logging.getLogger("predictor")

USAGE = {"groq": 0, "gemini": 0, "failed": 0}
_disabled: set[str] = set()


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    text = text.strip()
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


def _groq(system: str, user: str) -> dict | None:
    if not SETTINGS.groq_api_key or "groq" in _disabled:
        return None
    r = http.post(
        "https://api.groq.com/openai/v1/chat/completions",
        "groq",
        headers={"Authorization": f"Bearer {SETTINGS.groq_api_key}"},
        json={
            "model": SETTINGS.groq_model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        },
    )
    if r is None:
        _disabled.add("groq")  # probablemente sin cuota: no insistir en esta ejecución
        return None
    try:
        content = r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError):
        return None
    USAGE["groq"] += 1
    return _extract_json(content)


def _gemini(system: str, user: str) -> dict | None:
    if not SETTINGS.gemini_api_key or "gemini" in _disabled:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{SETTINGS.gemini_model}:generateContent"
    r = http.post(
        url,
        "gemini",
        headers={"x-goog-api-key": SETTINGS.gemini_api_key},
        json={
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
        },
    )
    if r is None:
        _disabled.add("gemini")
        return None
    try:
        parts = r.json()["candidates"][0]["content"]["parts"]
        content = "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError, ValueError):
        return None
    USAGE["gemini"] += 1
    return _extract_json(content)


def ask_json(system: str, user: str) -> tuple[dict | None, str | None]:
    """Devuelve (respuesta, proveedor usado)."""
    for name, fn in (("groq", _groq), ("gemini", _gemini)):
        out = fn(system, user)
        if out is not None:
            return out, name
    USAGE["failed"] += 1
    return None, None


def available() -> bool:
    return bool(SETTINGS.groq_api_key or SETTINGS.gemini_api_key)
