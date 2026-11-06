"""Erstellt automatisch Bewerbungsunterlagen (Anschreiben und CV als ZWEI separate PDFs)
für Stellen, die profile_matcher.rate_jobs() mit einem Top-Match bewertet hat.

Ablauf pro passender Stelle:
  1. Anschreiben-Text per Claude API generieren (Sprache passend zur Stellenausschreibung,
     ausschließlich auf Basis der Fakten aus dem Lebenslauf).
  2. Anschreiben als eigenes PDF rendern (reportlab) -> "Anschreiben_<Firma>_<Titel>.pdf".
  3. Original-CV-PDF unverändert daneben ablegen -> "CV_<Firma>_<Titel>.pdf".
  4. Beide in "generatedDocs/Bewerbungsunterlagen_<YYYY-MM-DD>/" ablegen.

Nutzt denselben ANTHROPIC_API_KEY wie profile_matcher (nur aus der Umgebungsvariable,
wird hier nirgends abgefragt oder gespeichert).
"""
from __future__ import annotations

import io
import re
import unicodedata
from datetime import datetime
from pathlib import Path

import pandas as pd
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import glob as _glob

import profile_matcher

# --- Schrift einbetten (TrueType statt Core-Font Helvetica), damit die PDF-Datei
# nicht als "leer"/zu klein von Online-Bewerbungsportalen abgelehnt wird
# (gleiche Ursache/Loesung wie beim CV-Builder). Fallback auf Helvetica falls
# keine TTFs gefunden werden. ---
def _find_font_files():
    import os
    search_dirs = ["/usr/share/fonts", "/Library/Fonts", "/System/Library/Fonts",
                   os.path.expanduser("~/Library/Fonts"), os.path.expanduser("~/.fonts")]
    # Liberation Sans zuerst (Linux/CI), sonst Arial (auf macOS immer vorhanden)
    for names in (
        ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf", "LiberationSans-Italic.ttf", "LiberationSans-BoldItalic.ttf"),
        ("Arial.ttf", "Arial Bold.ttf", "Arial Italic.ttf", "Arial Bold Italic.ttf"),
    ):
        found = [None, None, None, None]
        for d in search_dirs:
            if not os.path.isdir(d):
                continue
            for i, n in enumerate(names):
                for f in _glob.glob(d + f"/**/{n}", recursive=True):
                    found[i] = f
        if all(found):
            return tuple(found)
    return None


FONT_REGULAR = "Helvetica"
FONT_BOLD = "Helvetica-Bold"

_fonts = _find_font_files()
if _fonts:
    _reg, _bold, _ital, _boldital = _fonts
    try:
        pdfmetrics.registerFont(TTFont("CoverSans", _reg))
        pdfmetrics.registerFont(TTFont("CoverSans-Bold", _bold))
        pdfmetrics.registerFont(TTFont("CoverSans-Italic", _ital))
        pdfmetrics.registerFont(TTFont("CoverSans-BoldItalic", _boldital))
        pdfmetrics.registerFontFamily(
            "CoverSans", normal="CoverSans", bold="CoverSans-Bold",
            italic="CoverSans-Italic", boldItalic="CoverSans-BoldItalic",
        )
        FONT_REGULAR = "CoverSans"
        FONT_BOLD = "CoverSans-Bold"
    except Exception:
        pass

COVER_LETTER_MODEL = "claude-sonnet-4-5-20250929"
MAX_DESCRIPTION_CHARS = 3000
DEFAULT_MATCH_LABELS = ("Sehr gut",)

SYSTEM_PROMPT = """Du schreibst ein individuelles, professionelles Bewerbungsanschreiben \
basierend auf einem Lebenslauf und einer Stellenausschreibung.

Regeln:
- Erkenne die Sprache der Stellenausschreibung automatisch. Ist sie auf Englisch verfasst, \
schreibe das gesamte Anschreiben auf Englisch, sonst auf Deutsch.
- Nutze ausschließlich Fakten, die im Lebenslauf stehen. Erfinde keine Erfahrungen, Titel, \
Zahlen, Firmen oder Fähigkeiten hinzu.
- Struktur, in dieser Reihenfolge, ohne zusätzliche Elemente:
  1. Eine Betreffzeile (z.B. "Bewerbung als <Rolle> bei <Firma>" bzw. "Application for <Role> \
at <Company>")
  2. Eine Leerzeile
  3. Anrede ("Sehr geehrte Damen und Herren," bzw. "Dear Hiring Team,") – ein konkreter \
Ansprechpartner ist nicht bekannt
  4. 3 bis 4 Absätze Fließtext: (a) warum diese Rolle/Firma, (b) konkrete, zur Stelle passende \
Erfahrung aus dem Lebenslauf mit Beispielen, (c) falls vorhanden ehrlicher, selbstbewusster \
Umgang mit Lücken zwischen Profil und Anforderung, (d) kurzer Abschluss mit Gesprächsangebot
  5. Grußformel ("Mit freundlichen Grüßen" bzw. "Best regards,"), danach der vollständige Name \
des Bewerbers in einer eigenen Zeile
- Ton: selbstbewusst, konkret, ohne Floskeln oder Übertreibungen. Keine Emojis, keine \
Ausrufezeichen-Häufung.
- Länge: 200-350 Wörter Fließtext (ohne Betreff/Anrede/Gruß).
- Antworte NUR mit dem fertigen Anschreiben-Text (Betreff bis Signatur). Keine Erklärungen, \
kein Markdown, keine Anführungszeichen drumherum."""

PAGE_W, PAGE_H = A4
MARGIN_L = 25 * mm
MARGIN_R = 20 * mm
MARGIN_TOP = 25 * mm
MARGIN_BOTTOM = 20 * mm
CONTENT_WIDTH = PAGE_W - MARGIN_L - MARGIN_R


def _sanitize(value: str, max_len: int = 60) -> str:
    """Macht einen String dateinamentauglich (ASCII, keine Sonderzeichen)."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    return text[:max_len] or "Unbekannt"


def generate_cover_letter_text(
    client, cv_text: str, applicant_name: str, title: str, company: str, location: str, description: str | None
) -> str:
    """Ein Claude-API-Call, der den vollständigen Anschreiben-Text (Betreff bis Signatur) liefert."""
    description = (description or "")[:MAX_DESCRIPTION_CHARS]
    user_content = (
        f"BEWERBER: {applicant_name}\n\n"
        f"LEBENSLAUF:\n{cv_text}\n\n---\n\n"
        f"STELLENAUSSCHREIBUNG:\nTitel: {title}\nFirma: {company}\nStandort: {location or ''}\n"
        f"Beschreibung: {description or '(keine Beschreibung verfügbar – allgemein passend zu Titel/Firma formulieren)'}"
    )
    resp = client.messages.create(
        model=COVER_LETTER_MODEL,
        max_tokens=1200,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    return resp.content[0].text.strip()


def _draw_wrapped(c: canvas.Canvas, text: str, y: float, font: str = None, size: int = 11, leading: float = 15) -> float:
    """Zeichnet Fließtext (durch Leerzeilen getrennte Absätze) mit Zeilenumbruch und
    automatischem Seitenumbruch. Gibt die neue y-Position zurück."""
    font = font or FONT_REGULAR
    c.setFont(font, size)
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        for line in simpleSplit(paragraph, font, size, CONTENT_WIDTH):
            if y < MARGIN_BOTTOM:
                c.showPage()
                c.setFont(font, size)
                y = PAGE_H - MARGIN_TOP
            c.drawString(MARGIN_L, y, line)
            y -= leading
        y -= leading * 0.6
    return y


def render_cover_letter_pdf_bytes(header_lines: list[str], body_text: str) -> bytes:
    """Rendert das Anschreiben komplett im Speicher (kein Temp-File auf der Platte nötig)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = PAGE_H - MARGIN_TOP
    c.setFont(FONT_REGULAR, 11)
    for line in header_lines:
        if y < MARGIN_BOTTOM:
            c.showPage()
            c.setFont(FONT_REGULAR, 11)
            y = PAGE_H - MARGIN_TOP
        if line:
            c.drawString(MARGIN_L, y, line)
        y -= 15
    y -= 10
    _draw_wrapped(c, body_text, y)
    c.save()
    return buf.getvalue()


def _write_cover_letter(cover_letter_pdf_bytes: bytes, output_path: Path) -> None:
    with open(output_path, "wb") as f:
        f.write(cover_letter_pdf_bytes)


def _write_cv_copy(cv_path: str, output_path: Path) -> None:
    """Legt eine unveränderte Kopie des Original-CV unter dem stellenspezifischen
    Dateinamen ab (falls PDF). Bei Nicht-PDF-CVs (z.B. .docx) wird nur ein Hinweis
    ausgegeben, da hier keine Formatkonvertierung stattfindet."""
    cv = Path(cv_path)
    if cv.suffix.lower() == ".pdf":
        writer = PdfWriter()
        for page in PdfReader(str(cv)).pages:
            writer.add_page(page)
        with open(output_path, "wb") as f:
            writer.write(f)
    else:
        print(f"    Hinweis: CV ist kein PDF ({cv.suffix}) – bitte manuell als {output_path.name} beilegen.")


def generate_for_matches(
    df: pd.DataFrame,
    *,
    cv_path: str,
    applicant: dict | None = None,
    match_labels: tuple[str, ...] = DEFAULT_MATCH_LABELS,
    project_dir: str | Path | None = None,
) -> int:
    """Erstellt für jede Stelle mit profile_match in match_labels zwei separate PDFs
    (Anschreiben.pdf und CV.pdf) je in einem eigenen Unterordner
    "generatedDocs/Bewerbungsunterlagen_<Datum>/<Firma>_<Titel>/".
    Bereits vorhandene Ordner (gleicher Name) werden übersprungen, nicht überschrieben.
    Gibt die Anzahl neu erstellter Stellen zurück."""
    if df.empty or "profile_match" not in df.columns:
        return 0

    matches = df[df["profile_match"].isin(match_labels)]
    if matches.empty:
        return 0

    applicant = applicant or {}
    if not applicant.get("name"):
        print("  Hinweis: Kein 'applicant'-Block in der Config gesetzt – Bewerbungsunterlagen werden ohne Absender-Kopfzeile erstellt.")

    base_dir = Path(project_dir) if project_dir else Path(__file__).resolve().parent
    date_str = datetime.now().strftime("%Y-%m-%d")
    day_dir = base_dir / "generatedDocs" / f"Bewerbungsunterlagen_{date_str}"
    day_dir.mkdir(parents=True, exist_ok=True)

    cv_text = profile_matcher.load_cv_text(cv_path)
    client = profile_matcher.get_client()

    name = applicant.get("name", "")
    street = applicant.get("street", "")
    plz_city = applicant.get("plz_city", "")
    city = applicant.get("city") or (plz_city.split(" ", 1)[-1] if plz_city else "")
    phone = applicant.get("phone", "")
    email = applicant.get("email", "")
    contact_line = " | ".join(p for p in [f"Mobil: {phone}" if phone else "", f"E-Mail: {email}" if email else ""] if p)

    created = 0
    for _, row in matches.iterrows():
        company = row.get("company") or "Unbekannt"
        title = row.get("title") or "Stelle"
        location = row.get("location") or ""

        job_dir = day_dir / f"{_sanitize(company)}_{_sanitize(title)}"
        letter_path = job_dir / "Anschreiben.pdf"
        cv_out_path = job_dir / "CV.pdf"
        if letter_path.exists() and cv_out_path.exists():
            print(f"  Bewerbungsunterlagen für '{title}' bei {company} bereits vorhanden, übersprungen: {job_dir.name}")
            continue
        job_dir.mkdir(parents=True, exist_ok=True)

        print(f"  Erstelle Bewerbungsunterlagen für '{title}' bei {company} ...")
        try:
            body = generate_cover_letter_text(client, cv_text, name, title, company, location, row.get("description"))
        except Exception as e:
            print(f"    WARNUNG: Anschreiben-Generierung fehlgeschlagen für '{title}' bei {company}: {e}")
            continue

        date_line = datetime.now().strftime("%d.%m.%Y")
        header_lines = [l for l in [name, street, plz_city, contact_line, "", company, location, "", f"{city}, {date_line}" if city else date_line, ""] if l is not None]

        try:
            letter_bytes = render_cover_letter_pdf_bytes(header_lines, body)
            _write_cover_letter(letter_bytes, letter_path)
            _write_cv_copy(cv_path, cv_out_path)
        except Exception as e:
            print(f"    WARNUNG: PDF-Erstellung fehlgeschlagen für '{title}' bei {company}: {e}")
            continue

        print(f"    -> gespeichert in {job_dir}/")
        created += 1

    return created
