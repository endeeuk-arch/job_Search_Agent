"""StepStone verbietet in seiner robots.txt das Crawlen der Suchergebnis-Seiten
(u.a. /jobs/*?*, /5/ergebnisliste.html, /search-results, /listing).

Statt diese Regeln zu umgehen, baut dieses Modul nur die passende Such-URL, die der
Nutzer manuell öffnen kann (Link-only, kein automatisiertes Auslesen der Ergebnisse).
"""
from __future__ import annotations

from urllib.parse import quote


_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _slug(value: str) -> str:
    return quote(value.strip().lower().translate(_UMLAUTS).replace(" ", "-"))


def build_search_url(keywords: str, location: str, radius_km: int) -> str:
    kw_slug = _slug(keywords)
    loc_slug = _slug(location)
    return f"https://www.stepstone.de/jobs/{kw_slug}/in-{loc_slug}?radius={radius_km}"
