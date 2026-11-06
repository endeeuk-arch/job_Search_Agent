"""Jobvector schützt bereits die eigene robots.txt per Cloudflare-JS-Challenge – selbst
ein einfacher robots.txt-Abruf liefert nur eine "Just a moment..."-Challenge-Seite statt
Text. Das ist aktiver Bot-Schutz; ein Umgehen davon (Challenge lösen, Fingerprinting
austricksen) wird hier bewusst nicht versucht.

Dieses Modul baut deshalb nur die passende Such-URL, die der Nutzer manuell öffnen kann
(Link-only, wie StepStone – kein automatisiertes Auslesen der Ergebnisse, auch nicht per
Browser-Tool).
"""
from __future__ import annotations

from urllib.parse import quote


_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _slug(value: str) -> str:
    return quote(value.strip().lower().translate(_UMLAUTS).replace(" ", "-"))


def build_search_url(keywords: str, location: str) -> str:
    kw_slug = _slug(keywords)
    loc_slug = _slug(location)
    return f"https://www.jobvector.de/jobs/{kw_slug}/{loc_slug}/"
