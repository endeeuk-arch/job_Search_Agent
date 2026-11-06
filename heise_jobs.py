"""jobs.heise.de – IT-Jobbörse von heise.

Die Suchergebnisseite ist durch einen Bot-Schutz ("BotStopper", JavaScript-Challenge)
geschützt und /search ist in der robots.txt gesperrt. Statt das zu umgehen, baut dieses
Modul nur den fertigen Such-Link zum manuellen Öffnen (Link-only, wie StepStone).
Eingefügt 2026-10-05; URL-Format im Browser geprüft (Freitext + Ort + Umkreis).
"""
from __future__ import annotations

from urllib.parse import urlencode


def build_search_url(keywords: str, location: str, radius_km: int) -> str:
    params = {"q": keywords.strip(), "loc": location.strip(), "r": radius_km}
    return f"https://jobs.heise.de/search?{urlencode(params)}"
