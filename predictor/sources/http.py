"""Cliente HTTP con reintentos y un registro de lo que falló (para mostrarlo en la app)."""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger("predictor")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "champions-predictor/0.1 (+github.com)"})

STATUS: dict[str, dict] = {}


def mark(source: str, ok: bool, detail: str = "") -> None:
    entry = STATUS.setdefault(source, {"ok": 0, "errors": 0, "last_error": ""})
    if ok:
        entry["ok"] += 1
    else:
        entry["errors"] += 1
        entry["last_error"] = detail[:300]


def get(url: str, source: str, *, params=None, headers=None, timeout=20, retries=2):
    last = None
    for attempt in range(retries + 1):
        try:
            r = SESSION.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 429:
                time.sleep(2 + attempt * 3)
                last = f"429 rate limit en {url}"
                continue
            r.raise_for_status()
            mark(source, True)
            return r
        except requests.RequestException as e:  # noqa: PERF203
            last = f"{type(e).__name__}: {e}"
            time.sleep(1 + attempt)
    log.warning("%s falló: %s", source, last)
    mark(source, False, last or "error")
    return None


def post(url: str, source: str, *, json=None, headers=None, params=None, timeout=60, retries=1):
    last = None
    for attempt in range(retries + 1):
        try:
            r = SESSION.post(url, json=json, headers=headers, params=params, timeout=timeout)
            if r.status_code == 429:
                last = "429 rate limit"
                time.sleep(3 + attempt * 5)
                continue
            r.raise_for_status()
            mark(source, True)
            return r
        except requests.RequestException as e:  # noqa: PERF203
            body = ""
            if getattr(e, "response", None) is not None:
                body = e.response.text[:200]
            last = f"{type(e).__name__}: {e} {body}"
            time.sleep(1 + attempt)
    log.warning("%s falló: %s", source, last)
    mark(source, False, last or "error")
    return None
