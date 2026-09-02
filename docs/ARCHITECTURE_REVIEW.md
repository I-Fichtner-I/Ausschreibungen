# Architecture Review - tender-ai (Stand Stufe 1)

Stand: Commit `38a7054` (main, 2026-09-01) · Reviewer-Perspektive: Staff Engineer / Architekt
Begleitdokument: [OPTIMIZATION_ROADMAP.md](OPTIMIZATION_ROADMAP.md) (Tasks, Priorisierung, Reihenfolge)
Strategie und Stufenplan des Projekts: [architecture.md](architecture.md)

Alle Zeilenangaben beziehen sich auf den genannten Commit. Messwerte wurden in
dieser Umgebung reproduzierbar erhoben (Python 3.12.3, SQLite); die verwendeten
Skripte sind in Anhang H beschrieben.

---

## A. Executive Summary

**Aktueller Zustand.** Das Repository enthaelt Stufe 1 eines sechsstufigen
Procurement-Intelligence-Agenten: die automatisierte Ausschreibungsrecherche.
Rund 2.000 Zeilen Produktivcode (Python 3.12, httpx, Pydantic v2, SQLAlchemy
2.0, Typer) und rund 1.200 Zeilen Tests (74 Tests, 89 % Zeilenabdeckung).
Die Modulgrenzen sind sauber (Quellen, HTTP-Kern, Modelle, Pipeline,
Persistenz, Export, CLI), die Compliance-Regeln des Projekts (robots.txt,
Rate-Limit, keine Umgehung von Zugriffsschranken) sind an einer Stelle
implementiert. Es gibt **keine CI, keine Lint-/Typecheck-Konfiguration, keine
Schema-Migrationen, keine Lockdatei und keinen Container**.

**Wichtigste Erkenntnisse.**

1. **Datenverlust bei Persistenzfehlern (F-01, beobachtet).** Wirft `upsert`
   fuer einen einzelnen Datensatz eine Exception, verwirft der `rollback()`
   alle bereits geflushten, noch nicht committeten Datensaetze derselben
   Quelle - der Laufbericht meldet sie trotzdem als "neu". Reproduziert:
   Report `new=1`, Datenbank leer.
2. **Zwei Datenqualitaets-Bugs im Parsing (F-03, F-04, beobachtet).**
   `parse_amount("1234.50")` liefert `123450.0` (englischer Dezimalpunkt wird
   als Tausendertrennzeichen entfernt). Die RSS-Fristenerkennung akzeptiert das
   Wort `frist` allein und setzt damit *Bindefrist* und *Lieferfrist* als
   Angebotsfrist.
3. **Dublettenerkennung skaliert und trifft nicht zuverlaessig (F-02,
   beobachtet/gemessen).** Stufe 3 (Titelaehnlichkeit) vergleicht bis zu 500
   Kandidaten mit `difflib.SequenceMatcher` in Python: gemessen 56 ms pro
   neuem Datensatz bei 750 fremdquelligen Kandidaten; oberhalb des Caps von
   500 werden Dubletten schlicht nicht mehr gefunden.
4. **Kein Migrationspfad fuer das Schema (F-12, beobachtet).** `create_all`
   bei jedem Session-Start; die Stufen 2-6 werden das Schema erweitern und
   bestehende Datenbanken ohne Alembic brechen.
5. **Prozessluecken (F-13, F-14, F-15).** Keine CI, 98 ruff-Befunde, 5
   mypy-Fehler, Abhaengigkeiten doppelt deklariert und nur mit Untergrenzen.

**Groesste Risiken.** (1) Stiller Datenverlust und falsche Laufberichte
(F-01). (2) Falsche Geldbetraege fliessen unbemerkt in die spaetere
Profitabilitaetsrechnung (F-03). (3) Schemaaenderungen der naechsten Stufen
ohne Migrationspfad (F-12). (4) Fehlende CI: keiner der obigen Fixes ist
heute automatisch abgesichert (F-13).

**Groesste Chancen.** Die Codebasis ist klein, modular und gut getestet -
alle P0-/P1-Massnahmen sind lokal begrenzte Aenderungen (je 1-3 Dateien) und
in Summe an einem Arbeitstag umsetzbar. Ein Service-Layer (F-22), typisierte
Quellkonfigurationen (F-23) und Streaming-Downloads (F-18) *vor* Stufe 2
verhindern, dass sich die heutigen Muster in Dokumentenanalyse und Dashboard
vervielfachen.

---

## B. Repository- und Architekturuebersicht

### B.1 Struktur und Umfang

```
tender_ai/                     2.001 Statements (coverage-Zaehlung)
├── cli.py            538 LOC  Typer-CLI, 9 Befehle, Rich-Ausgabe
├── config.py         207 LOC  Settings: config.yaml + .env + Umgebung
├── core/                      HTTP-Client (227), Cache (82), robots.txt (79),
│                              Rate-Limit (42), Logging (50), Fehler (56)
├── models/                    Tender/Lot/Document/Requirements (207), Provenance (86)
├── sources/                   Interface + Registry (197/82), TED (319), RSS (208),
│                              Fixture (58), Parsing-Helfer (184)
├── pipeline/                  Ingest-Orchestrierung (272), Dedup (109)
├── database/                  ORM-Modelle (193), Repository (454), Session (53)
└── export/                    JSON/CSV/XLSX (119)
tests/                         11 Dateien, 74 Tests, respx-basiert (offline)
config.yaml · .env.example · pyproject.toml · requirements.txt
docs/architecture.md           Strategie, Stufenplan, Entscheidungen
data/fixtures/sample_tenders.json  Offline-Demoquelle
```

Nicht vorhanden: `.github/` (CI), `Dockerfile`, `alembic/`, Lockdatei,
`ruff`/`mypy`-Konfiguration, `CHANGELOG`.

### B.2 Sprachen, Frameworks, Abhaengigkeiten (installierte Versionen)

| Paket | Version | Rolle | Deklaration in `pyproject.toml` |
|---|---|---|---|
| Python | 3.12.3 | Laufzeit | `>=3.12` |
| httpx (+h2, httpcore 1.0.9) | 0.28.1 | HTTP-Client | `>=0.27` |
| pydantic / pydantic-core | 2.13.5 / 2.46.5 | Datenmodelle | `>=2.7` |
| pydantic-settings | 2.15.0 | Konfiguration | `>=2.2` |
| SQLAlchemy | 2.0.52 | ORM, SQLite/PostgreSQL | `>=2.0` |
| typer (bringt Click gebuendelt mit) | 0.27.2 | CLI | `>=0.12` |
| rich | 15.0.0 | Terminalausgabe | `>=13.7` |
| PyYAML | 6.0.3 | config.yaml | `>=6.0` |
| python-dotenv | 1.2.3 | `.env` | `>=1.0` |
| structlog | 26.1.0 | Logging | `>=24.1` |
| feedparser (+sgmllib3k) | 6.0.14 | RSS/Atom | `>=6.0` |
| openpyxl | 3.1.5 | XLSX-Export (optional) | `excel` extra |
| pytest / pytest-asyncio / respx / anyio | 9.1.1 / 1.4.0 / 0.23.1 / 4.14.2 | Tests | `dev` extra |

`pip-audit` (2026-09-01): **keine bekannten Schwachstellen** in den
installierten Paketen. Alle Deklarationen sind reine Untergrenzen; es gibt
keine Lockdatei (siehe F-15).

### B.3 Komponenten und Verantwortlichkeiten

| Komponente | Verantwortung | Abhaengigkeiten |
|---|---|---|
| `config.Settings` | Einlesen und Zusammenfuehren von config.yaml, `.env`, Umgebung; Pfade; Quell-Secrets | pydantic-settings, PyYAML |
| `core.http.HttpClient` | Einziger Netzwerkzugang: Retry/Backoff, `Retry-After`, Rate-Limit je Host, Response-Cache, robots.txt | httpx, `core.cache`, `core.robots`, `core.ratelimit` |
| `sources.base.TenderSource` | Abstraktes Quell-Interface (`search`, `get_tender_details`, `download_documents`, `health_check`); `SearchQuery` mit clientseitigem Filter | `models`, `core.http` |
| `sources.registry` | Typ-Registry, Instanziierung aus Konfiguration, Prioritaetssortierung | alle Adapter |
| `sources.ted / rss / fixture` | Konkrete Adapter; Mapping auf `Tender` | `sources.parsing` |
| `models.tender.Tender` | Kanonisches Datenmodell inkl. `fingerprint()` / `content_hash()` | pydantic |
| `pipeline.ingest.IngestService` | Parallele Suche, sequenzielle Persistenz, Laufbericht, Quellenstatus | `database.repository`, Quellen |
| `pipeline.dedup.DuplicateDetector` | Dreistufige Dublettenerkennung | SQLAlchemy-Session, `difflib` |
| `database.repository.TenderRepository` | Upsert mit Aenderungserkennung, Dubletten-Verknuepfung, Abfragen, Laufprotokolle | ORM-Modelle, `pipeline.dedup` |
| `database.session` | Engine-Cache, `create_all`, Session-Kontext | SQLAlchemy |
| `export.exporters` | JSON/CSV/XLSX mit `UNKNOWN`-Kennzeichnung | openpyxl (optional) |
| `cli` | Befehle, Ausgabeformatierung, **und** Aufbau von HTTP-Client/Quellen/IngestService | alles oben |

### B.4 Datenfluss eines Rechercherlaufs

```
CLI search ──> Settings ──> build_http_client ──> build_sources (nach Prioritaet)
    │
    └─> IngestService.run(query)
          ├─ asyncio.gather(source.search(query) ...)        parallel, je Quelle gekapselt
          │      └─ HttpClient.request ─ robots ─ rate-limit ─ retry ─ cache
          │      └─ Adapter._to_tender()  ->  Tender (+ raw, provenance)
          ├─ je Quelle (sortiert nach Prioritaet), je Tender:
          │      TenderRepository.upsert(tender)
          │        ├─ existiert (source, source_id)?  -> content_hash-Vergleich
          │        │      -> "unchanged" | "updated" (+ TenderChangeRecord je Feld)
          │        └─ sonst DuplicateDetector.find()
          │               1 national_id  2 fingerprint  3 Titel/Vergabestelle-Aehnlichkeit
          │               -> "new" | "duplicate" (+ Alias, Primaerquelle nach Prioritaet)
          │      session.commit() nach jeder Quelle
          ├─ SourceStateRecord je Quelle, IngestRunRecord fuer den Lauf
          └─ IngestReport -> CLI-Tabellen / --json / --export
```

Persistenz: SQLite (Default `data/tender_ai.db`) oder PostgreSQL ueber
`TENDER_AI_DATABASE_URL`. Tabellen: `tenders`, `tender_aliases`,
`tender_documents`, `tender_changes`, `ingest_runs`, `source_states`.
`tenders.payload` haelt das vollstaendige `Tender`-JSON inklusive `raw`.

### B.5 Externe Schnittstellen

| Schnittstelle | Art | Konfiguration | Status |
|---|---|---|---|
| TED Search API (`POST /v3/notices/search`) | offizielle JSON-API, Expert-Query | `sources.ted.*` in config.yaml, optionaler Key aus `.env` | implementiert, **live nicht verifiziert** (Netzsperre der Entwicklungsumgebung; in README und architecture.md dokumentiert) |
| service.bund.de RSS | oeffentlicher Feed | `sources.bund_rss.feeds[]` | implementiert, **live nicht verifiziert** |
| robots.txt je Host | GET vor erstem Abruf | `http.respect_robots` | implementiert |

Keine eigene Netzwerkoberflaeche (kein Server, keine API), daher heute keine
Authentifizierung/Autorisierung; relevant erst mit dem Dashboard (Stufe 6).

### B.6 Build, Laufzeit, Konfiguration, Deployment

- Installation: `pip install -e ".[dev]"`; Konsolenskript `tender-ai` ueber
  `pyproject.toml`. `requirements.txt` dupliziert die Deklaration (F-15).
- Konfigurationsprioritaet: Umgebung > `.env` > `config.yaml` > Defaults;
  umgesetzt ueber eine eigene `_YamlSettingsSource` (`config.py:106-123`).
- Automatisierung: cron-Beispiel in README; kein Scheduler-Prozess (bewusst,
  architecture.md §7).
- Kein Dockerfile, kein Release-Prozess; Version doppelt gepflegt
  (`pyproject.toml:7`, `tender_ai/__init__.py:9`).

### B.7 Tests und Teststrategie

74 Tests in 11 Dateien; offline gegen `respx`-Mocks; `asyncio_mode=auto`.
Abgedeckt: Parser, Modelle, HTTP-Regeln (Retry, `Retry-After`, robots,
Cache, Rate-Limit), alle drei Adapter, Repository (Upsert, Aenderungen,
Dubletten, Primaerquelle), Ingest (Quellausfall, Wiederholungslauf), Export,
CLI-Befehle.

Zeilenabdeckung 89 % gesamt; unter 80 %: `cli.py` 76 % (`show`-Tabellen,
`runs`), `sources/ted.py` 79 % (`get_tender_details`, `download_documents`
vollstaendig ungetestet, Zeilen 203-245). Keine Coverage-Schwelle, keine
CI-Ausfuehrung.

### B.8 Logging, Monitoring, Observability

structlog mit Konsolen- oder JSON-Renderer (`core/logging.py`); HTTP-
Statistiken je Lauf (`HttpStats`), Laufprotokoll und Quellenstatus in der
Datenbank. Keine Korrelation der Logzeilen zu einem Lauf (`run_id` fehlt),
keine Metriken, kein Health-Endpunkt (nicht noetig ohne Server).

### B.9 Dokumentation vs. Code - Diskrepanzen

| Stelle | Aussage | Befund |
|---|---|---|
| `core/http.py:139-142` Kommentar | "robots.txt darf strenger sein als unsere eigene Konfiguration" | Code setzt `1/crawl_delay` **unbedingt** und kann ein strengeres Quell-Limit lockern (F-09). |
| `README.md:140,195` | "74 Tests" | Zahl ist hart kodiert und driftet mit jedem Test (F-26). |
| `config.yaml:27` und `:107` | `search.min_days_until_deadline: 3` und `criteria.minimum_days_until_deadline: 3` | Dasselbe Konzept an zwei Stellen mit unterschiedlichen Code-Defaults (`config.py:50` = 0, `:85` = 3); `criteria`/`scoring` werden geladen, aber nirgends gelesen (F-19). |
| `.env.example` | `TENDER_AI_DTVP_API_KEY` | Platzhalter fuer eine nicht existierende Quelle; als solcher gekennzeichnet. Wuerde aber wegen F-05 aus `.env` gar nicht gelesen. |
| `architecture.md` §5 | "Wiederaufnahme abgebrochener Jobs" (Anforderung 24) | Nicht implementiert; `ingest_runs.finished_at` bleibt bei Abbruch `NULL`, es gibt keinen Resume-Pfad (F-20). |
| README "Was jetzt laeuft" | "Dubletten ueber Quellen hinweg (3-stufig ...)" | Korrekt, aber Stufe 3 ist auf 500 Kandidaten gedeckelt - nicht dokumentiert (F-02). |

### B.10 Relevante Architekturentscheidungen (aus architecture.md, verifiziert)

- Asynchrones Quell-Interface, ein zentraler HTTP-Client - eingehalten.
- Fehlende Werte bleiben `None`, Ausgabe `UNKNOWN` - konsequent umgesetzt
  (`models/common.py:display`, Export).
- Synchrone SQLAlchemy-Session innerhalb der asynchronen Pipeline
  (`pipeline/ingest.py`) - bewusst pragmatisch fuer SQLite, siehe F-11.
- TED-Aufrufe ohne robots.txt-Pruefung (`sources/ted.py`,
  `check_robots=False`) mit Begruendung im Code und in architecture.md §6.

---

## C. Findings

Statuslegende: **beobachtet** = im Code/durch Messung belegt ·
**wahrscheinlich** = aus belegtem Code abgeleitet, Auswirkung nicht gemessen ·
**potenziell** = tritt unter absehbaren Bedingungen ein (Wachstum, Stufe 2+) ·
**subjektiv** = Architekturpraeferenz.
Schweregrad: Hoch / Mittel / Niedrig.

### C.1 Korrektheit und Datenqualitaet

#### F-01 · Persistenzfehler verwirft bereits gespeicherte Datensaetze, Report meldet sie als neu
- **Kategorie:** Fehlerbehandlung / Zustandsverwaltung · **Status:** beobachtet · **Schweregrad:** Hoch
- **Beschreibung:** `IngestService.run` ruft `repository.upsert` je Tender und committet erst nach der ganzen Quelle. Bei einer Exception wird `session.rollback()` ausgefuehrt - das verwirft auch alle vorher geflushten Upserts derselben Quelle. Die Zaehler `new`/`updated` und `new_tender_ids` sind zu diesem Zeitpunkt bereits inkrementiert.
- **Evidenz:** `pipeline/ingest.py:196-205` (rollback), `:227` (commit nach Quelle). Reproduktion (Anhang H.3): Quelle liefert zwei Tender, der zweite Upsert wirft -> Report `new=1`, `count()==0`.
- **Betroffen:** `tender_ai/pipeline/ingest.py`
- **Auswirkung:** Stiller Datenverlust; falsche Laufberichte; spaetere Benachrichtigungen (Stufe 6) wuerden nicht existierende Datensaetze melden.
- **Empfehlung:** Savepoint je Datensatz (`session.begin_nested()`), Rollback nur auf den Savepoint; Zaehler erst nach erfolgreichem Flush erhoehen; Fehler je Datensatz im Report ausweisen. -> **T-01**

#### F-02 · Dublettenerkennung Stufe 3: Kandidaten-Cap und Python-Vergleich
- **Kategorie:** Performance / Korrektheit · **Status:** beobachtet (gemessen) · **Schweregrad:** Mittel heute, Hoch bei Wachstum
- **Beschreibung:** `DuplicateDetector.find` laedt fuer jeden neuen Tender ohne Nummern-/Fingerprint-Treffer bis zu 500 fremdquellige Datensaetze im Zeitfenster (`ORDER BY last_seen_at DESC LIMIT 500`) und vergleicht jeden mit `difflib.SequenceMatcher`.
- **Evidenz:** `pipeline/dedup.py:69-109` (Query `:85`, Schleife `:89`). Benchmark (Anhang H.2): 250 Fremdkandidaten -> 1,9 ms/Upsert; 750 Fremdkandidaten -> 56 ms/Upsert (Cap 500 greift). Datensaetze jenseits des Caps werden nie verglichen.
- **Betroffen:** `tender_ai/pipeline/dedup.py`, `tender_ai/database/models.py` (fehlende Blocking-Spalte/Index)
- **Auswirkung:** Ein Lauf mit 1.000 neuen Bekanntmachungen bei gefuellter Datenbank kostet ~1 Minute nur fuer Dedup; Dubletten werden ab 500 Kandidaten im Fenster systematisch uebersehen (Recall-Verlust), was Mehrfachanalysen und doppelte Benachrichtigungen nach sich zieht.
- **Empfehlung:** Blocking-Schluessel (normalisierte Vergabestelle + erste N Zeichen des normalisierten Titels) als indizierte Spalte; Kandidaten ueber Gleichheit des Blocking-Schluessels einschraenken; Cap entfernen oder auf Blocking-Gruppe beziehen; optional `rapidfuzz` statt `difflib`. -> **T-10**

#### F-03 · `parse_amount` interpretiert englische Dezimalpunkte als Tausendertrennzeichen
- **Kategorie:** Datenqualitaet · **Status:** beobachtet · **Schweregrad:** Hoch (Betraege sind Kern der spaeteren Kalkulation)
- **Beschreibung:** Alle Punkte werden entfernt, bevor die Zahl extrahiert wird.
- **Evidenz:** `sources/parsing.py:120-146`, Punkt-Entfernung in `:138`. Reproduktion: `parse_amount("1234.50") == 123450.0`, `parse_amount({"amount": "1234.50"}) == 123450.0`. TED liefert `amount` heute numerisch (Pfad `:130`), der Bug trifft String-Betraege aus RSS/HTML und kuenftigen Dokumenten.
- **Betroffen:** `tender_ai/sources/parsing.py`
- **Auswirkung:** Faktor-100-Fehler in `estimated_value`; in Stufe 4/5 in Preisen und Profitabilitaet.
- **Empfehlung:** Locale-bewusste Heuristik (letztes Trennzeichen entscheidet; "1.234,50" -> 1234.50; "1,234.50" -> 1234.50; "1234.50" -> 1234.50), mit Tabellen-Test. -> **T-02**

#### F-04 · RSS-Fristenerkennung setzt Binde-/Lieferfrist als Angebotsfrist
- **Kategorie:** Datenqualitaet · **Status:** beobachtet · **Schweregrad:** Mittel-Hoch
- **Beschreibung:** Das Regex-Alternativ `frist` matcht jedes Wort, das auf "frist" endet.
- **Evidenz:** `sources/rss.py:30-33`. Reproduktion: `"Bindefrist: 01.12.2026"` -> `submission_deadline=2026-12-01`; `"Lieferfrist: 30.11.2026"` -> `2026-11-30`. Der Hinweis in `notes` (`rss.py:157-161`) entschaerft, verhindert aber nicht, dass `--min-deadline-days` und die spaetere Deadline-Bewertung auf falschen Werten arbeiten.
- **Betroffen:** `tender_ai/sources/rss.py`
- **Auswirkung:** Falsch gefilterte oder falsch priorisierte Ausschreibungen; verlorene Chancen bzw. Fehlalarme.
- **Empfehlung:** Nur explizite Angebotsfrist-Begriffe zulassen; Binde-/Lieferfrist getrennt in `binding_period_end` / `delivery_deadline` ablegen (Felder existieren bereits in `Tender`). -> **T-03**

#### F-05 · API-Schluessel fuer Nicht-TED-Quellen werden aus `.env` nicht gelesen
- **Kategorie:** Konfiguration / Security · **Status:** beobachtet · **Schweregrad:** Mittel (latent, erst mit weiteren API-Quellen wirksam)
- **Beschreibung:** `secret_for_source` liest fuer alle Quellen ausser `ted` direkt `os.environ`; pydantic-settings laedt `.env` nur in deklarierte Felder, nicht in die Prozessumgebung.
- **Evidenz:** `config.py:189-194`. Reproduktion (Anhang H.4): `.env` mit `TENDER_AI_FOO_API_KEY` -> `secret_for_source("foo") is None`, waehrend `ted` funktioniert.
- **Betroffen:** `tender_ai/config.py`, `.env.example`
- **Auswirkung:** Die dokumentierte Secret-Handhabung funktioniert nur fuer eine Quelle; Entwickler wuerden Keys in `config.yaml` schreiben.
- **Empfehlung:** Generisches Feld `source_api_keys: dict[str, SecretStr]` ueber `TENDER_AI_SOURCE_API_KEYS__<name>` oder pro Quelle `api_key_env` in config.yaml mit Aufloesung ueber die Settings-Quellen. -> **T-04**

#### F-06 · Path-Traversal ueber `source_id` im Dokumentendownload
- **Kategorie:** Security · **Status:** beobachtet · **Schweregrad:** Mittel (Datenquelle heute vertrauenswuerdig; das Muster wird in Stufe 2 fuer alle Quellen wiederverwendet)
- **Beschreibung:** Der Zielpfad wird aus der Quell-ID gebildet, ohne sie zu bereinigen.
- **Evidenz:** `sources/ted.py:233`. Reproduktion: `source_id="../../evil"` -> Zielpfad `/tmp/evil.pdf` ausserhalb des Dokumentenverzeichnisses. Das Fixture-Format akzeptiert beliebige `source_id`-Strings (`sources/fixture.py:44-46`).
- **Betroffen:** `tender_ai/sources/ted.py`, kuenftig `sources/base.py`
- **Auswirkung:** Schreiben ausserhalb `data/documents` bei manipulierter Quelle.
- **Empfehlung:** Zentrale Hilfsfunktion `safe_document_path(base, source, source_id, suffix)` in `sources/base.py`: Zeichen auf `[A-Za-z0-9._-]` reduzieren, Laenge begrenzen, `resolved.is_relative_to(base)` erzwingen. -> **T-05**

#### F-07 · Rich-Markup aus Quelldaten wird in der CLI interpretiert
- **Kategorie:** Security (Ausgabe) · **Status:** beobachtet · **Schweregrad:** Niedrig
- **Beschreibung:** Titel, Vergabestelle, Beschreibung werden ungeschuetzt an Rich uebergeben; Markup wie `[bold red]` oder `[link=...]` wird ausgefuehrt.
- **Evidenz:** `cli.py:_tender_table` (Zeilen 84-119, `add_row` :104), `show` (Panel-Text Zeilen 373-392). Reproduktion: Titel `[bold red]MANIPULIERT[/]` wird nicht literal gerendert.
- **Betroffen:** `tender_ai/cli.py`
- **Auswirkung:** Irrefuehrende Terminalausgabe (Farben, versteckte Links) durch Quellinhalte.
- **Empfehlung:** `rich.markup.escape()` auf alle Fremdtexte in `display()`-Aufrufen fuer die Ausgabe bzw. `Table(..., markup=False)` mit expliziten `Text`-Objekten fuer eigene Formatierung. -> **T-06**

#### F-08 · TED-Status ignoriert `notice-type`; Vergabebekanntmachungen gelten als OPEN
- **Kategorie:** Datenqualitaet · **Status:** wahrscheinlich (Code belegt; Wirkung haengt von Live-Daten ab) · **Schweregrad:** Mittel
- **Beschreibung:** Status wird ausschliesslich aus der Frist abgeleitet; fehlt sie, ist der Status `OPEN`. Contract-Award-Notices (`can-*`) haben keine Angebotsfrist und wuerden als offene Ausschreibungen gefuehrt.
- **Evidenz:** `sources/ted.py:286`; `notice_type` wird gespeichert (`:283`), aber nicht ausgewertet.
- **Betroffen:** `tender_ai/sources/ted.py`, `tender_ai/models/tender.py` (`TenderStatus.AWARDED` existiert)
- **Auswirkung:** Rauschen in `list --open`, unnoetige Analysen in Stufe 2+.
- **Empfehlung:** Mapping `notice-type`-Praefix -> Status (`cn-*`/`pin-*` -> OPEN, `can-*` -> AWARDED, Aufhebung -> CANCELLED), Frist nur als Zusatzkriterium; Default `UNKNOWN` statt `OPEN`. -> **T-07**

#### F-09 · `Crawl-delay` kann ein strengeres Quell-Rate-Limit lockern
- **Kategorie:** Compliance / Korrektheit · **Status:** beobachtet · **Schweregrad:** Niedrig-Mittel
- **Beschreibung:** Der Kommentar verspricht "strenger", der Code setzt `1/crawl_delay` unbedingt - eine Quelle mit 0,5 req/s wird bei `Crawl-delay: 1` auf 1 req/s beschleunigt. Zusaetzlich umgeht der robots.txt-Abruf selbst Rate-Limiter und Retry.
- **Evidenz:** `core/http.py:139-142`; `core/robots.py:42` (direkter `client.get`).
- **Betroffen:** `tender_ai/core/http.py`, `tender_ai/core/robots.py`
- **Auswirkung:** Verstoss gegen die eigene Hoeflichkeitsregel; im Grenzfall 429-Antworten.
- **Empfehlung:** `configure_host(host, min(configured_rps, 1/crawl_delay))`; robots.txt ueber den regulaeren Request-Pfad (ohne robots-Pruefung) holen. -> **T-08**

#### F-10 · `show` filtert Aenderungen aus den letzten 200 statt per Query
- **Kategorie:** Korrektheit · **Status:** beobachtet · **Schweregrad:** Niedrig
- **Evidenz:** `cli.py:366` (`recent_changes(200)` + Python-Filter). Ab 200 Aenderungen in der Datenbank fehlen aeltere Eintraege der angezeigten Ausschreibung.
- **Betroffen:** `tender_ai/cli.py`, `tender_ai/database/repository.py`
- **Empfehlung:** `TenderRepository.changes_for(tender_id, limit)` mit `WHERE tender_id = ?`. -> **T-09**

### C.2 Architektur und Wartbarkeit

#### F-11 · Synchrone Datenbankzugriffe blockieren den Event-Loop
- **Kategorie:** Architektur / Performance · **Status:** beobachtet (Design), Auswirkung wahrscheinlich bei PostgreSQL · **Schweregrad:** Mittel
- **Beschreibung:** Upserts laufen synchron innerhalb der asynchronen `run()`; bei SQLite lokal vernachlaessigbar, bei PostgreSQL ueber Netz addiert sich jede Roundtrip-Latenz seriell (bei ~2 ms/Upsert lokal gemessen, netzabhaengig deutlich mehr).
- **Evidenz:** `pipeline/ingest.py:168-227`; `database/session.py` (sync Engine).
- **Betroffen:** `tender_ai/pipeline/ingest.py`, `tender_ai/database/session.py`
- **Empfehlung:** Persistenz einer Quelle als Block in `asyncio.to_thread` ausfuehren (Session ist threadgebunden, daher Session im Thread erzeugen) oder Batch-Upsert; async Engine erst, wenn ein Server (Stufe 6) parallel schreibt. -> **T-15**

#### F-12 · Keine Schema-Migrationen; `create_all` bei jedem Session-Start
- **Kategorie:** Betrieb / Wartbarkeit · **Status:** beobachtet · **Schweregrad:** Hoch fuer Stufe 2+
- **Evidenz:** `database/session.py:35-38` (`create_all`), `:42-44` (`session_scope` ruft `create_all` bei jedem Aufruf). Kein `alembic/`-Verzeichnis.
- **Betroffen:** `tender_ai/database/`
- **Auswirkung:** Jede Spaltenaenderung (Stufe 2: Anforderungen, Stufe 3: `tender_items`) bricht bestehende Datenbanken ohne Migrationspfad; `create_all` pro Befehl kostet einen Metadata-Roundtrip.
- **Empfehlung:** Alembic einfuehren, `create_all` nur in `init`/Tests, Migrationscheck in `session_scope`. -> **T-11**

#### F-13 · Keine CI
- **Kategorie:** Prozess · **Status:** beobachtet · **Schweregrad:** Hoch (Prozess)
- **Evidenz:** kein `.github/`-Verzeichnis; PR #1 wurde ohne Checks gemergt.
- **Empfehlung:** GitHub Actions: pytest mit Coverage-Schwelle, ruff, mypy, pip-audit. -> **T-12**

#### F-14 · Kein Lint-/Typecheck-Setup; 98 ruff-Befunde, 5 mypy-Fehler
- **Kategorie:** Codequalitaet · **Status:** beobachtet · **Schweregrad:** Mittel
- **Evidenz:** `ruff check` (Default-Regeln + UP/B/BLE/DTZ/SIM/C4/I): 98 Befunde, 54 automatisch behebbar; haeufigste: `UP017` (25, `datetime.UTC`), `UP045` (22, `Optional[...]`), `B008` (18, Typer-Konvention, zu ignorieren), `BLE001` (10, bewusste breite `except`), `DTZ011` (4, `date.today()` ohne Zeitzone). 12 Zeilen ueber 100 Zeichen. `mypy --ignore-missing-imports`: 5 Fehler (`parsing.py:45,49,55`; `ted.py:193`; `rss.py:150`). Keine Konfiguration in `pyproject.toml`.
- **Empfehlung:** ruff-Konfiguration mit begruendeten Ignores, Auto-Fixes, mypy-Fehler beheben, beides in CI. -> **T-13**, **T-14**

#### F-15 · Abhaengigkeiten doppelt deklariert, keine Lockdatei, nur Untergrenzen
- **Kategorie:** Dependencies / Build · **Status:** beobachtet · **Schweregrad:** Mittel
- **Evidenz:** `pyproject.toml:11-32` und `requirements.txt` (gleiche Liste, manuell synchron zu halten); keine `uv.lock`/`requirements*.txt` mit Pins.
- **Auswirkung:** Nicht reproduzierbare Installationen; ein Major-Update (z. B. typer buendelt seit 0.27 Click selbst) kann Tests unbemerkt brechen.
- **Empfehlung:** `pyproject.toml` als einzige Quelle, Lockdatei mit `uv lock` oder `pip-compile`; `requirements.txt` generiert. -> **T-16**

#### F-16 · API-Schluessel als Klartext-`str` in Settings (erscheint in `repr`)
- **Kategorie:** Security · **Status:** beobachtet · **Schweregrad:** Mittel
- **Evidenz:** `config.py:147-148` (`ted_api_key: str | None`); pytest-Ausgaben in dieser Session zeigten die vollstaendige `Settings(...)`-repr inklusive `ted_api_key=...`.
- **Auswirkung:** Schluessel landen in Tracebacks, Testlogs, Fehlerberichten.
- **Empfehlung:** `pydantic.SecretStr`; `get_secret_value()` nur in `TedSource._headers`. -> **T-04**

#### F-17 · HTTP-Cache ohne Eviction; Cache-Schluessel ignoriert Auth-Header
- **Kategorie:** Wartbarkeit / Ressourcen · **Status:** beobachtet · **Schweregrad:** Niedrig
- **Evidenz:** `core/cache.py:40-53` (TTL nur beim Lesen geprueft, abgelaufene Dateien bleiben liegen), `:27-35` (Key aus Methode, URL, Body).
- **Auswirkung:** `data/cache` waechst unbegrenzt; nach Wechsel des API-Keys koennen bis zu 15 Minuten alte Antworten des alten Kontexts geliefert werden.
- **Empfehlung:** Ablaufbereinigung beim Start bzw. `cache-clear --expired`, Groessenlimit, Auth-Header in den Key hashen. -> **T-17**

#### F-18 · Downloads vollstaendig im Speicher, ohne Groessen- oder Typlimit
- **Kategorie:** Robustheit / Performance · **Status:** potenziell (Stufe 2 laedt Vergabeunterlagen in Masse) · **Schweregrad:** Niedrig heute, Mittel Stufe 2
- **Evidenz:** `core/http.py:209-214` (`response.content` -> `write_bytes`), `:182` (Statistik ueber `len(content)`); kein `max_bytes`, kein Content-Type-Check. Feeds (feedparser/sgmllib3k) werden ebenfalls ungebremst geparst.
- **Empfehlung:** `client.stream()` mit `max_bytes` (Default 50 MB), Abbruch mit klarer Fehlermeldung; erwarteter Content-Type optional. -> **T-18**

#### F-19 · Doppelte Konfiguration desselben Konzepts; ungenutzte Bloecke
- **Kategorie:** Konfiguration / Dokumentation · **Status:** beobachtet · **Schweregrad:** Niedrig
- **Evidenz:** `config.yaml:27` vs. `:107`; `config.py:50` (Default 0) vs. `:85` (Default 3); `criteria`, `scoring` werden nirgends gelesen (grep ueber `tender_ai/`).
- **Empfehlung:** In Stufe 1 nur `search.min_days_until_deadline` aktiv halten, `criteria`/`scoring` in config.yaml als "ab Stufe 5" kommentieren und aus dem Beispiel auskommentieren oder in ein `criteria.enabled: false` stellen; README-Testzahl entfernen. -> **T-19**

#### F-20 · Keine Wiederaufnahme abgebrochener Laeufe
- **Kategorie:** Betrieb · **Status:** beobachtet (Luecke gegenueber Anforderung 24) · **Schweregrad:** Niedrig (Laeufe sind kurz und idempotent)
- **Evidenz:** `database/models.py:IngestRunRecord` hat `finished_at`, aber kein Status; `pipeline/ingest.py` markiert Abbrueche nicht.
- **Empfehlung:** `status` (`running`/`finished`/`aborted`) am Laufprotokoll, Kennzeichnung verwaister Laeufe beim Start; echtes Resume erst mit langlaufenden Stufen (Dokumente, Preise). -> **T-20**

#### F-21 · Keine Lauf-Korrelation in Logs
- **Kategorie:** Observability · **Status:** beobachtet · **Schweregrad:** Niedrig
- **Evidenz:** `core/logging.py` bindet `merge_contextvars`, aber niemand bindet `run_id`/`source`.
- **Empfehlung:** `structlog.contextvars.bind_contextvars(run_id=..., source=...)` in `IngestService.run` / `_search_one`. -> **T-21**

#### F-22 · CLI enthaelt Orchestrierung, die das Dashboard duplizieren muesste
- **Kategorie:** Architektur · **Status:** potenziell / subjektiv · **Schweregrad:** Mittel (vor Stufe 6)
- **Evidenz:** `cli.py:259-276` (`search`, innere `_run`): Aufbau von HTTP-Client, Quellen, `IngestService`, Session-Handling und Fehlerbehandlung innerhalb des Typer-Befehls; `doctor` wiederholt das Muster (`:197-207`).
- **Empfehlung:** `tender_ai/services/` mit `run_search(settings, query, *, sources, store, download)` und `check_sources(settings, only)`; CLI und spaeteres FastAPI/Streamlit rufen dieselben Funktionen. -> **T-22**

#### F-23 · Untypisierte Quellkonfiguration
- **Kategorie:** Wartbarkeit · **Status:** subjektiv · **Schweregrad:** Niedrig-Mittel
- **Evidenz:** `config.py:SourceConfig` mit `extra="allow"`; Adapter lesen `getattr(self.config, "page_size", 50)` (`ted.py:69-79`, `rss.py:43`). Ein Tippfehler in `config.yaml` (`page_sze`) faellt nicht auf.
- **Empfehlung:** Discriminated Union `TedSourceConfig | RssSourceConfig | FixtureSourceConfig` ueber das Feld `type`; `extra="forbid"`. -> **T-23**

### C.3 Tests, Build, Betrieb

#### F-24 · Testluecken und fehlende Coverage-Schwelle
- **Status:** beobachtet · **Schweregrad:** Mittel
- **Evidenz:** Coverage-Report (Anhang H.1): `ted.py` 203-245 (`get_tender_details`, `download_documents`) ungetestet; `cli.py` `show`-Tabellen/`runs` ungetestet; `cache.clear`, `robots.crawl_delay`, `registry`-Fehlerpfade ungetestet. Kein `--cov-fail-under`.
- **Empfehlung:** Tests fuer die genannten Pfade; Schwelle 85 % in CI. -> **T-24**

#### F-25 · Kein Container, Version doppelt gepflegt
- **Status:** beobachtet · **Schweregrad:** Niedrig
- **Evidenz:** kein `Dockerfile`; `pyproject.toml:7` und `tender_ai/__init__.py:9` beide `0.1.0`.
- **Empfehlung:** Schlankes Dockerfile (python:3.12-slim, non-root, `data/` als Volume); Version aus `importlib.metadata`. -> **T-25**

#### F-26 · Hart kodierte Testanzahl in README
- **Status:** beobachtet · **Schweregrad:** Niedrig · **Evidenz:** `README.md:140,195`. -> in **T-19**

#### F-27 · SQLite ohne WAL/`busy_timeout`
- **Kategorie:** Betrieb · **Status:** potenziell (cron-Lauf und interaktive CLI gleichzeitig) · **Schweregrad:** Niedrig-Mittel
- **Evidenz:** `database/session.py:21-27` (nur `check_same_thread=False`).
- **Empfehlung:** `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000; PRAGMA foreign_keys=ON` per `connect`-Event. -> **T-26**

#### F-28 · Redundante Speicherung: `payload` enthaelt `raw` und alle Spaltenwerte
- **Status:** beobachtet, Auswirkung potenziell · **Schweregrad:** Niedrig
- **Evidenz:** `database/repository.py:122` (`payload=_json_ready(tender.model_dump())`), `Tender.raw` (`models/tender.py:131`).
- **Empfehlung:** `raw` in eigene Spalte/Tabelle, optional komprimiert; erst relevant, wenn Volumen messbar wird (Stufe 2 speichert Dokumenttexte). -> **T-27**

#### F-29 · Dependency-Risiko feedparser/sgmllib3k (XML-Entities)
- **Kategorie:** Security · **Status:** potenziell, **nicht verifiziert** · **Schweregrad:** Niedrig
- **Beschreibung:** feedparser 6.x nutzt sgmllib3k und `xml.sax`; ob externe Entities/Entity-Expansion deaktiviert sind, wurde hier nicht geprueft.
- **Empfehlung:** Groessenlimit fuer Feed-Antworten (T-18) als Mitigation; Verifikation gegen eine Billion-laughs-Testdatei im Rahmen von T-24.

#### F-30 · Kein Auth-Konzept fuer die Dashboard-Stufe
- **Status:** potenziell · **Schweregrad:** n/a heute
- **Hinweis:** Sobald FastAPI/Streamlit hinzukommt, sind Nutzerentscheidungen (`tenders.user_decision`) und manuelle Kosteneingaben schreibende Aktionen -> Auth, CSRF und Audit-Trail muessen mit dem ersten Endpunkt kommen, nicht danach. Kein Task in dieser Roadmap; Design-Note fuer Stufe 6.

---

## H. Anhang: Messungen und Reproduktionen

Alle Messungen mit `.venv` (Python 3.12.3) auf Commit `38a7054`.

### H.1 Coverage (`pytest --cov=tender_ai --cov-report=term-missing`)

```
TOTAL                          2001 stmts   217 miss   89%
cli.py                          273          66        76%
sources/ted.py                  151          31        79%   (203-222, 228-245)
sources/parsing.py              134          25        81%
core/cache.py                    55          11        80%
```

### H.2 Dedup-Benchmark (Anhang-Skript: 3 Runden je 250/500/1000 Datensaetze, zwei Quellen)

| Kandidaten anderer Quelle im Zeitfenster | ms pro Upsert (Stufe 3 erreicht) |
|---|---|
| 250 | 1,9 |
| 750 (Cap 500 greift) | 56,0 |

Upserts, die in Stufe 2 (Fingerprint, indiziert) treffen, liegen konstant bei
~2,3 ms.

**Nach T-10** (Blocking-Schluessel, indiziert): 2,2 ms je Upsert bei 1000
fremdquelligen Kandidaten - Faktor 25. Der Messwert wird als Test
(`tests/test_dedup_scaling.py`, Marker `slow`) gegen eine Obergrenze von 5 ms
geprueft, damit die Regression auffaellt.

### H.3 Reproduktion F-01

Quelle liefert `two:ok` und `two:bad`; `upsert` wirft fuer `bad`. Ergebnis:
`IngestReport.new == 1`, `TenderRepository.count(only_primary=False) == 0`.

### H.4 Reproduktion F-05

`.env` mit `TENDER_AI_TED_API_KEY=...` und `TENDER_AI_FOO_API_KEY=...`;
`secret_for_source("ted")` liefert den Wert, `secret_for_source("foo")` liefert
`None`.

### H.5 Statische Analyse

- `ruff check tender_ai tests` (Regeln E,F,W,UP,B,BLE,DTZ,SIM,C4,I,ISC,PYI,FURB): 98 Befunde, 54 fixbar.
- `mypy tender_ai --ignore-missing-imports`: 5 Fehler in 3 Dateien.
- `pip-audit`: keine bekannten Schwachstellen.
