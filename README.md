# tender-ai - Procurement Intelligence Agent

Automatisierte Recherche, Analyse und Profitabilitaetsbewertung oeffentlicher
Ausschreibungen.

Das Projekt wird **stufenweise** gebaut: jede Stufe ist einzeln lauffaehig und
testbar, bevor die naechste beginnt.

> **Stufe 1 ist fertig und kann getestet werden: Ausschreibungen automatisiert
> recherchieren.**
> Quell-Adapter (TED, RSS-Portale, Offline-Fixture), einheitliches Datenmodell,
> Dublettenerkennung, Speicherung, Aenderungserkennung, Export und CLI.
> Die Stufen 2-6 (Dokumentenanalyse, Artikelextraktion, Preisrecherche,
> Kalkulation, Scoring, Dashboard) folgen danach - siehe
> [docs/architecture.md](docs/architecture.md).

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

### 3. Echte Recherche

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
```

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
| `fixture` | lokale JSON-Datei | Demo- und Testquelle, kein Netzwerk |

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
├── pipeline/              ingest.py (Lauforchestrierung), dedup.py
├── database/              SQLAlchemy-Modelle, Session, Repository, Alembic-Migrationen
└── export/                JSON / CSV / XLSX
tests/                     Offline-Tests (respx-Mocks)
config.yaml  .env.example  docs/architecture.md
```

Naechste Stufe: **Ausschreibungen analysieren** - Detailseiten und Unterlagen
laden, PDF/DOCX/XLSX auswerten, Anforderungen und Risiken erkennen.
Details in [docs/architecture.md](docs/architecture.md).

## Review und Roadmap

- [docs/ARCHITECTURE_REVIEW.md](docs/ARCHITECTURE_REVIEW.md) - evidenzbasierte
  Bestandsaufnahme von Stufe 1 (Findings mit Belegen, Messungen, Diskrepanzen
  zwischen Dokumentation und Code)
- [docs/OPTIMIZATION_ROADMAP.md](docs/OPTIMIZATION_ROADMAP.md) - priorisierte,
  direkt umsetzbare Tasks mit Akzeptanzkriterien und empfohlener Reihenfolge;
  Wellen 0-3 sind das Gate vor Beginn von Stufe 2
