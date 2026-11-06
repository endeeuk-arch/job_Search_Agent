"""Direkte Links zu den offiziellen Karriereportalen bekannter Firmen.

Diese Portale (Mercedes-Benz, Porsche, Allianz, ...) sind JavaScript-gerenderte
Single-Page-Apps (Workday/Phenom/eigene ATS-Systeme) – die Suchergebnisse existieren
nicht im statischen HTML und lassen sich daher nicht zuverlässig mit requests+BeautifulSoup
scrapen (anders als z.B. LinkedIns Guest-Suche). Statt eine fragile/falsche Lösung zu bauen,
gibt dieses Modul nur den verifizierten, funktionierenden Link zum jeweiligen Karriereportal
aus, den man manuell mit den Suchkriterien befüllt.

Alias-Namen (z.B. "benz" für Mercedes-Benz) erleichtern die Eingabe.
"""
from __future__ import annotations

KNOWN_CAREER_SITES: dict[str, str] = {
    "mercedes-benz": "https://jobs.mercedes-benz.com/",
    "benz": "https://jobs.mercedes-benz.com/",
    "mercedes": "https://jobs.mercedes-benz.com/",
    "porsche": "https://jobs.porsche.com/",
    "allianz": "https://careers.allianz.com/de/de",
}


def find_career_link(company: str) -> str | None:
    """Gibt den bekannten Karriereportal-Link zurück, falls die Firma in der Liste ist."""
    return KNOWN_CAREER_SITES.get(company.strip().lower())
