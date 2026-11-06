"""Bewertet Stellenanzeigen gegen einen Lebenslauf via Claude API.

Der ANTHROPIC_API_KEY wird ausschließlich aus der Umgebungsvariable gelesen –
dieses Skript fragt ihn nie ab und speichert ihn nirgends. Setze ihn selbst, z.B.:
    export ANTHROPIC_API_KEY="dein-key"
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
from pypdf import PdfReader
from docx import Document

MODEL = "claude-haiku-4-5-20251001"
RATING_LABELS = ["Strong", "Sehr gut", "Gut", "Kein Match"]
MAX_DESCRIPTION_CHARS = 3000
MAX_CV_CHARS = 6000

SYSTEM_PROMPT = f"""Du bist ein Recruiting-Assistent. Du bekommst einen Lebenslauf und eine \
Stellenausschreibung (Titel, Firma, Beschreibung). Bewerte, wie gut die Stelle zum Profil \
im Lebenslauf passt – bezogen auf Erfahrungslevel, fachliche Ausrichtung und Branche.

Antworte AUSSCHLIESSLICH mit genau einem dieser Labels, exakt so geschrieben, ohne \
Anführungszeichen, ohne weiteren Text:
{" / ".join(RATING_LABELS)}

"Strong" = idealer Fit auf allen Ebenen. "Sehr gut" = starker Fit mit kleineren Abweichungen \
(z.B. Branche oder Seniorität leicht anders). "Gut" = grundsätzlich passend, aber spürbare \
Lücken. "Kein Match" = Rolle passt nicht (z.B. völlig anderes Erfahrungslevel oder Fachgebiet)."""


def load_cv_text(cv_path: str) -> str:
    path = Path(cv_path)
    if not path.exists():
        raise FileNotFoundError(f"CV-Datei nicht gefunden: {cv_path}")

    if path.suffix.lower() == ".pdf":
        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    elif path.suffix.lower() in (".docx", ".doc"):
        doc = Document(str(path))
        text = "\n".join(p.text for p in doc.paragraphs)
    else:
        raise ValueError(f"Nicht unterstütztes CV-Format: {path.suffix} (nur .pdf/.docx)")

    return text.strip()[:MAX_CV_CHARS]


def get_client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY ist nicht gesetzt. Bitte selbst setzen, z.B.:\n"
            '  export ANTHROPIC_API_KEY="dein-key"\n'
            "(aus Sicherheitsgründen trägt der Agent API-Keys nie selbst ein)"
        )
    import anthropic

    return anthropic.Anthropic(api_key=api_key)


def _rate_one(client, cv_text: str, title: str, company: str, description: str) -> str:
    description = description if isinstance(description, str) else ""
    description = description[:MAX_DESCRIPTION_CHARS]
    user_content = (
        f"LEBENSLAUF:\n{cv_text}\n\n---\n\n"
        f"STELLENAUSSCHREIBUNG:\nTitel: {title}\nFirma: {company}\n"
        f"Beschreibung: {description or '(keine Beschreibung verfügbar – bewerte nur anhand von Titel/Firma)'}"
    )
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=10,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
        label = resp.content[0].text.strip()
        return label if label in RATING_LABELS else "Unbewertet"
    except Exception as e:  # nicht die ganze Bewertung wegen eines einzelnen Fehlers abbrechen
        print(f"    Warnung: Bewertung fehlgeschlagen für '{title}': {e}")
        return "Fehler"


def rate_jobs(df: pd.DataFrame, cv_path: str) -> pd.DataFrame:
    """Fügt eine 'profile_match'-Spalte hinzu (Strong/Sehr gut/Gut/Kein Match/Unbewertet/Fehler)."""
    if df.empty:
        df = df.copy()
        df["profile_match"] = []
        return df

    cv_text = load_cv_text(cv_path)
    client = get_client()

    print(f"Bewerte {len(df)} Stellen gegen {cv_path} (Claude {MODEL}) ...")
    ratings = []
    for i, row in enumerate(df.itertuples(), 1):
        rating = _rate_one(
            client,
            cv_text,
            getattr(row, "title", ""),
            getattr(row, "company", ""),
            getattr(row, "description", None),
        )
        ratings.append(rating)
        if i % 10 == 0 or i == len(df):
            print(f"  {i}/{len(df)} bewertet")

    df = df.copy()
    df["profile_match"] = ratings
    return df
