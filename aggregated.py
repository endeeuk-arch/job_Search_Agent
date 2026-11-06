"""Sucht Stellenangebote über die etablierte jobspy-Bibliothek (LinkedIn, Indeed, Glassdoor, ZipRecruiter, Google).

jobspy nutzt jeweils die von den Portalen bereitgestellten Such-/Such-APIs bzw. öffentlich
zugängliche Suchergebnisse und ist die Standardlösung für diesen Zweck im Python-Ökosystem.
"""
from __future__ import annotations

import pandas as pd
from jobspy import scrape_jobs

SUPPORTED_SITES = ["linkedin", "indeed", "glassdoor", "zip_recruiter", "google"]


def search(
    keywords: str,
    location: str,
    radius_km: int,
    sites: list[str],
    results_wanted: int,
    hours_old: int | None,
    remote_only: bool,
    company: str | None = None,
    country: str = "Germany",
    fetch_description: bool = False,
) -> pd.DataFrame:
    """Durchsucht die angegebenen Portale und liefert einen kombinierten DataFrame zurück.

    Ist `company` gesetzt, wird der Firmenname in den Suchbegriff aufgenommen (die Portale
    haben keinen dedizierten Firmenfilter für freien Firmennamen) und die Ergebnisse werden
    zusätzlich clientseitig auf Treffer gefiltert, deren 'company'-Spalte den Namen enthält –
    das erhöht die Trefferpräzision gegenüber reiner Stichwortsuche.

    `fetch_description=True` lädt bei LinkedIn zusätzlich die volle Stellenbeschreibung
    (separater Request pro Treffer, spürbar langsamer) – nötig für die Profil-Bewertung.
    Indeed liefert Beschreibungen bereits standardmäßig mit.
    """
    sites = [s for s in sites if s in SUPPORTED_SITES]
    if not sites:
        return pd.DataFrame()

    # jobspy erwartet die Distanz in Meilen
    distance_miles = max(1, round(radius_km / 1.60934))

    search_term = keywords
    if company:
        search_term = f"{keywords} {company}"
    if remote_only:
        search_term = f"{search_term} remote"

    df = scrape_jobs(
        site_name=sites,
        search_term=search_term,
        location=location,
        distance=distance_miles,
        results_wanted=results_wanted,
        hours_old=hours_old,
        country_indeed=country,
        is_remote=remote_only,
        linkedin_fetch_description=fetch_description,
    )
    if df is None:
        return pd.DataFrame()

    if company and "company" in df.columns:
        df = df[df["company"].str.contains(company, case=False, na=False)]

    return df
