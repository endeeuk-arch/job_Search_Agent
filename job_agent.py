#!/usr/bin/env python3
"""Job-Such-Agent: durchsucht mehrere Jobportale nach konfigurierbaren Kriterien.

Standardmäßig: LinkedIn + Indeed (über jobspy) rund um Stuttgart im Radius von 50 km.
StepStone wird nicht gescraped (robots.txt verbietet das) – stattdessen wird nur die
passende Such-URL ausgegeben. Zusätzliche eigene Portale können per --custom-url mit
CSS-Selektoren angebunden werden.

Kriterien können statt per CLI auch aus einer YAML/JSON-Datei geladen werden (siehe
config.example.yaml) – praktisch für wiederkehrende Suchen oder mehrere gespeicherte
Suchprofile. CLI-Flags überschreiben dabei einzelne Werte aus der Config-Datei.

Beispiele:
    python job_agent.py --keywords "Python Entwickler"
    python job_agent.py --config config.example.yaml
    python job_agent.py --config config.example.yaml --results-wanted 10
    python job_agent.py --keywords "DevOps" --custom-url \\
        "https://example-jobs.de/suche?q=devops" \\
        --custom-item-selector ".job-card" --custom-title-selector ".job-title"
"""
from __future__ import annotations

import argparse
import html as html_lib
import multiprocessing
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

import application_generator
import config_loader
import profile_matcher
from scrapers import aggregated, arbeitsagentur, company_links, generic, get_in_it, heise_jobs, jobvector, stepstone, stuttgart_stadt

EXTRA_SITES = ["arbeitsagentur", "stuttgart-stadt", "get-in-it"]
ALL_SITES = aggregated.SUPPORTED_SITES + EXTRA_SITES

# Maximale Laufzeit pro Quelle und Suchprofil. jobspy wiederholt LinkedIn-429er und befolgt
# dabei Retry-After ohne Obergrenze – am 23./24.10.2026 hingen dadurch zwei Läufe 6–8 h.
SOURCE_TIMEOUT_SECONDS = 300

# SOURCE_TIMEOUT_SECONDS misst nur wache Zeit – schläft der Mac (Deckel zu), steht die Uhr
# und der Lauf "läuft" stundenlang (26.10.2026: 06:24–16:50 ohne Ergebnis). Deshalb
# zusätzlich: Gesamtbudget in echter Uhrzeit, danach restliche Profile überspringen und
# mit dem Vorhandenen abschließen; und Warnung, sobald ein Profil verschlafen wurde.
MAX_RUN_SECONDS = 90 * 60  # 31.10.2026: 60 -> 90 min, da München + Frankfurt dazukamen (50 Profile)
SLEEP_WARN_SECONDS = 120


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _call_with_timeout(fn, **kwargs):
    """Führt eine Scraper-Funktion in einem eigenen Prozess aus und bricht ihn nach
    SOURCE_TIMEOUT_SECONDS hart ab (ein Thread ließe sich bei hängendem Netzwerk-I/O nicht
    beenden). Wirft TimeoutError, den die Aufrufer wie jeden anderen Quellen-Fehler behandeln."""
    with multiprocessing.get_context("spawn").Pool(1) as pool:
        try:
            return pool.apply_async(fn, kwds=kwargs).get(SOURCE_TIMEOUT_SECONDS)
        except multiprocessing.TimeoutError:
            raise TimeoutError(f"TIMEOUT nach {SOURCE_TIMEOUT_SECONDS // 60} min") from None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sucht Stellenangebote über mehrere Jobportale.")
    p.add_argument("--config", default=None, help="Pfad zu einer YAML/JSON-Datei mit Suchkriterien")
    p.add_argument(
        "--company",
        nargs="+",
        default=None,
        help="Gezielt nach einer oder mehreren Firmen filtern, z.B. 'Mercedes-Benz' 'Porsche' 'Allianz' "
        "(wird in die Portalsuche aufgenommen + Ergebnisse werden auf die Firma gefiltert; bei mehreren "
        "Firmen entsteht ein eigener Suchlauf pro Firma; bei bekannten Firmen wird zusätzlich der Link "
        "zum Karriereportal ausgegeben)",
    )
    p.add_argument(
        "--keywords",
        nargs="+",
        default=None,
        help="Suchbegriffe, z.B. 'Python Entwickler'. Mehrfach angebbar für mehrere getrennte Suchen, "
        "z.B. --keywords \"Python Entwickler\" \"Backend Developer\"",
    )
    p.add_argument("--location", default=None, help="Standort (Default: Stuttgart)")
    p.add_argument("--radius", type=int, default=None, help="Umkreis in km (Default: 50)")
    p.add_argument(
        "--sites",
        nargs="+",
        default=None,
        choices=ALL_SITES,
        help="Portale (Default: linkedin indeed). 'arbeitsagentur' nutzt die offizielle "
        "Bundesagentur-für-Arbeit-API, 'stuttgart-stadt' die Karriereseite der Stadt Stuttgart, "
        "'get-in-it' die offene Such-API von get-in-it.de.",
    )
    p.add_argument("--results-wanted", type=int, default=None, help="Ergebnisse pro Portal")
    p.add_argument("--hours-old", type=int, default=None, help="Nur Anzeigen der letzten X Stunden")
    p.add_argument("--remote", action="store_true", default=None, help="Nur Remote-Stellen")
    p.add_argument(
        "--no-stepstone-link",
        action="store_true",
        default=None,
        help="StepStone-Such-Link nicht mit ausgeben",
    )
    p.add_argument(
        "--custom-url",
        action="append",
        default=[],
        help="Zusätzliche eigene Such-URL (mehrfach angebbar); benötigt --custom-*-selector",
    )
    p.add_argument("--custom-item-selector", default=None, help="CSS-Selektor für ein Job-Listenelement")
    p.add_argument("--custom-title-selector", default=None, help="CSS-Selektor für den Jobtitel innerhalb des Elements")
    p.add_argument("--custom-link-selector", default=None, help="CSS-Selektor für den Link (optional)")
    p.add_argument("--custom-company-selector", default=None, help="CSS-Selektor für die Firma (optional)")
    p.add_argument("--custom-location-selector", default=None, help="CSS-Selektor für den Standort (optional)")
    p.add_argument("--output", default=None, help="Ausgabedatei (.csv oder .json)")
    p.add_argument(
        "--cv-path",
        default=None,
        help="Pfad zu einem Lebenslauf (PDF/DOCX). Wenn gesetzt, wird jede Stelle per Claude API "
        "gegen den Lebenslauf bewertet (Spalte 'profile_match': Strong/Sehr gut/Gut/Kein Match). "
        "Braucht ANTHROPIC_API_KEY als Umgebungsvariable (selbst setzen, nie hier eintragen) und "
        "verursacht API-Kosten + spürbar längere Laufzeit (Description-Fetching + ein Call pro Stelle).",
    )
    p.add_argument(
        "--generate-applications",
        action="store_true",
        default=None,
        help="Für Stellen mit Match 'Sehr gut'/'Strong' automatisch ein Anschreiben generieren und mit "
        "dem CV zu einer Bewerbungsunterlagen-PDF zusammenführen (braucht --cv-path/cv_path). "
        "Standardmäßig AUS (niedrige Priorität, kostet zusätzliche API-Anfragen mit Sonnet – teurer "
        "als die reine Bewertung mit Haiku). Explizit anfordern, wenn gewünscht.",
    )
    p.add_argument(
        "--exclude",
        nargs="+",
        default=None,
        help="Treffer ausschließen, deren Titel einen dieser Begriffe enthält (Groß-/Kleinschreibung egal), "
        "z.B. --exclude \"Duales Studium\" \"Praktikum\"",
    )
    p.add_argument(
        "--exclude-companies",
        nargs="+",
        default=None,
        help="Treffer ausschließen, deren Firmenname einen dieser Begriffe enthält (Groß-/Kleinschreibung "
        "egal), z.B. --exclude-companies \"ExampleCorp\"",
    )
    return p.parse_args()


def _expand_keyword_lists(profiles: list[dict]) -> list[dict]:
    """Expandiert Profile, deren 'keywords' eine Liste ist, in mehrere Einzelprofile
    (gleiche restlichen Kriterien, ein Suchlauf pro Keyword). Ergebnisse werden über
    die Spalte 'search_profile' unterscheidbar."""
    expanded = []
    for profile in profiles:
        kw = profile.get("keywords")
        if isinstance(kw, list):
            base_name = profile.get("name")
            for keyword in kw:
                sub = {**profile, "keywords": keyword}
                sub["name"] = f"{base_name}-{keyword}" if base_name else keyword
                expanded.append(sub)
        else:
            expanded.append(profile)
    return expanded


def _expand_companies(profiles: list[dict]) -> list[dict]:
    """Expandiert Profile mit einer 'companies'-Liste in mehrere Einzelprofile
    (gleiche Keywords/Standort/Radius/etc., ein Suchlauf pro Firma). Wird mit einer
    bereits per Keyword expandierten Liste kombiniert, entsteht das Kreuzprodukt
    aus Keywords x Firmen."""
    expanded = []
    for profile in profiles:
        companies = profile.get("companies")
        if isinstance(companies, list):
            base_name = profile.get("name")
            for comp in companies:
                sub = {k: v for k, v in profile.items() if k != "companies"}
                sub["company"] = comp
                sub["name"] = f"{base_name}-{comp}" if base_name else comp
                expanded.append(sub)
        else:
            expanded.append(profile)
    return expanded


def build_profiles(args: argparse.Namespace) -> list[dict]:
    """Kombiniert Config-Datei (falls angegeben) und CLI-Flags zu einer Liste von Suchprofilen.

    CLI-Flags überschreiben dabei gleichnamige Werte aus der Config-Datei, gelten aber
    für alle Profile aus der Datei gleichermaßen. Werden mehrere --keywords angegeben
    (oder eine 'keywords'-Liste in der Config), entsteht pro Keyword ein eigener Suchlauf.
    """
    cli_keywords = args.keywords
    if cli_keywords is not None and len(cli_keywords) == 1:
        cli_keywords = cli_keywords[0]  # einzelnes Keyword: wie ein normaler String behandeln

    cli_overrides = {
        k: v
        for k, v in {
            "keywords": cli_keywords,
            "location": args.location,
            "radius": args.radius,
            "sites": args.sites,
            "results_wanted": args.results_wanted,
            "hours_old": args.hours_old,
            "remote": args.remote,
            "no_stepstone_link": args.no_stepstone_link,
            "exclude_keywords": args.exclude,
            "exclude_companies": args.exclude_companies,
            "cv_path": args.cv_path,
            "generate_applications": args.generate_applications,
            # Wird explizit per CLI übergeben, überschreibt es einheitlich alle Profile dieses
            # Laufs (auch bei --keywords/--company-Expansion) – main() gruppiert ohnehin nach
            # Output und speichert erst am Ende einmal pro Gruppe, das Overwrite-Problem von
            # früher betrifft das nicht mehr.
            "output": args.output,
        }.items()
        if v is not None
    }

    if args.company is not None:
        if len(args.company) == 1:
            cli_overrides["company"] = args.company[0]
        else:
            cli_overrides["companies"] = args.company

    if args.custom_url:
        if not (args.custom_item_selector and args.custom_title_selector):
            print("Warnung: --custom-url benötigt mindestens --custom-item-selector und --custom-title-selector. Übersprungen.")
        else:
            cli_overrides["custom_urls"] = [
                {
                    "url": url,
                    "item_selector": args.custom_item_selector,
                    "title_selector": args.custom_title_selector,
                    "link_selector": args.custom_link_selector,
                    "company_selector": args.custom_company_selector,
                    "location_selector": args.custom_location_selector,
                }
                for url in args.custom_url
            ]

    if args.config:
        profiles = config_loader.load_profiles(args.config)
        for profile in profiles:
            profile.update(cli_overrides)
        return _expand_companies(_expand_keyword_lists(profiles))

    if "keywords" not in cli_overrides:
        raise SystemExit("Fehler: --keywords ist erforderlich, wenn keine --config Datei angegeben ist.")

    profile = {**config_loader.DEFAULTS, **cli_overrides}
    return _expand_companies(_expand_keyword_lists([profile]))


def _filter_by_age(df: pd.DataFrame, hours_old: int | None) -> pd.DataFrame:
    """Entfernt Treffer, deren date_posted älter als hours_old ist. jobspy-Portale (LinkedIn/
    Indeed/...) filtern das bereits selbst serverseitig, aber arbeitsagentur/stuttgart-stadt
    nicht – dieser Filter sorgt für einheitliches Verhalten über alle Quellen hinweg. Einträge
    ohne (parsebares) Datum werden sicherheitshalber behalten statt verworfen."""
    if not hours_old or df.empty or "date_posted" not in df.columns:
        return df
    dates = pd.to_datetime(df["date_posted"], errors="coerce", dayfirst=True)
    cutoff = pd.Timestamp.now() - pd.Timedelta(hours=hours_old)
    keep = dates.isna() | (dates >= cutoff)
    return df[keep]


def _filter_excluded(df: pd.DataFrame, exclude_keywords: list[str]) -> pd.DataFrame:
    """Entfernt Treffer, deren Titel einen der exclude_keywords enthält (case-insensitive)."""
    if not exclude_keywords or df.empty or "title" not in df.columns:
        return df
    pattern = "|".join(re.escape(kw) for kw in exclude_keywords)
    mask = df["title"].str.contains(pattern, case=False, na=False, regex=True)
    return df[~mask]


def _filter_excluded_companies(df: pd.DataFrame, exclude_companies: list[str]) -> pd.DataFrame:
    """Entfernt Treffer, deren Firmenname einen der exclude_companies enthält (case-insensitive).

    Für Firmen, bei denen aus persönlichen Gründen (z.B. bestehende Bewerbungssperre)
    keine Ergebnisse erwünscht sind, unabhängig vom Stellentitel.
    """
    if not exclude_companies or df.empty or "company" not in df.columns:
        return df
    pattern = "|".join(re.escape(c) for c in exclude_companies)
    mask = df["company"].str.contains(pattern, case=False, na=False, regex=True)
    return df[~mask]


_LEGAL_FORM_WORDS = {
    "gmbh", "co", "kg", "ag", "se", "inc", "corp", "corporation", "ltd", "llc",
    "group", "holding", "gruppe", "deutschland", "germany", "europe",
}


def _normalize_company(value: str) -> str:
    """Normalisiert einen Firmennamen für den Abgleich gegen bewerbungen_import.csv:
    kleingeschrieben, Rechtsform-/Zusatzwörter (GmbH, AG, & Co. KG, ...) entfernt,
    Satzzeichen entfernt, Whitespace vereinheitlicht."""
    text = re.sub(r"[^a-zA-Z0-9äöüß\s]", " ", str(value).lower())
    words = [w for w in text.split() if w not in _LEGAL_FORM_WORDS]
    return " ".join(words).strip()


def _normalize_title(value: str) -> str:
    """Normalisiert einen Stellentitel: kleingeschrieben, Klammerinhalte (m/w/d)-Tags,
    Referenznummern etc. entfernt, Satzzeichen entfernt, Whitespace vereinheitlicht."""
    text = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", str(value).lower())
    text = re.sub(r"[^a-zA-Z0-9äöüß\s]", " ", text)
    return " ".join(text.split()).strip()


def _load_applied_jobs(csv_path: str) -> set[tuple[str, str]]:
    """Liest bewerbungen_import.csv (Firma, Job-Titel, ...) und gibt normalisierte
    (Firma, Titel)-Paare zurück, gegen die neue Suchtreffer abgeglichen werden, damit
    bereits beworbene Stellen nicht erneut gelistet/bewertet werden."""
    path = Path(csv_path)
    if not path.exists():
        return set()
    try:
        applied = pd.read_csv(path)
    except Exception as e:
        print(f"WARNUNG: {csv_path} konnte nicht gelesen werden ({e}) – Abgleich mit bereits beworbenen Stellen übersprungen")
        return set()
    if "Firma" not in applied.columns or "Job-Titel" not in applied.columns:
        return set()
    pairs = set()
    for _, row in applied.iterrows():
        pairs.add((_normalize_company(row["Firma"]), _normalize_title(row["Job-Titel"])))
    return pairs


def _filter_already_applied(df: pd.DataFrame, applied: set[tuple[str, str]]) -> pd.DataFrame:
    """Entfernt Treffer, die (Firma, Titel)-normalisiert bereits in bewerbungen_import.csv
    stehen – bereits beworbene Stellen werden ignoriert statt erneut bewertet/gelistet."""
    if not applied or df.empty or "company" not in df.columns or "title" not in df.columns:
        return df
    keys = df.apply(
        lambda r: (_normalize_company(r.get("company", "")), _normalize_title(r.get("title", ""))),
        axis=1,
    )
    mask = keys.isin(applied)
    return df[~mask]


def run_profile(profile: dict) -> tuple[pd.DataFrame, str | None]:
    name = profile.get("name")
    label = f" [{name}]" if name else ""

    frames: list[pd.DataFrame] = []

    company = profile.get("company")
    company_suffix = f" bei {company}" if company else ""
    fetch_description = bool(profile.get("cv_path"))
    print(f"[{_ts()}] Durchsuche {', '.join(profile['sites'])} für '{profile['keywords']}'{company_suffix} in {profile['location']} (+{profile['radius']} km){label}...")
    try:
        df_agg = _call_with_timeout(
            aggregated.search,
            keywords=profile["keywords"],
            location=profile["location"],
            radius_km=profile["radius"],
            sites=profile["sites"],
            results_wanted=profile["results_wanted"],
            hours_old=profile["hours_old"],
            remote_only=profile["remote"],
            company=company,
            fetch_description=fetch_description,
        )
    except Exception as e:
        print(f"  WARNUNG: linkedin/indeed/... fehlgeschlagen ({e}) – übersprungen, Rest läuft weiter")
        df_agg = pd.DataFrame()
    if not df_agg.empty:
        frames.append(df_agg)
    print(f"  -> {len(df_agg)} Treffer")

    if "arbeitsagentur" in profile["sites"]:
        print(f"[{_ts()}] Durchsuche arbeitsagentur (offizielle API) ...")
        try:
            df_ba = _call_with_timeout(
                arbeitsagentur.search,
                keywords=profile["keywords"],
                location=profile["location"],
                radius_km=profile["radius"],
                results_wanted=profile["results_wanted"],
                company=company,
                fetch_description=fetch_description,
            )
        except Exception as e:
            print(f"  WARNUNG: arbeitsagentur fehlgeschlagen ({e}) – übersprungen")
            df_ba = pd.DataFrame()
        if not df_ba.empty:
            frames.append(df_ba)
        print(f"  -> {len(df_ba)} Treffer")

    if "stuttgart-stadt" in profile["sites"]:
        print(f"[{_ts()}] Durchsuche stuttgart-stadt (Karriereseite der Landeshauptstadt Stuttgart) ...")
        try:
            df_stgt = _call_with_timeout(
                stuttgart_stadt.search,
                keywords=profile["keywords"], company=company, fetch_description=fetch_description
            )
        except Exception as e:
            print(f"  WARNUNG: stuttgart-stadt fehlgeschlagen ({e}) – übersprungen")
            df_stgt = pd.DataFrame()
        if not df_stgt.empty:
            frames.append(df_stgt)
        print(f"  -> {len(df_stgt)} Treffer")

    if "get-in-it" in profile["sites"]:
        print(f"[{_ts()}] Durchsuche get-in-it (offene JSON-API, Datum aus Detailseite) ...")
        try:
            df_gii = _call_with_timeout(
                get_in_it.search,
                keywords=profile["keywords"],
                location=profile["location"],
                radius_km=profile["radius"],
                hours_old=profile["hours_old"],
                company=company,
                fetch_description=fetch_description,
            )
        except Exception as e:
            print(f"  WARNUNG: get-in-it fehlgeschlagen ({e}) – übersprungen")
            df_gii = pd.DataFrame()
        if not df_gii.empty:
            frames.append(df_gii)
        print(f"  -> {len(df_gii)} Treffer")

    if company:
        career_link = company_links.find_career_link(company)
        if career_link:
            print(f"  Karriereportal von {company} (JS-basiert, kein Scraping möglich – manuell durchsuchen): {career_link}")

    for custom in profile.get("custom_urls", []):
        print(f"Durchsuche eigene URL: {custom['url']} ...")
        try:
            df_custom = generic.scrape(
                url=custom["url"],
                item_selector=custom["item_selector"],
                title_selector=custom["title_selector"],
                link_selector=custom.get("link_selector"),
                company_selector=custom.get("company_selector"),
                location_selector=custom.get("location_selector"),
            )
        except Exception as e:
            print(f"  WARNUNG: eigene URL {custom['url']} fehlgeschlagen ({e}) – übersprungen")
            df_custom = pd.DataFrame()
        if not df_custom.empty:
            frames.append(df_custom)
        print(f"  -> {len(df_custom)} Treffer")

    if frames:
        result = pd.concat(frames, ignore_index=True, sort=False)
        dedup_cols = [c for c in ["job_url", "title", "company"] if c in result.columns]
        if dedup_cols:
            result = result.drop_duplicates(subset=dedup_cols)
    else:
        result = pd.DataFrame()

    before_age = len(result)
    result = _filter_by_age(result, profile.get("hours_old"))
    if profile.get("hours_old") and before_age != len(result):
        print(f"  -> {before_age - len(result)} Treffer älter als {profile['hours_old']}h entfernt (arbeitsagentur/stuttgart-stadt filtern nicht selbst)")

    exclude_keywords = profile.get("exclude_keywords") or []
    before = len(result)
    result = _filter_excluded(result, exclude_keywords)
    if exclude_keywords and before != len(result):
        print(f"  -> {before - len(result)} Treffer wegen Ausschluss-Begriffen {exclude_keywords} entfernt")

    exclude_companies = profile.get("exclude_companies") or []
    before = len(result)
    result = _filter_excluded_companies(result, exclude_companies)
    if exclude_companies and before != len(result):
        print(f"  -> {before - len(result)} Treffer wegen ausgeschlossener Firmen {exclude_companies} entfernt")

    if name:
        result["search_profile"] = name

    return result, build_manual_links(profile, verbose=True)


def build_manual_links(profile: dict, verbose: bool = False) -> dict[str, list[str]]:
    """Manuelle Such-Links (StepStone/Jobvector/heise) für den Profil-Standort plus alle
    Städte aus `manual_link_extra_locations` (z.B. München, Frankfurt am Main)."""
    company = profile.get("company")
    manual_keywords = f"{profile['keywords']} {company}" if company else profile["keywords"]
    locations = [profile["location"]]
    remote_only = str(profile["location"]).strip().lower() in ("germany", "deutschland")
    if not remote_only:
        for extra in profile.get("manual_link_extra_locations") or []:
            if extra not in locations:
                locations.append(extra)

    manual_links: dict[str, list[str]] = {}
    for loc in locations:
        if not profile.get("no_stepstone_link"):
            url = stepstone.build_search_url(manual_keywords, loc, profile["radius"])
            manual_links.setdefault("stepstone", []).append(url)
            if verbose:
                print(f"StepStone (manuell prüfen, kein automatisches Scraping): {url}")
        if not profile.get("no_jobvector_link"):
            url = jobvector.build_search_url(manual_keywords, loc)
            manual_links.setdefault("jobvector", []).append(url)
            if verbose:
                print(f"Jobvector (manuell prüfen, Cloudflare-geschützt – kein automatisches Auslesen): {url}")
        if not profile.get("no_heise_link"):
            url = heise_jobs.build_search_url(manual_keywords, loc, profile["radius"])
            manual_links.setdefault("heise", []).append(url)
            if verbose:
                print(f"heise jobs (manuell prüfen, Bot-Schutz – kein automatisches Auslesen): {url}")
    return manual_links


HTML_COLUMNS = ["profile_match", "search_profile", "site", "title", "company", "location", "date_posted"]
MATCH_CSS_CLASS = {
    "Strong": "match-strong",
    "Sehr gut": "match-very-good",
    "Gut": "match-good",
    "Kein Match": "match-none",
}


def _to_html(df: pd.DataFrame, out: Path, manual_links: dict[str, list[str]] | None = None,
             note: str | None = None) -> None:
    """Schreibt die Ergebnisse als kompakte HTML-Tabelle, bei der der Jobtitel direkt
    zum Stellenangebot verlinkt. Beschränkt sich auf die relevanten Spalten (jobspy
    liefert ~30 meist leere Rohspalten wie Gehalt/Beschreibung, die hier nicht nützen).
    Alle Zellinhalte werden manuell escaped, da die Daten von Drittportalen stammen
    (nicht vertrauenswürdig für rohes HTML)."""
    columns = [c for c in HTML_COLUMNS if c in df.columns]

    def esc(value) -> str:
        if pd.isna(value):
            return ""
        return html_lib.escape(str(value))

    header_html = "".join(f"<th>{esc(c)}</th>" for c in columns)

    rows_html = []
    for _, row in df.iterrows():
        cells = []
        for col in columns:
            if col == "title" and "job_url" in df.columns and pd.notna(row.get("job_url")):
                url = html_lib.escape(str(row["job_url"]), quote=True)
                cells.append(f'<td><a href="{url}" target="_blank" rel="noopener noreferrer">{esc(row[col])}</a></td>')
            elif col == "profile_match":
                css_class = MATCH_CSS_CLASS.get(row.get(col), "")
                cells.append(f'<td class="{css_class}">{esc(row[col])}</td>')
            else:
                cells.append(f"<td>{esc(row[col])}</td>")
        rows_html.append(f"<tr>{''.join(cells)}</tr>")

    def _manual_section(title: str, note: str, links: list[str] | None) -> str:
        if not links:
            return ""
        unique_links = list(dict.fromkeys(links))  # Reihenfolge erhalten, Duplikate raus
        items = "".join(
            f'<li><a href="{html_lib.escape(link, quote=True)}" target="_blank" rel="noopener noreferrer">{esc(link)}</a></li>'
            for link in unique_links
        )
        return f"""
<h2>{html_lib.escape(title)}</h2>
<p class="meta">{html_lib.escape(note)}</p>
<ul>{items}</ul>
"""

    manual_links = manual_links or {}
    stepstone_section = _manual_section(
        "StepStone (manuell prüfen)",
        "StepStones robots.txt verbietet automatisiertes Scrapen der Suchergebnisse – hier nur die fertigen Such-Links zum manuellen Öffnen:",
        manual_links.get("stepstone"),
    )
    jobvector_section = _manual_section(
        "Jobvector (manuell prüfen)",
        "Jobvector schützt schon die robots.txt per Cloudflare-Challenge – kein automatisiertes Auslesen, auch nicht per Browser-Tool. Nur die fertigen Such-Links zum manuellen Öffnen:",
        manual_links.get("jobvector"),
    )
    heise_section = _manual_section(
        "heise jobs (manuell prüfen)",
        "jobs.heise.de schützt die Suchergebnisse per Bot-Schutz (JavaScript-Challenge) und sperrt /search in der robots.txt – nur die fertigen Such-Links zum manuellen Öffnen:",
        manual_links.get("heise"),
    )

    html_doc = f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>Job-Suchergebnisse</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600&family=Public+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
  :root {{ color-scheme: dark; }}
  body {{
    font-family: "Public Sans", -apple-system, "Segoe UI", sans-serif;
    margin: 0; padding: 2rem; color: #E7E9EE; background: #0D0F13;
  }}
  h1 {{ font-family: "Fraunces", Georgia, serif; font-weight: 600; font-size: 1.6rem; margin: 0 0 4px; }}
  h2 {{ font-family: "Fraunces", Georgia, serif; font-weight: 600; font-size: 1.1rem; margin-top: 2rem; }}
  .meta {{ font-family: "IBM Plex Mono", monospace; color: #99A1B0; margin-bottom: 1.2rem; font-size: 0.85rem; }}
  .table-scroll {{ overflow-x: auto; border: 1px solid #262B33; border-radius: 10px; }}
  table {{ border-collapse: collapse; width: 100%; min-width: 860px; font-size: 0.9rem; background: #15181D; }}
  th, td {{ border-bottom: 1px solid #262B33; padding: 9px 12px; text-align: left; vertical-align: top; color: #E7E9EE; }}
  th {{
    font-family: "IBM Plex Mono", monospace; font-size: 0.72rem; letter-spacing: 0.06em;
    text-transform: uppercase; color: #99A1B0; background: #1B1F26; position: sticky; top: 0;
  }}
  tr:nth-child(even) td {{ background: #181B21; }}
  tr:hover td {{ background: rgba(123,127,245,0.1); }}
  a {{ color: #E7E9EE; font-weight: 600; text-decoration: none; border-bottom: 1px solid transparent; }}
  a:hover {{ color: #C3C5FB; border-bottom-color: currentColor; }}
  li a {{ font-weight: 400; color: #C3C5FB; }}
  li {{ margin-bottom: 4px; word-break: break-all; color: #99A1B0; }}
  .match-strong {{ color: #34D399; font-weight: 600; }}
  .match-very-good {{ color: #FBBF24; font-weight: 600; }}
  .match-good {{ color: #9AA3B5; }}
  .match-none {{ color: #5A6070; }}
</style>
</head>
<body>
<h1>Job-Suchergebnisse</h1>
<p class="meta">{len(df)} Treffer &middot; erzeugt am {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
{f'<p class="meta"><strong>{html_lib.escape(note)}</strong></p>' if note else ""}
<div class="table-scroll">
<table>
<thead><tr>{header_html}</tr></thead>
<tbody>
{"".join(rows_html)}
</tbody>
</table>
</div>
{stepstone_section}
{jobvector_section}
{heise_section}
</body>
</html>
"""
    out.write_text(html_doc, encoding="utf-8")


def _sort_by_date_posted(df: pd.DataFrame) -> pd.DataFrame:
    """Sortiert nach 'date_posted' absteigend (neueste zuerst); Einträge ohne Datum ans Ende."""
    if "date_posted" not in df.columns or df.empty:
        return df
    dates = pd.to_datetime(df["date_posted"], errors="coerce")
    return df.assign(_sort_date=dates).sort_values(
        "_sort_date", ascending=False, na_position="last"
    ).drop(columns="_sort_date").reset_index(drop=True)


def _resolve_output_path(output_path: str) -> str:
    """Ersetzt Platzhalter im Dateinamen: {date} = heutiges Datum als TTMM."""
    return output_path.replace("{date}", datetime.now().strftime("%d%m"))


def save(df: pd.DataFrame, output_path: str, manual_links: dict[str, list[str]] | None = None,
         note: str | None = None, label: str = "Ergebnisse gespeichert unter") -> None:
    out = Path(_resolve_output_path(output_path))
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".json":
        df.to_json(out, orient="records", force_ascii=False, indent=2)
    elif out.suffix.lower() in (".html", ".htm"):
        _to_html(df, out, manual_links, note)
    else:
        df.to_csv(out, index=False)
    print(f"{label}: {out.resolve()}")


def _save_partial(output_path: str, frames: list[pd.DataFrame], manual_links: dict[str, list[str]],
                  applied_csv: str | None) -> None:
    """Schreibt nach jedem Profil einen Zwischenstand in die Ergebnisdatei, damit bei
    Abbruch/Absturz nicht alle bisherigen Treffer verloren gehen. Der finale Lauf-Abschluss
    in main() überschreibt die Datei danach regulär."""
    combined = pd.concat(frames, ignore_index=True, sort=False)
    dedup_cols = [c for c in ["job_url", "title", "company"] if c in combined.columns]
    if dedup_cols and not combined.empty:
        combined = combined.drop_duplicates(subset=dedup_cols)
    if applied_csv:
        combined = _filter_already_applied(combined, _load_applied_jobs(applied_csv))
    combined = _sort_by_date_posted(combined.assign(profile_match="Unbewertet"))
    save(combined, output_path, manual_links,
         note="ZWISCHENSTAND – Lauf noch nicht abgeschlossen bzw. abgebrochen.",
         label="  Zwischenstand gespeichert unter")


def _warn_if_slept(wall_start: float, mono_start: float) -> float:
    """Vergleicht verstrichene Uhrzeit mit verstrichener Wachzeit; die Differenz ist die Zeit,
    die der Rechner geschlafen hat. Gibt die Schlafdauer in Sekunden zurück."""
    slept = (time.time() - wall_start) - (time.monotonic() - mono_start)
    if slept > SLEEP_WARN_SECONDS:
        print(f"  WARNUNG: Rechner hat während dieses Profils ca. {slept / 60:.0f} min geschlafen "
              f"(Deckel zu/Ruhezustand) – Lauf dadurch verzögert.")
    return max(slept, 0.0)


def main() -> int:
    # Verhindert Ruhezustand während des Laufs (endet automatisch mit diesem Prozess).
    # Bewusst hier statt als `caffeinate …`-Präfix im Aufruf: der löst in Claude Code
    # eine Bestätigungs-Rückfrage aus und blockiert den unbeaufsichtigten Lauf.
    try:
        subprocess.Popen(["caffeinate", "-dis", "-w", str(os.getpid())])
    except OSError:
        pass
    args = parse_args()
    profiles = build_profiles(args)

    # Nach Ziel-Output gruppieren, statt direkt pro Profil zu speichern: so landen
    # z.B. mehrere per --keywords/Config-Liste expandierte Suchläufe mit demselben
    # 'output' gemeinsam in einer Datei, statt sich gegenseitig zu überschreiben.
    groups: dict[str | None, list[pd.DataFrame]] = {}
    manual_link_groups: dict[str | None, dict[str, list[str]]] = {}
    cv_paths: dict[str | None, str] = {}
    applied_csv_paths: dict[str | None, str] = {}
    auto_rate_flags: dict[str | None, bool] = {}
    applicants: dict[str | None, dict] = {}
    generate_apps: dict[str | None, bool] = {}
    application_match_labels: dict[str | None, list] = {}

    run_start = time.time()
    total_slept = 0.0
    abort_note = None

    for index, profile in enumerate(profiles):
        if time.time() - run_start > MAX_RUN_SECONDS:
            skipped = len(profiles) - index
            abort_note = (f"Lauf nach {MAX_RUN_SECONDS // 60} min abgebrochen – {skipped} von {len(profiles)} "
                          f"Suchprofilen nicht durchsucht (davon ca. {total_slept / 60:.0f} min Ruhezustand).")
            print(f"WARNUNG: {abort_note}\n")
            # Manuelle Such-Links (StepStone/Jobvector/heise) trotzdem für ALLE übrigen
            # Profile ausgeben – kostet keine Netzwerkzeit (Nutzerwunsch 2026-11-05).
            for rest in profiles[index:]:
                group = manual_link_groups.setdefault(rest.get("output"), {})
                for site, links in build_manual_links(rest).items():
                    site_links = group.setdefault(site, [])
                    site_links.extend(u for u in links if u not in site_links)
            break
        wall_start, mono_start = time.time(), time.monotonic()
        try:
            result, manual_links = run_profile(profile)
        except Exception as e:
            print(f"  WARNUNG: Profil '{profile.get('name') or profile['keywords']}' komplett fehlgeschlagen ({e}) – übersprungen, weiter mit nächstem\n")
            total_slept += _warn_if_slept(wall_start, mono_start)
            continue
        total_slept += _warn_if_slept(wall_start, mono_start)
        print(f"  Gesamt für dieses Profil: {len(result)} Stellenangebote (nach Deduplizierung).\n")
        output_key = profile.get("output")
        groups.setdefault(output_key, []).append(result)
        if manual_links:
            group = manual_link_groups.setdefault(output_key, {})
            for site, links in manual_links.items():
                group.setdefault(site, []).extend(links)
        if profile.get("cv_path"):
            cv_paths[output_key] = profile["cv_path"]
        if profile.get("applied_jobs_csv"):
            applied_csv_paths[output_key] = profile["applied_jobs_csv"]
        auto_rate_flags[output_key] = profile.get("auto_rate_with_haiku", True)
        if profile.get("applicant"):
            applicants[output_key] = profile["applicant"]
        if profile.get("generate_applications"):
            generate_apps[output_key] = True
        if profile.get("application_match_labels"):
            application_match_labels[output_key] = profile["application_match_labels"]
        if output_key:
            try:
                _save_partial(output_key, groups[output_key], manual_link_groups.get(output_key, {}),
                              applied_csv_paths.get(output_key))
            except Exception as e:
                print(f"  WARNUNG: Zwischenstand konnte nicht gespeichert werden ({e})")

    for output_path, frames in groups.items():
        combined = pd.concat(frames, ignore_index=True, sort=False) if len(frames) > 1 else frames[0]

        cv_path = cv_paths.get(output_path)
        if cv_path and not combined.empty:
            dedup_cols = [c for c in ["job_url", "title", "company"] if c in combined.columns]
            if dedup_cols:
                combined = combined.drop_duplicates(subset=dedup_cols)

            applied_csv = applied_csv_paths.get(output_path)
            if applied_csv:
                applied_jobs = _load_applied_jobs(applied_csv)
                before_applied = len(combined)
                combined = _filter_already_applied(combined, applied_jobs)
                if before_applied != len(combined):
                    print(f"  -> {before_applied - len(combined)} Treffer bereits in {applied_csv} als beworben markiert – ignoriert")

            if auto_rate_flags.get(output_path, True):
                try:
                    combined = profile_matcher.rate_jobs(combined, cv_path)
                    rank = {"Strong": 0, "Sehr gut": 1, "Gut": 2, "Kein Match": 3, "Unbewertet": 4, "Fehler": 4}
                    combined = combined.assign(
                        _rank=combined["profile_match"].map(rank).fillna(5),
                        _sort_date=pd.to_datetime(combined.get("date_posted"), errors="coerce"),
                    )
                    combined = (
                        combined.sort_values(["_rank", "_sort_date"], ascending=[True, False], na_position="last")
                        .drop(columns=["_rank", "_sort_date"])
                        .reset_index(drop=True)
                    )
                except Exception as e:
                    print(f"WARNUNG: Profil-Bewertung komplett fehlgeschlagen ({e}) – Ergebnisse werden ungewertet gespeichert")
                    combined = _sort_by_date_posted(combined)
            else:
                # Automatische Haiku-Bewertung bewusst deaktiviert (Nutzerwunsch 2026-09-08):
                # die Bewertung übernimmt stattdessen der ausführende Agent selbst, manuell
                # gegen den Lebenslauf/das dokumentierte Profil, gemäß der in der SKILL.md des
                # CareerRadar-Tasks hinterlegten Bewertungsregel. profile_match bleibt hier
                # absichtlich "Unbewertet", bis der Agent es überschreibt.
                combined["profile_match"] = "Unbewertet"
                combined = _sort_by_date_posted(combined)
                print("  Automatische Haiku-Bewertung deaktiviert (auto_rate_with_haiku: false) – "
                      "Bewertung erfolgt durch den ausführenden Agenten selbst, siehe SKILL.md.")

            if generate_apps.get(output_path):
                try:
                    labels = application_match_labels.get(output_path) or application_generator.DEFAULT_MATCH_LABELS
                    created = application_generator.generate_for_matches(
                        combined, cv_path=cv_path, applicant=applicants.get(output_path), match_labels=tuple(labels)
                    )
                    if created:
                        print(f"  {created} Bewerbungsunterlagen-PDF(s) erstellt (Match {list(labels)}) in Bewerbungsunterlagen_{datetime.now().strftime('%Y-%m-%d')}/")
                except Exception as e:
                    print(f"WARNUNG: Bewerbungsunterlagen-Erstellung fehlgeschlagen ({e}) – übersprungen")
        else:
            combined = _sort_by_date_posted(combined)

        links = manual_link_groups.get(output_path, {})
        if output_path:
            save(combined, output_path, links, note=abort_note)
        else:
            default_output = args.output
            if default_output is None:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                default_output = f"job_results_{timestamp}.csv"
            save(combined, default_output, links, note=abort_note)

    return 0


if __name__ == "__main__":
    sys.exit(main())
