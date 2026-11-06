# Job-Such-Agent

Durchsucht mehrere Jobportale nach konfigurierbaren Kriterien (Stichworte, Standort, Umkreis, remote).

## Portale

| Portal | Methode |
|---|---|
| LinkedIn, Indeed, Glassdoor, ZipRecruiter, Google Jobs | automatisiertes Scraping über [`python-jobspy`](https://pypi.org/project/python-jobspy/) (`--sites linkedin indeed ...`) |
| Bundesagentur für Arbeit | offizieller JSON-Such-Endpoint der Behörde selbst, kein Scraping (`--sites arbeitsagentur`) |
| Landeshauptstadt Stuttgart (Karriereseite) | statisches Scraping der städtischen Stellenübersicht, lokal nach Keyword gefiltert (`--sites stuttgart-stadt`) |
| StepStone | **kein Scraping** – StepStones `robots.txt` verbietet das Crawlen der Suchergebnis-Seiten. Der Agent gibt stattdessen nur den passenden Such-Link aus, den man manuell öffnet. |
| Eigene/weitere Portale | über `--custom-url` mit selbst angegebenen CSS-Selektoren; vor jedem Zugriff wird automatisch die `robots.txt` der Ziel-Domain geprüft und bei Verbot übersprungen |

### Geprüft, aber nicht (zuverlässig) automatisierbar

- **hays.de** – keine funktionierende Such-URL gefunden (mehrere Pfade getestet, alle 404)
- **stellenanzeigen.de** – React-Seite, Suchergebnisse werden clientseitig nachgeladen (nur 1 Eintrag im statischen HTML)
- **jobware.de** – blockiert direkte Requests (HTTP 403 Bot-Schutz), obwohl robots.txt es erlauben würde
- **jobsuche-bw.de** – Suchergebnis-URL liefert Serverfehler (HTTP 500) bei den getesteten Parametern

Für diese vier bleibt vorerst nur die manuelle Suche bzw. `--custom-url` mit selbst ermittelten Selektoren, falls sich die Seiten ändern.

## Setup

Benötigt Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Nutzung

```bash
python job_agent.py --keywords "Python Entwickler"
```

Nutzt automatisch Standort Stuttgart und 50 km Umkreis. Weitere Optionen:

### Gezielt nach Firma suchen

```bash
python job_agent.py --keywords "Python Entwickler" --company "Mercedes-Benz"
python job_agent.py --keywords "Python Entwickler" --company "Porsche"
python job_agent.py --keywords "Python Entwickler" --company "Allianz"
```

Der Firmenname wird in die Portalsuche (LinkedIn/Indeed) aufgenommen und die Ergebnisse
werden zusätzlich clientseitig darauf gefiltert, dass die `company`-Spalte den Namen
enthält – so kommen nur echte Treffer der Firma durch, keine Zufallstreffer.

Für bekannte Großunternehmen (Mercedes-Benz, Porsche, Allianz – siehe
[`scrapers/company_links.py`](scrapers/company_links.py)) gibt der Agent zusätzlich den
Link zum offiziellen Karriereportal aus. **Diese Portale werden nicht automatisch
gescraped**: es sind JavaScript-Single-Page-Apps (Workday/Phenom/eigene ATS-Systeme),
deren Suchergebnisse gar nicht im statischen HTML stecken – ein `requests`-basierter
Scraper würde dort einfach nichts finden. Der Link führt direkt zum Portal, wo man
manuell weitersucht. Weitere Firmen lassen sich in `company_links.py` ergänzen.

#### Mehrere Firmen auf einmal

Gleiche Keywords/Standort/Radius/etc. für mehrere Firmen durchsuchen – jede Firma
bekommt einen eigenen Suchlauf, Ergebnisse landen gemeinsam in einer Datei,
unterscheidbar über die Spalte `search_profile`:

```bash
python job_agent.py --keywords "Python Entwickler" --company "Mercedes-Benz" "Porsche" "Allianz"
```

Oder in der Config-Datei als Liste (siehe Kommentar in `config.example.yaml`):

```yaml
keywords: "Python Entwickler"
companies:
  - "Mercedes-Benz"
  - "Porsche"
  - "Allianz"
```

Kombinierbar mit einer `keywords`-Liste: dann entsteht das Kreuzprodukt aus
allen Keywords × allen Firmen.

### Mehrere Keywords

Als CLI-Flag mehrfach angeben (jedes Keyword = eigener Suchlauf, gleiche restlichen Kriterien):

```bash
python job_agent.py --keywords "Python Entwickler" "Backend Developer" "Software Engineer Python"
```

Oder in der Config-Datei als Liste (siehe Kommentar in `config.example.yaml`):

```yaml
keywords:
  - "Python Entwickler"
  - "Backend Developer"
```

Ergebnisse aller Keywords landen in derselben Ausgabedatei, unterscheidbar über die
Spalte `search_profile`.

### Kriterien aus einer Datei laden (statt CLI-Flags)

Statt Parameter jedes Mal per CLI einzutippen, können sie in einer YAML- oder
JSON-Datei hinterlegt werden – praktisch als persistente "Wissens"-Datei mit den
eigenen Standardsuchen:

```bash
python job_agent.py --config config.example.yaml
```

Siehe [`config.example.yaml`](config.example.yaml) für ein einzelnes Suchprofil.
CLI-Flags überschreiben dabei einzelne Werte aus der Datei, z.B.:

```bash
python job_agent.py --config config.example.yaml --results-wanted 10 --remote
```

Es können auch **mehrere gespeicherte Suchen** in einer Datei hinterlegt werden
(z.B. verschiedene Rollen/Standorte), die der Agent nacheinander abarbeitet und
im Ergebnis über die Spalte `search_profile` unterscheidet – siehe
[`config.multi.example.yaml`](config.multi.example.yaml):

```bash
python job_agent.py --config config.multi.example.yaml
```

Hinweis: Wird `--output` zusammen mit einer Multi-Profil-Config verwendet, gilt
der Wert für alle Profile ohne eigenes `output`-Feld gemeinsam (kombinierte Datei).
Profile mit eigenem `output`-Feld in der Config werden stattdessen einzeln gespeichert.

```bash
python job_agent.py \
  --keywords "Data Engineer" \
  --location "München" \
  --radius 30 \
  --sites linkedin indeed glassdoor \
  --results-wanted 50 \
  --remote \
  --hours-old 72 \
  --output ergebnisse.csv
```

### Eigene Portale hinzufügen

```bash
python job_agent.py --keywords "DevOps" \
  --custom-url "https://example-jobs.de/suche?q=devops" \
  --custom-item-selector ".job-card" \
  --custom-title-selector ".job-title" \
  --custom-link-selector ".job-title a" \
  --custom-company-selector ".company-name" \
  --custom-location-selector ".job-location"
```

Die passenden Selektoren findet man über die Browser-Entwicklertools (Rechtsklick → Untersuchen) auf der jeweiligen Such-Ergebnis-Seite.

### HTML-Ausgabe mit anklickbaren Direktlinks

```bash
python job_agent.py --keywords "Python Entwickler" --output ergebnisse.html
```

Erzeugt eine kompakte HTML-Tabelle (Suchprofil, Portal, Titel, Firma, Standort, Datum),
bei der der Jobtitel direkt zum Stellenangebot verlinkt – einfach im Browser öffnen.

### Treffer ausschließen (z.B. Praktika/Duales Studium)

```bash
python job_agent.py --keywords "IT Project Manager" --exclude "Duales Studium" "Praktikum"
```

Oder in der Config-Datei:

```yaml
exclude_keywords:
  - "Duales Studium"
  - "Praktikum"
```

Entfernt alle Treffer, deren Titel einen der Begriffe enthält (Groß-/Kleinschreibung egal).

### Stellen gegen den eigenen Lebenslauf bewerten

```bash
export ANTHROPIC_API_KEY="dein-key"   # selbst setzen, wird nie vom Agenten eingetragen
python job_agent.py --keywords "IT Project Manager" --cv-path CV.pdf
```

Oder in der Config-Datei: `cv_path: "CV.pdf"` (PDF oder DOCX, relativ zum Projektordner oder
absoluter Pfad). Ist `cv_path` gesetzt, wird für jede gefundene Stelle die volle
Stellenbeschreibung geladen (LinkedIn/Indeed/Bundesagentur/Stuttgart-Stadt – spürbar
langsamer) und per Claude API (Haiku) gegen den Lebenslauf bewertet. Neue Spalte
`profile_match`: **Strong** / **Sehr gut** / **Gut** / **Kein Match** – Ergebnisse werden
danach sortiert (bestes Match zuerst) und in der HTML-Ausgabe farblich hervorgehoben.

Verursacht laufende API-Kosten (ein Call pro Stelle, Haiku-Preise) und verlängert die
Laufzeit spürbar bei vielen Treffern – für sehr große Suchen (100+ Treffer) `--results-wanted`
reduzieren oder gezielt mit `--keywords`/`--company` einschränken.

### Automatische Bewerbungsunterlagen bei Top-Match

Nur relevant, wenn `cv_path` gesetzt ist (siehe oben). Für jede Stelle, die
`profile_matcher` mit **"Sehr gut"** bewertet, erstellt der Agent automatisch ein
vollständiges, versandfertiges Bewerbungs-PDF:

1. Ein individuelles Anschreiben wird per Claude API generiert (Sprache passend zur
   Stellenausschreibung – Englisch, wenn diese auf Englisch ist, sonst Deutsch),
   ausschließlich basierend auf Fakten aus dem Lebenslauf.
2. Das Anschreiben wird an den Original-CV (`cv_path`, falls PDF) angehängt.
3. Die kombinierte Datei landet in einem Ordner `Bewerbungsunterlagen_<YYYY-MM-DD>/`
   im Projektverzeichnis, benannt nach Firma und Jobtitel, z.B.
   `Bewerbung_Mercedes-Benz_Application_Manager.pdf`.

Bereits vorhandene Dateien (gleicher Name) werden nicht überschrieben, sondern
übersprungen – ein zweiter Lauf am selben Tag erzeugt also keine Duplikate und keine
zusätzlichen API-Kosten für bereits erstellte Unterlagen.

Für die Absender-Kopfzeile im Anschreiben (Name, Adresse, Telefon, E-Mail) wird der
`applicant`-Block aus der Config genutzt (siehe Kommentar in `config.example.yaml`):

```yaml
applicant:
  name: "Vorname Nachname"
  street: "Musterstraße 1"
  plz_city: "70000 Musterstadt"
  city: "Musterstadt"
  phone: "+49 000 0000000"
  email: "name@example.com"
```

Ohne diesen Block wird trotzdem generiert, dann aber ohne Absender-Kopfzeile im
Anschreiben. Nutzt denselben `ANTHROPIC_API_KEY` wie die Profil-Bewertung, verursacht
also pro "Sehr gut"-Treffer einen zusätzlichen (etwas teureren, weil Sonnet statt Haiku)
API-Call – bei typischerweise wenigen Top-Treffern pro Lauf ist das vernachlässigbar.

## Alle Optionen

```bash
python job_agent.py --help
```

## Hinweise

- Ergebnisse werden über alle Quellen hinweg dedupliziert (nach Link/Titel/Firma) und als CSV (Standard), JSON (`--output datei.json`) oder HTML (`--output datei.html`) gespeichert.
- Im `--output`-Dateinamen (bzw. `output:` in der Config) wird `{date}` automatisch durch das
  heutige Datum im Format TTMM ersetzt, z.B. `SearchResult_{date}.html` → `SearchResult_0708.html`.
  Praktisch, damit jeder Lauf eine eigene Datei bekommt statt die vorherige zu überschreiben.
- `--hours-old` filtert nach Aktualität der Anzeige einheitlich über alle Quellen.
- Jobportale ändern ihre HTML-Struktur gelegentlich; falls `--custom-url`-Selektoren keine Treffer mehr liefern, müssen sie angepasst werden.
