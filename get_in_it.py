"""get in IT (get-in-it.de) – IT-Jobbörse, automatisiert durchsuchbar.

Die Website lädt ihre Trefferliste über eine offene JSON-API (/api/v2/open/job/search,
benötigt nur den Header `X-Requested-With: XMLHttpRequest`); robots.txt sperrt weder
/api noch /jobsuche (nur /jobsuche/feeds/, /extern/, /preview/ u. ä.). Eingefügt
2026-10-05 auf Nutzerwunsch, nachdem eine Portalliste geprüft wurde.

Einschränkungen der API:
  - keine Freitext-/Keyword-Suche (nur Filter nach Berufsfeld/Ort) – daher wird wie bei
    stuttgart-stadt die komplette Liste im Umkreis geladen und lokal per Keyword im
    Titel gefiltert;
  - die Liste enthält kein Veröffentlichungsdatum – das steht nur im JSON-LD
    (`datePosted`) der Detailseite. Um nicht täglich alle ~700 Detailseiten abzurufen,
    werden die Detailseiten in absteigender Job-ID-Reihenfolge (IDs steigen mit dem
    Anlegedatum) geladen, bis mehrere Anzeigen in Folge älter als das Zeitfenster sind.
    Nur so datierte Treffer werden zurückgegeben – undatierte Treffer gibt es hier nie,
    der bekannte "ohne Datum = behalten"-Effekt von _filter_by_age greift also nicht.
    Eine Anzeige, die mit alter ID neu datiert wurde, kann dadurch fehlen.

Liste und Detail-Daten werden pro Lauf zwischengespeichert, damit die vielen
Suchprofile/Keywords eines Laufs die Website nur einmal abfragen.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

BASE = "https://www.get-in-it.de"
API = f"{BASE}/api/v2/open/job/search"
PAGE_SIZE = 39  # maximal von der API akzeptierte Seitengröße
USER_AGENT = "Mozilla/5.0 (compatible; JobAgent/1.0; +personal-use-job-search)"
HEADERS = {"User-Agent": USER_AGENT, "X-Requested-With": "XMLHttpRequest"}
REQUEST_DELAY = 0.3  # Sekunden zwischen Anfragen (höflich bleiben)
STOP_AFTER_OLD = 8  # so viele Anzeigen in Folge älter als das Fenster -> Scan beenden
MAX_PAGES = 40

# get-in-it filtert per Koordinaten. Bei Bedarf weitere Orte ergänzen.
CITY_COORDS = {
    "stuttgart": (48.78232, 9.17702),
    "münchen": (48.13743, 11.57549),
    "frankfurt am main": (50.11092, 8.68213),
}

_list_cache: dict[tuple, list[dict]] = {}
_date_cache: dict[int, str | None] = {}
_description_cache: dict[int, str | None] = {}


def _fetch_list(lat: float, lon: float, radius_km: int) -> list[dict]:
    key = (lat, lon, radius_km)
    if key in _list_cache:
        return _list_cache[key]
    params = {
        "filter[city][radius]": radius_km,
        "filter[city][city][0][lat]": lat,
        "filter[city][city][0][lon]": lon,
        "limit": PAGE_SIZE,
    }
    jobs: dict[int, dict] = {}
    for page in range(MAX_PAGES):
        resp = requests.get(API, params={**params, "start": page * PAGE_SIZE}, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        items = (data.get("items") or {}).get("results") or []
        for item in items:
            jobs.setdefault(item["id"], item)
        if not items or (page + 1) * PAGE_SIZE >= int(data.get("total") or 0):
            break
        time.sleep(REQUEST_DELAY)
    result = sorted(jobs.values(), key=lambda j: j["id"], reverse=True)
    _list_cache[key] = result
    return result


def _fetch_detail(job: dict) -> None:
    """Liest datePosted (+ Beschreibung) aus dem JSON-LD der Detailseite in den Cache."""
    job_id = job["id"]
    if job_id in _date_cache:
        return
    date_posted, description = None, None
    try:
        resp = requests.get(BASE + job["url"], headers={"User-Agent": USER_AGENT}, timeout=20)
        resp.raise_for_status()
        for block in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', resp.text, re.S):
            try:
                ld = json.loads(block)
            except ValueError:
                continue
            if isinstance(ld, dict) and ld.get("@type") == "JobPosting":
                date_posted = (ld.get("datePosted") or "")[:10] or None
                desc_html = ld.get("description") or ""
                description = re.sub(r"<[^>]+>", " ", desc_html)
                description = re.sub(r"\s+", " ", description).strip() or None
                break
    except Exception:
        pass
    _date_cache[job_id] = date_posted
    _description_cache[job_id] = description
    time.sleep(REQUEST_DELAY)


def _recent_jobs(jobs: list[dict], hours_old: int | None) -> list[dict]:
    """Lädt Detailseiten (neueste ID zuerst), bis STOP_AFTER_OLD Anzeigen in Folge
    älter als das Zeitfenster sind; gibt nur Jobs mit bekanntem Datum im Fenster zurück."""
    window_hours = hours_old or 24 * 7
    # Datumsangaben sind tagesgenau (00:00) – ein Tag Puffer, die exakte Prüfung macht
    # anschließend _filter_by_age in job_agent.py für alle Quellen einheitlich.
    cutoff = (datetime.now() - timedelta(hours=window_hours + 24)).date()
    recent, old_streak = [], 0
    for job in jobs:
        _fetch_detail(job)
        date_posted = _date_cache.get(job["id"])
        if date_posted and datetime.strptime(date_posted, "%Y-%m-%d").date() >= cutoff:
            recent.append(job)
            old_streak = 0
        else:
            old_streak += 1
            if old_streak >= STOP_AFTER_OLD:
                break
    return recent


def search(
    keywords: str,
    location: str,
    radius_km: int,
    hours_old: int | None = None,
    company: str | None = None,
    fetch_description: bool = False,
) -> pd.DataFrame:
    coords = CITY_COORDS.get(location.strip().lower())
    if coords is None:
        raise ValueError(f"keine Koordinaten für Ort '{location}' hinterlegt (CITY_COORDS in scrapers/get_in_it.py)")
    jobs = _recent_jobs(_fetch_list(*coords, radius_km), hours_old)

    rows = []
    for job in jobs:
        locations = [loc.get("name") for loc in job.get("locations") or [] if loc.get("name")]
        row = {
            "site": "get-in-it",
            "title": job.get("title"),
            "company": (job.get("company") or {}).get("title"),
            "location": ", ".join(locations[:3]) + (" …" if len(locations) > 3 else ""),
            "job_url": BASE + job["url"],
            "date_posted": _date_cache.get(job["id"]),
        }
        if fetch_description:
            row["description"] = _description_cache.get(job["id"])
        rows.append(row)

    df = pd.DataFrame(rows)
    if not df.empty:
        keyword_terms = [re.escape(k.strip()) for k in keywords.split() if len(k.strip()) > 2]
        if keyword_terms:
            df = df[df["title"].str.contains("|".join(keyword_terms), case=False, na=False, regex=True)]
        if company:
            df = df[df["company"].str.contains(company, case=False, na=False)]
    return df
