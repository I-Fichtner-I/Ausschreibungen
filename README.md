# tender-ai - Procurement Intelligence Agent

Automatisierte Recherche, Analyse und Profitabilitaetsbewertung oeffentlicher
Ausschreibungen.

Das Projekt wird **stufenweise** gebaut: jede Stufe ist einzeln lauffaehig und
testbar, bevor die naechste beginnt.

> **Stufe 1 ist fertig: Ausschreibungen automatisiert recherchieren.**
> Quell-Adapter (TED, RSS-Portale, Offline-Fixture), einheitliches Datenmodell,
> Dublettenerkennung, Speicherung, Aenderungserkennung, Export und CLI.
>
> **Stufe 2 ist fertig: Ausschreibungen analysieren.**
> `tender-ai documents <id>` laedt die frei zugaenglichen Unterlagen und
> extrahiert Text und Tabellen aus PDF, DOCX, XLSX, HTML und CSV.
> `tender-ai analyze <id>` erkennt daraus Anforderungen (Zertifikate,
> Mindestanforderungen, Zahlungs- und Lieferbedingungen, Zuschlagskriterien,
> Herstellerbindung) und berechnet einen begruendeten Risiko-Score.
>
> **Stufe 3 ist fertig: Artikel aus dem Leistungsverzeichnis erkennen.**
> `tender-ai items <id>` liest aus den erkannten Tabellen die zu liefernden
> Positionen - Ordnungszahl, Bezeichnung, Menge, Einheit, Hersteller, Typ,
> Artikelnummer und Merkmale, jede mit Fundstelle und Konfidenz.
>
> Die Stufen 4-6 (Preisrecherche, Kalkulation, Scoring, Dashboard) folgen
> danach - siehe [docs/architecture.md](docs/architecture.md).

---

## Schnellstart

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env          # optional: API-Schluessel eintragen
tender-ai init                # Verzeichnisse + Datenbank anlegen
```

### 1. Offline ausprobieren (ohne Netzwerk)

```bash
tender-ai search --source fixture          # Demodaten aus data/fixtures/
tender-ai list
tender-ai show fixture:demo-2026-0001
tender-ai export data/exports/test.xlsx
```

### 2. Quellen live pruefen

```bash
tender-ai doctor
```

Zeigt je Quelle, ob der Endpunkt erreichbar ist und die Antwort geparst werden
kann. **Damit zuerst testen** - der Befehl sagt genau, welche Quelle klemmt und
warum.

### 3. Vergabeunterlagen auslesen (Stufe 2)

```bash
tender-ai documents ted:00123456-2026          # laedt und extrahiert
tender-ai documents ted:00123456-2026 --json   # maschinenlesbar
```

Nur Dokumente mit `access = PUBLIC` werden abgerufen. Geschuetzte Unterlagen
(Login, Captcha, Paywall) werden uebersprungen und in der Ausgabe als
"nicht oeffentlich" ausgewiesen - sie werden nicht umgangen.

### 4. Anforderungen und Risiko bewerten (Stufe 2)

```bash
tender-ai analyze ted:00123456-2026             # laedt fehlende Unterlagen mit
tender-ai analyze ted:00123456-2026 --findings  # jede Fundstelle mit Beleg
tender-ai analyze --all -n 50                   # Stapellauf, z. B. per cron
```

Der Risiko-Score ist additiv und **erklaerbar**: jeder Faktor nennt seine
Punkte, eine Begruendung und den Satz aus dem Dokument, auf dem er beruht.
`tender-ai list` und `tender-ai show` zeigen die Bewertung danach mit an.

Wichtig: **Fehlende Information senkt das Risiko nicht.** Fehlen Frist,
Volumen oder auswertbare Unterlagen, erzeugt genau das eigene Faktoren - eine
unbekannte Ausschreibung soll nicht unauffaellig wirken. Die Auswertung ist
regelbasiert: jeder Hinweis ist ein Fund im Text, keine Rechtsauskunft.

### 5. Artikel erkennen (Stufe 3)

```bash
tender-ai items ted:00123456-2026                     # Positionen auflisten
tender-ai items ted:00123456-2026 --evidence          # mit Fundstelle je Position
tender-ai items ted:00123456-2026 --min-confidence 60 # nur sicher gelesene Zeilen
tender-ai items --all -n 50                           # Stapellauf
```

```
Positionen: 5, kalkulierbar: 4, mittlere Konfidenz: 80
Pos.   Bezeichnung                                  Menge      Einheit  Hersteller / Typ      Konf.
1.10   Monitor 27 Zoll, Fabrikat: Muster GmbH ...     120      STK      Muster GmbH / MX-27      95
1.30   Hoehenverstellbarer Schreibtisch 160x80 cm     ~60      STK      -                        75
1.50   Vor-Ort-Montage                            UNKNOWN      H        -                        63

Hinweis 1.50: Menge steht nicht fest: 'auf Abruf'
```

Gelesen wird aus den in Stufe 2 erkannten Tabellen - mit Kopfzeile ueber die
Spaltennamen, ohne Kopfzeile ueber den Inhalt der Spalten. Erst wenn keine
Tabelle auswertbar ist, greift die Rueckfallebene ueber Positionsmuster im
Fliesstext; solche Positionen sind als `source_kind = TEXT` gekennzeichnet und
tragen eine niedrigere Konfidenz.

Drei Regeln, die das Ergebnis brauchbar halten:

- **Keine erfundenen Mengen.** "auf Abruf" bleibt UNKNOWN, "ca. 20" wird als
  Schaetzung gekennzeichnet (`~20`), ein mehrdeutiges "1.200" ebenfalls.
- **Jede Position ist belegbar.** Dokument, Seite, Abschnitt und Originalzeile
  stehen an der Position; `--evidence` zeigt sie an.
- **Erkennung und Zuordnung sind zwei Dinge.** `confidence` sagt, wie sicher
  die Zeile *gelesen* wurde. `match_confidence` bleibt leer, bis in Stufe 4 ein
  konkretes Produkt zugeordnet ist - eine unsichere Zuordnung wird nicht durch
  eine erfundene Zahl kaschiert.

Eine Fabrikatsvorgabe ohne "oder gleichwertig" in derselben Position wird als
`brand_locked` markiert - genau die Konstellation, die Alternativangebote
ausschliesst.

### 6. Echte Recherche

```bash
# EU-weit (TED) nach Monitoren, veroeffentlicht in den letzten 14 Tagen
tender-ai search --source ted -k "Monitor" -k "Bildschirm" --cpv 30231300 --days 14

# alle aktiven Quellen, mind. 7 Tage Restfrist, Export als Excel
tender-ai search --days 7 --min-deadline-days 7 --export data/exports/treffer.xlsx --format xlsx

# gespeicherte Ergebnisse durchsehen
tender-ai list --open --order deadline
tender-ai show ted:00123456-2026
tender-ai runs                              # Laufprotokoll + Quellenstatus
```

---

## Befehle

| Befehl | Zweck |
|--------|-------|
| `tender-ai init` | Verzeichnisse anlegen, Datenbankschema per Migration erzeugen |
| `tender-ai db-upgrade` | Datenbankschema auf den aktuellen Stand bringen |
| `tender-ai sources` | konfigurierte Quellen anzeigen |
| `tender-ai doctor [--source X] [--json]` | Erreichbarkeit und Parsing pruefen |
| `tender-ai search [...]` | recherchieren (siehe `--help`) |
| `tender-ai list [--open] [--search TEXT]` | gespeicherte Ausschreibungen |
| `tender-ai show <id>` | Details, Dokumente, Dubletten, Aenderungen |
| `tender-ai documents <id>` | Vergabeunterlagen laden und auslesen (Stufe 2) |
| `tender-ai analyze <id> [--findings]` | Anforderungen erkennen, Risiko bewerten |
| `tender-ai analyze --all [-n N]` | alle laufenden Ausschreibungen bewerten |
| `tender-ai items <id> [--evidence]` | Positionen des Leistungsverzeichnisses erkennen (Stufe 3) |
| `tender-ai items --all [-n N]` | Positionen aller laufenden Ausschreibungen erkennen |
| `tender-ai export <datei>` | JSON / CSV / XLSX |
| `tender-ai runs` | letzte Laeufe und Quellenstatus |
| `tender-ai cache-clear` | HTTP-Cache leeren |

Wichtige `search`-Optionen: `-k/--keyword`, `--cpv`, `--country`, `-s/--source`,
`--days`, `--min-deadline-days`, `-n/--limit`, `--no-store`, `--download-docs`,
`--export` + `--format`, `--json`.

`--source` aktiviert eine Quelle auch dann, wenn sie in `config.yaml`
deaktiviert ist - praktisch fuer die Offline-Fixture.

---

## Konfiguration

- **`config.yaml`** - Quellen, Suchvorgaben, HTTP-Verhalten, Dubletten,
  Mindestkriterien, Score-Schwellen. Keine Secrets.
- **`.env`** - ausschliesslich Secrets und Umgebungsspezifisches
  (`TENDER_AI_SOURCE_API_KEYS__<QUELLE>`, `TENDER_AI_DATABASE_URL`, ...).
  API-Schluessel werden als `SecretStr` gehalten und in Logs maskiert.

Reihenfolge: Umgebungsvariablen (`TENDER_AI_`, verschachtelt mit `__`) schlagen
`.env`, `.env` schlaegt `config.yaml`.

Die Konfiguration jeder Quelle wird gegen die Klasse ihres `type` geprueft: ein
Tippfehler (`page_sze` statt `page_size`) faellt beim Start auf. Ein unbekannter
`type` macht die Konfiguration dagegen nicht ungueltig - die Quelle wird
gemeldet und uebersprungen.

Neue Quelle aktivieren, Beispiel RSS-Feed eines Landesportals:

```yaml
sources:
  land_xy:
    enabled: true
    type: rss
    priority: 30
    requests_per_second: 0.5
    feeds:
      - name: "Vergabeportal Land XY"
        url: "https://…/ausschreibungen.xml"
        country: "DEU"
```

Danach `tender-ai doctor --source land_xy`.

Datenbank: SQLite ist Standard (`data/tender_ai.db`). Fuer den Produktivbetrieb
in `.env`:
`TENDER_AI_DATABASE_URL=postgresql+psycopg://user:passwort@host:5432/tender_ai`

Das Schema wird ueber Alembic verwaltet. `tender-ai init` legt es an,
`tender-ai db-upgrade` bringt eine bestehende Datenbank auf den aktuellen
Stand. Eine Datenbank aus der Zeit vor den Migrationen wird automatisch auf
die initiale Revision gestempelt, ohne Daten zu verlieren. SQLite laeuft im
WAL-Modus mit `busy_timeout` und aktiven Fremdschluesseln, damit ein
cron-Lauf und die interaktive CLI sich nicht gegenseitig blockieren.

---

## Automatisierung

Stufe 1 laeuft ueber cron - ein eigener Scheduler kommt, wenn mehrere Stufen zu
takten sind:

```cron
0 6 * * * cd /pfad/zum/projekt && .venv/bin/tender-ai search --days 2 >> data/cron.log 2>&1
30 6 * * * cd /pfad/zum/projekt && .venv/bin/tender-ai analyze --all >> data/cron.log 2>&1
```

Der Analyselauf ueberspringt Ausschreibungen, die sich seit ihrer letzten
Bewertung nicht geaendert haben (Vergleich ueber den Inhalts-Hash).

Jeder Lauf erkennt neue Ausschreibungen, aktualisiert bekannte und
protokolliert Aenderungen (Frist, Volumen, Status, Dokumente) in
`tender_changes` - die Grundlage fuer die spaeteren Benachrichtigungen.

---

## Abhaengigkeiten und CI

`pyproject.toml` ist die einzige Quelle der Abhaengigkeiten (mit
Major-Obergrenzen). Die Lockdatei `uv.lock` und die daraus exportierten
`requirements.txt` / `requirements-dev.txt` werden **nicht von Hand** gepflegt:

```bash
uv lock
uv export --format requirements-txt --no-hashes --no-emit-project --no-dev -o requirements.txt
uv export --format requirements-txt --no-hashes --no-emit-project --extra dev --extra excel -o requirements-dev.txt
```

Die CI (`.github/workflows/ci.yml`) laeuft auf jedem PR mit Python 3.12 und
3.13: `ruff check`, `ruff format --check`, `mypy tender_ai`, `pytest` mit
Coverage-Schwelle und `pip-audit`. Lokal dasselbe:

```bash
ruff check . && ruff format --check . && mypy tender_ai && pytest -q --cov=tender_ai
```

## Tests

```bash
pytest -q            # laeuft offline in wenigen Sekunden
```

Die Adaptertests arbeiten gegen aufgezeichnete HTTP-Antworten (`respx`), nicht
gegen echte Portale. Geprueft werden unter anderem: Retry mit Exponential
Backoff, `Retry-After`, robots.txt-Sperre, Cache, Rate-Limit, Ausfall einer
Quelle, Dublettenerkennung, Aenderungserkennung, Export und die CLI.

---

## Status der Quellen

| Quelle | Typ | Bemerkung |
|--------|-----|-----------|
| `ted` | offizielle EU-Such-API (TED) | Endpunkt, Feldliste und Query-Syntax sind in `config.yaml` konfigurierbar, weil TED seine API versioniert. Optionaler API-Key ueber `.env`. |
| `bund_rss` | RSS | oeffentlicher Ausschreibungs-Feed von service.bund.de; weitere Feeds ohne Codeaenderung ergaenzbar |
| `evergabe_nrw` | HTML-Trefferliste (`html_list`) | Vergabemarktplatz NRW. **Standardmaessig aus** - erst `tender-ai doctor --source evergabe_nrw` bestaetigt die Selektoren (siehe unten) |
| `fixture` | lokale JSON-Datei | Demo- und Testquelle, kein Netzwerk |

### Portale ohne Schnittstelle (`html_list`)

Bietet ein Portal keine API und keinen Feed, bleibt die oeffentliche
Trefferliste. Der `html_list`-Adapter macht daraus einen Konfigurationsblock
statt eines handgeschriebenen Parsers: Zeilenselektor plus Feldselektoren
stehen in `config.yaml`, ein geaendertes Markup ist damit eine
Konfigurationsaenderung.

```yaml
evergabe_nrw:
  enabled: false                     # erst nach der Pruefung unten einschalten
  type: html_list
  requests_per_second: 0.5           # bewusst langsam - fremde Infrastruktur
  base_url: "https://www.evergabe.nrw.de"
  list_url: "https://www.evergabe.nrw.de/VMPCenter/company/announcements/categoryOverview.do?method=show"
  timezone: "Europe/Berlin"          # Fristen stehen dort als Ortszeit
  row_selector: "table tr"
  required_fields: ["title", "detail_url"]
  fields:
    title:         {selector: "a"}
    detail_url:    {selector: "a", attribute: "href"}
    contracting_authority: {selector: "td:nth-of-type(2)"}
    submission_deadline:   {selector: "td:nth-of-type(3)"}
```

**Selektoren pruefen statt raten.** `tender-ai doctor --source evergabe_nrw`
ruft die Liste einmal ab und meldet je Feld die Trefferquote:

```
evergabe_nrw  21 Zeile(n), davon 20 verwertbar | title 20/21, detail_url 20/21,
              contracting_authority 20/21, submission_deadline 0/21
              | ohne Treffer: submission_deadline
```

`submission_deadline 0/21` zeigt sofort auf den falschen Selektor - eine Zeile
in `config.yaml`, kein Release. Findet der Zeilenselektor nichts oder ist keine
Zeile verwertbar, meldet `doctor` die Quelle als **nicht ok**; ein leeres
Ergebnis wird nie als Erfolg ausgegeben.

Weitere Optionen: `page_param` + `max_pages` (Blaetterung), `follow_detail` +
`detail_fields` (Detailseite nachladen, gedeckelt durch `max_detail_requests`),
`id_param` (Query-Parameter als stabile Quell-ID), `regex` je Feld.

Der Adapter ruft ausschliesslich frei erreichbare Seiten ueber den normalen
HTTP-Client ab - mit robots.txt-Pruefung, Rate-Limit und Cache. Er meldet sich
nirgends an und umgeht keine Zugriffsbeschraenkung. Vor dem Dauerbetrieb eines
fremden Portals gehoert ausserdem ein Blick in dessen Nutzungsbedingungen.

**Wichtiger Hinweis zur Abnahme:** Die Entwicklungsumgebung hatte keinen
Netzzugang zu externen Portalen (die Netzwerkpolicy laesst nur Paketregistries
zu). Die Adapter sind vollstaendig implementiert und gegen aufgezeichnete
Antworten getestet, aber **die Live-Endpunkte konnten hier nicht verifiziert
werden**. Der erste Schritt bei dir ist deshalb `tender-ai doctor`: schlaegt
eine Quelle fehl, stehen Endpunkt, Pfad, Feldliste und Query-Felder in
`config.yaml` und lassen sich ohne Codeaenderung anpassen.

---

## Was das Tool nicht tut

- keine Umgehung von Captchas, Logins, Paywalls oder sonstigen
  Zugriffsbeschraenkungen; robots.txt wird beachtet (eine Sperre wird als
  Quellfehler gemeldet, nicht umgangen)
- keine automatische Abgabe verbindlicher Angebote und keine rechtlich
  bindenden Erklaerungen. Der Ablauf bleibt:
  **Analyse → Freigabe durch den Nutzer → Angebotsentwurf → manuelle Pruefung
  → manuelle Abgabe**
- keine erfundenen Daten: fehlende Angaben erscheinen als `UNKNOWN`,
  geschaetzte Werte sind als Schaetzung gekennzeichnet, jede Angabe traegt
  Quelle und Abrufzeitpunkt

---

## Projektstruktur

```
tender_ai/
├── config.py              Konfiguration (config.yaml + .env + Umgebung)
├── cli.py                 Kommandozeile
├── core/                  HTTP (Retry/Backoff/Rate-Limit/Cache), robots.txt, Logging, Fehler
├── models/                Tender, TenderLot, TenderDocument, Provenance, …
├── sources/               base.py, registry.py, ted.py, rss.py, fixture.py, parsing.py
├── services/              run_search, check_sources, fetch_documents, analyze_tender, extract_tender_items
├── extraction/            PDF, DOCX, XLSX, HTML, Text/CSV -> Seiten und Tabellen
├── analysis/              Anforderungserkennung (Regeln) und Risiko-Score
├── items/                 Artikelerkennung: Spaltenrollen, Einheiten, Positionen
├── pipeline/              ingest.py (Lauforchestrierung), dedup.py
├── database/              SQLAlchemy-Modelle, Session, Repository, Alembic-Migrationen
└── export/                JSON / CSV / XLSX
tests/                     Offline-Tests (respx-Mocks)
config.yaml  .env.example  docs/architecture.md
```

Naechste Stufe: **Marktpreise recherchieren** - zu jeder erkannten Position
Lieferanten und Preise finden, jede Preisangabe mit Quelle, Zeitpunkt,
Waehrung, Netto/Brutto-Status, Versandkosten und Verfuegbarkeit.
Details in [docs/architecture.md](docs/architecture.md).

## Review und Roadmap

- [docs/ARCHITECTURE_REVIEW.md](docs/ARCHITECTURE_REVIEW.md) - evidenzbasierte
  Bestandsaufnahme von Stufe 1 (Findings mit Belegen, Messungen, Diskrepanzen
  zwischen Dokumentation und Code)
- [docs/OPTIMIZATION_ROADMAP.md](docs/OPTIMIZATION_ROADMAP.md) - priorisierte,
  direkt umsetzbare Tasks mit Akzeptanzkriterien und empfohlener Reihenfolge;
  Wellen 0-3 sind das Gate vor Beginn von Stufe 2
