"""Generischer Scraper für zusätzliche/eigene Job-Portal-URLs.

Prüft vor jedem Zugriff die robots.txt der Ziel-Domain und bricht ab, wenn der
Pfad für Crawler gesperrt ist. Die Extraktion erfolgt über vom Nutzer angegebene
CSS-Selektoren, da die HTML-Struktur je nach Portal unterschiedlich ist.
"""
from __future__ import annotations

from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import pandas as pd
import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; JobAgent/1.0; +personal-use-job-search)"


def _is_allowed(url: str) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = RobotFileParser()
    try:
        rp.set_url(robots_url)
        rp.read()
    except Exception:
        # robots.txt nicht erreichbar -> vorsichtshalber nicht blockieren,
        # aber auch nicht automatisch erlauben; hier: erlauben, da keine Regel bekannt ist
        return True
    return rp.can_fetch(USER_AGENT, url)


def scrape(
    url: str,
    item_selector: str,
    title_selector: str,
    link_selector: str | None = None,
    company_selector: str | None = None,
    location_selector: str | None = None,
) -> pd.DataFrame:
    """Scraped eine einzelne Suchergebnis-Seite anhand der übergebenen CSS-Selektoren.

    Gibt einen leeren DataFrame zurück (mit Warnung), wenn robots.txt das verbietet.
    """
    if not _is_allowed(url):
        print(f"[generic] Übersprungen – robots.txt verbietet das Crawlen von: {url}")
        return pd.DataFrame()

    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    rows = []
    for item in soup.select(item_selector):
        title_el = item.select_one(title_selector)
        if not title_el:
            continue
        title = title_el.get_text(strip=True)

        link = None
        if link_selector:
            link_el = item.select_one(link_selector)
            href = link_el.get("href") if link_el else None
            link = urljoin(url, href) if href else None
        elif title_el.name == "a":
            href = title_el.get("href")
            link = urljoin(url, href) if href else None

        company = None
        if company_selector:
            company_el = item.select_one(company_selector)
            company = company_el.get_text(strip=True) if company_el else None

        job_location = None
        if location_selector:
            loc_el = item.select_one(location_selector)
            job_location = loc_el.get_text(strip=True) if loc_el else None

        rows.append(
            {
                "site": urlparse(url).netloc,
                "title": title,
                "company": company,
                "location": job_location,
                "job_url": link,
            }
        )

    return pd.DataFrame(rows)
