"""Bundesagentur für Arbeit – Jobsuche über deren öffentlichen JSON-Endpoint.

Kein HTML-Scraping: dies ist der Such-Endpoint, den die eigene Web-/App-Suche der
Bundesagentur selbst verwendet (dokumentiert unter github.com/bundesAPI/jobsuche-api),
mit dem dafür vorgesehenen öffentlichen Client-Key. Offizielle, freie Jobdaten des Bundes.
"""
from __future__ import annotations

import base64

import pandas as pd
import requests

API_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs"
API_KEY = "jobboerse-jobsuche"


def _fetch_description(refnr: str) -> str | None:
    """Lädt die volle Stellenbeschreibung über den Detail-Endpoint (ein Request pro Stelle)."""
    if not refnr:
        return None
    try:
        encoded = base64.b64encode(refnr.encode()).decode()
        resp = requests.get(
            f"https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobdetails/{encoded}",
            headers={"X-API-Key": API_KEY},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("stellenangebotsBeschreibung")
    except Exception:
        return None


def search(
    keywords: str,
    location: str,
    radius_km: int,
    results_wanted: int = 25,
    company: str | None = None,
    fetch_description: bool = False,
) -> pd.DataFrame:
    was = f"{keywords} {company}" if company else keywords
    params = {
        "was": was,
        "wo": location,
        "umkreis": radius_km,
        "size": min(max(results_wanted, 1), 100),
    }
    resp = requests.get(API_URL, headers={"X-API-Key": API_KEY}, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    rows = []
    for item in data.get("ergebnisliste", []):
        loc = (item.get("stellenlokationen") or [{}])[0]
        adresse = loc.get("adresse", {})
        location_str = ", ".join(filter(None, [adresse.get("ort"), adresse.get("land")]))

        refnr = item.get("referenznummer")
        job_url = item.get("externeURL")
        if not job_url and refnr:
            # Die öffentliche Jobdetail-Seite erwartet die rohe Referenznummer in der
            # URL, NICHT base64-kodiert (das war ein Bug: base64-kodierte URLs liefern
            # HTTP 410 "Stellenangebot gibt es nicht", die rohe Referenznummer HTTP 200).
            job_url = f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}"

        rows.append(
            {
                "site": "arbeitsagentur",
                "title": item.get("stellenangebotsTitel"),
                "company": item.get("firma"),
                "location": location_str,
                "job_url": job_url,
                "date_posted": item.get("aktuelleVeroeffentlichungsdatum")
                or item.get("datumErsteVeroeffentlichung"),
                "_refnr": refnr,
            }
        )

    df = pd.DataFrame(rows)

    if fetch_description and not df.empty:
        df["description"] = df["_refnr"].apply(_fetch_description)
    if "_refnr" in df.columns:
        df = df.drop(columns="_refnr")
    if company and not df.empty and "company" in df.columns:
        df = df[df["company"].str.contains(company, case=False, na=False)]
    return df
