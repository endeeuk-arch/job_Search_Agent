"""Stellenangebote der Landeshauptstadt Stuttgart (eigene Karriereseite).

Die Seite ist statisch serverseitig gerendert (robots.txt erlaubt Crawlen) und
unterstützt keine Keyword-Suche per URL-Parameter – daher werden alle aktuellen
Angebote geladen und lokal nach Keyword im Titel gefiltert.
"""
from __future__ import annotations

from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

URL = "https://www.stuttgart.de/rathaus/karriere/fach-und-fuehrungskraefte/aktuelle-stellenangebote"
USER_AGENT = "Mozilla/5.0 (compatible; JobAgent/1.0; +personal-use-job-search)"


def _fetch_description(url: str) -> str | None:
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        body = soup.select_one(".b_rte__bodytext")
        return body.get_text(" ", strip=True) if body else None
    except Exception:
        return None


def search(keywords: str, company: str | None = None, fetch_description: bool = False) -> pd.DataFrame:
    resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    rows = []
    for item in soup.select(".SP-TeaserList__item"):
        link_el = item.select_one("a.SP-Teaser__inner")
        if not link_el:
            continue
        title_el = item.select_one(".SP-Teaser__title, h3, h2") or link_el
        title = title_el.get_text(strip=True)
        date_el = item.select_one(".SP-Kicker span")  # erstes <span> = reines Datum, zweites = Kategorie
        rows.append(
            {
                "site": "stuttgart-stadt",
                "title": title,
                "company": "Landeshauptstadt Stuttgart",
                "location": "Stuttgart",
                "job_url": urljoin(URL, link_el.get("href")),
                "date_posted": date_el.get_text(strip=True) if date_el else None,
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        keyword_terms = [k.strip() for k in keywords.split() if len(k.strip()) > 2]
        if keyword_terms:
            pattern = "|".join(keyword_terms)
            df = df[df["title"].str.contains(pattern, case=False, na=False, regex=True)]
        if company:
            # Stadt Stuttgart ist selbst der Arbeitgeber; ein Firmenfilter ergibt hier keinen Sinn.
            df = df[df["company"].str.contains(company, case=False, na=False)]
        if fetch_description and not df.empty:
            df["description"] = df["job_url"].apply(_fetch_description)
    return df
