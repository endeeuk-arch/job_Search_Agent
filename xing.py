"""XING erlaubt in seiner robots.txt KI-Agenten (u.a. ClaudeBot) explizit das Crawlen
von /jobs/search/ und /jobs/search?* (im Gegensatz zum generischen User-agent "*",
für den genau diese Pfade verboten sind). Die Suchergebnisseite ist allerdings eine
JS-Single-Page-App ohne serverseitig gerenderte Job-Daten – ein reiner HTTP-Request
(requests/jobspy/WebFetch) liefert nur die leere App-Hülle.

Deshalb baut dieses Modul nur die Such-URL. Das tatsächliche Auslesen der Treffer
übernimmt der ausführende Agent selbst per Browser-Tool (rendert JS, keine
robots.txt-Verletzung, da /jobs/search/ für KI-Agenten erlaubt ist) – siehe SKILL.md
des CareerRadar-Tasks. Kein eigener requests/jobspy-Scraper hier, weil das robots.txt
für den generischen User-agent explizit verbietet, /jobs/search/ per einfachem
HTTP-Client abzufragen.
"""
from __future__ import annotations

from urllib.parse import quote


def build_search_url(keywords: str, location: str, radius_km: int) -> str:
    return (
        "https://www.xing.com/jobs/search"
        f"?keywords={quote(keywords.strip())}&location={quote(location.strip())}&radius={radius_km}"
    )
