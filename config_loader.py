"""Lädt Suchkriterien aus einer YAML- oder JSON-Konfigurationsdatei.

Unterstützt zwei Formate:
  1. Einzelnes Profil: Suchkriterien direkt auf oberster Ebene (siehe config.example.yaml).
  2. Mehrere gespeicherte Suchen: {"defaults": {...}, "searches": [{"name": ..., ...}, ...]}
     - "defaults" gilt für alle Einträge in "searches" und wird pro Eintrag überschrieben.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

DEFAULTS = {
    "location": "Stuttgart",
    "radius": 50,
    "sites": ["linkedin", "indeed"],
    "results_wanted": 25,
    "hours_old": None,
    "remote": False,
    "no_stepstone_link": False,
    "no_xing_link": False,
    "no_jobvector_link": False,
    "no_heise_link": False,
    "custom_urls": [],
    "output": None,
    "company": None,
    "exclude_keywords": [],
    "exclude_companies": [],
    "cv_path": None,
    "applied_jobs_csv": None,
    "auto_rate_with_haiku": True,
    "applicant": None,
    "generate_applications": False,
    "application_match_labels": None,  # None = application_generator-Default ("Sehr gut")
}


def _read_raw(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    return yaml.safe_load(text)


def load_profiles(path: str) -> list[dict]:
    """Gibt eine Liste normalisierter Suchprofile zurück (mit DEFAULTS aufgefüllt)."""
    raw = _read_raw(Path(path))
    if not raw:
        raise ValueError(f"Konfigurationsdatei ist leer oder ungültig: {path}")

    if "searches" in raw:
        base = {**DEFAULTS, **raw.get("defaults", {})}
        profiles = []
        for entry in raw["searches"]:
            profile = {**base, **entry}
            if "keywords" not in profile:
                raise ValueError(f"Suchprofil ohne 'keywords': {entry}")
            profiles.append(profile)
        return profiles

    profile = {**DEFAULTS, **raw}
    if "keywords" not in profile:
        raise ValueError(f"Konfigurationsdatei benötigt 'keywords': {path}")
    return [profile]
