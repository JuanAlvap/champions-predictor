"""Titulares recientes por equipo desde Google News RSS (gratis, sin clave)."""
from __future__ import annotations

import email.utils
import html
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

from . import http

FEEDS = [
    # (idioma de la consulta, parámetros de región)
    ("en", "hl=en-GB&gl=GB&ceid=GB:en"),
    ("es", "hl=es-419&gl=CO&ceid=CO:es-419"),
]

QUERY_TERMS = {
    "en": "(injury OR injured OR doubt OR ruled out OR suspended OR lineup OR rotation OR press conference)",
    "es": "(lesión OR lesionado OR baja OR duda OR sancionado OR alineación OR convocatoria OR rueda de prensa)",
}

TAG_RE = re.compile(r"<[^>]+>")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(TAG_RE.sub(" ", s or ""))).strip()


def team_headlines(team: str, days: int = 4, limit: int = 25) -> list[dict]:
    """Titulares de los últimos `days` días sobre bajas, dudas y alineaciones del equipo."""
    items: list[dict] = []
    seen: set[str] = set()
    now = time.time()
    for lang, region in FEEDS:
        q = f'"{team}" {QUERY_TERMS[lang]} when:{days}d'
        url = f"https://news.google.com/rss/search?q={quote_plus(q)}&{region}"
        r = http.get(url, "google-news", timeout=20, retries=1)
        if r is None:
            continue
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError:
            continue
        for it in root.iter("item"):
            title = _clean(it.findtext("title", ""))
            if not title:
                continue
            key = title.lower()[:90]
            if key in seen:
                continue
            seen.add(key)
            pub = it.findtext("pubDate", "")
            try:
                ts = email.utils.parsedate_to_datetime(pub).timestamp()
            except (TypeError, ValueError):
                ts = now
            source = it.find("source")
            items.append({
                "title": title,
                "source": source.text if source is not None else "",
                "url": it.findtext("link", ""),
                "ts": ts,
                "hours_ago": round((now - ts) / 3600, 1),
                "lang": lang,
            })
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items[:limit]
