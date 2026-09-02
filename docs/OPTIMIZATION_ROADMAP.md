# Optimization Roadmap - tender-ai

Stand: Commit `38a7054` · Grundlage: [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md) (Findings F-01 … F-30)
Stufenplan des Produkts (Stufe 2-6): [architecture.md](architecture.md)

Diese Roadmap ist fuer Coding-Agents geschrieben: jeder Task nennt Dateien,
die konkrete Aenderung, Tests und messbare Akzeptanzkriterien. Kein Task
aendert Fachlogik ueber das Beschriebene hinaus. Geschaetzter Aufwand:
S = < 2 h, M = halber Tag, L = 1-2 Tage.

## Umsetzungsstand

| Welle | Tasks | Status |
|---|---|---|
| 0 | T-12 CI, T-13 ruff, T-16 Lockdatei, T-14 mypy (vorgezogen) | **erledigt** |
| 1 | T-01, T-02, T-03, T-04, T-05, T-08 | **erledigt** |
| 2 | T-07 TED-Status | **erledigt** (vorgezogen, gleiche Datei wie T-04/T-05) |
| 2 | T-26 SQLite-PRAGMAs, T-11 Alembic, T-09 changes_for, T-06 Markup-Escape | **erledigt** |
| 3 | T-10, T-24 (Coverage-Schwelle steht auf 80 %, Ist 91 %) | offen |
| 4-5 | T-22, T-15, T-23, T-18, T-17, T-19, T-21, T-25, T-20, T-27 | offen |

Nachweise der erledigten Tasks: `.github/workflows/ci.yml`, `uv.lock`,
`requirements*.txt`, Tests `test_ingest.py::test_failed_upsert_keeps_other_records`
(T-01), `test_parsing.py::test_parse_amount_table` (T-02),
`test_source_rss.py::test_deadline_kinds_are_separated` (T-03),
`test_config.py` (T-04), `test_paths.py` (T-05),
`test_source_ted.py::test_status_is_derived_from_notice_type` (T-07),
`test_http.py::test_crawl_delay_only_tightens_rate_limit` (T-08),
`test_migrations.py` (T-11, T-26),
`test_repository.py::test_changes_for_returns_only_that_tender` (T-09),
`test_cli.py::test_markup_in_source_data_is_escaped` (T-06).

---

## D. Priorisierte Roadmap

| ID | Prio | Bereich | Aufgabe | Impact | Effort | Risk | Abhaengigkeiten |
|---|---|---|---|---|---|---|---|
| T-12 | P0 | CI/CD | GitHub-Actions-Workflow: pytest + Coverage, ruff, mypy, pip-audit | Hoch | S | Niedrig | - |
| T-01 | P0 | Korrektheit | Savepoint je Upsert, korrekte Zaehler, Fehler je Datensatz im Report (F-01) | Hoch | S | Niedrig | - |
| T-02 | P0 | Datenqualitaet | `parse_amount` locale-bewusst (F-03) | Hoch | S | Niedrig | - |
| T-03 | P0 | Datenqualitaet | RSS-Fristenerkennung nur fuer Angebotsfristen; Binde-/Lieferfrist getrennt (F-04) | Hoch | S | Niedrig | - |
| T-13 | P1 | Codequalitaet | ruff-Konfiguration + Auto-Fixes, begruendete Ignores (F-14) | Mittel | S | Niedrig | T-12 |
| T-16 | P1 | Dependencies | pyproject als einzige Quelle, Lockdatei, generierte requirements (F-15) | Mittel | S | Niedrig | T-12 |
| T-04 | P1 | Security/Config | Secrets als `SecretStr`; generische Quell-Keys aus `.env` (F-05, F-16) | Mittel | S | Niedrig | - |
| T-05 | P1 | Security | Sichere Dokumentpfade (`safe_document_path`) (F-06) | Mittel | S | Niedrig | - |
| T-08 | P1 | Compliance | `Crawl-delay` nur verschaerfend; robots.txt ueber Request-Pfad (F-09) | Mittel | S | Niedrig | - |
| T-07 | P1 | Datenqualitaet | TED-Status aus `notice-type` ableiten (F-08) | Mittel | S | Niedrig | - |
| T-10 | P1 | Performance/Korrektheit | Dedup-Blocking-Schluessel + Index, Cap entfernen (F-02) | Hoch | M | Mittel | T-11 (Migration) |
| T-11 | P1 | Betrieb | Alembic-Migrationen, `create_all` nur in `init`/Tests (F-12) | Hoch | M | Mittel | T-26 |
| T-26 | P1 | Betrieb | SQLite-PRAGMAs (WAL, busy_timeout, foreign_keys) (F-27) | Mittel | S | Niedrig | - |
| T-24 | P1 | Tests | Testluecken schliessen, Coverage-Schwelle 85 % (F-24, F-29) | Mittel | M | Niedrig | T-12, T-01…T-08 |
| T-06 | P2 | Security | Rich-Markup aus Quelldaten escapen (F-07) | Niedrig | S | Niedrig | - |
| T-09 | P2 | Korrektheit | `changes_for(tender_id)` statt Python-Filter (F-10) | Niedrig | S | Niedrig | - |
| T-14 | P2 | Codequalitaet | mypy-Fehler beheben, mypy in CI (F-14) | Mittel | S | Niedrig | T-13 |
| T-18 | P2 | Robustheit | Streaming-Download mit `max_bytes`, Content-Type-Check (F-18) | Mittel | S | Niedrig | T-05 |
| T-22 | P2 | Architektur | Service-Layer `tender_ai/services/` fuer Suche und Doctor (F-22) | Mittel | M | Niedrig | T-01 |
| T-23 | P2 | Wartbarkeit | Typisierte Quellkonfigurationen, `extra="forbid"` (F-23) | Mittel | M | Mittel | T-04 |
| T-15 | P2 | Performance | Persistenz ausserhalb des Event-Loops (F-11) | Mittel | M | Mittel | T-01, T-22 |
| T-17 | P2 | Ressourcen | Cache-Eviction, Auth-Header im Cache-Key (F-17) | Niedrig | S | Niedrig | - |
| T-19 | P2 | Konfiguration/Doku | Konfigurationsdoppelung bereinigen, README-Testzahl entfernen (F-19, F-26) | Niedrig | S | Niedrig | - |
| T-21 | P2 | Observability | `run_id`/`source` in Log-Kontext binden (F-21) | Niedrig | S | Niedrig | - |
| T-25 | P2 | Betrieb | Dockerfile, Version aus Paketmetadaten (F-25) | Niedrig | S | Niedrig | T-16 |
| T-20 | P3 | Betrieb | Laufstatus `running/finished/aborted`, verwaiste Laeufe markieren (F-20) | Niedrig | S | Niedrig | T-11 |
| T-27 | P3 | Ressourcen | `raw` aus `payload` auslagern (F-28) | Niedrig | M | Mittel | T-11 |

**Quick Wins** (S, sofort, unabhaengig): T-12, T-01, T-02, T-03, T-04, T-05, T-06, T-08, T-09, T-13, T-16, T-26.
**Mittelfristig** (M): T-07*, T-10, T-11, T-24, T-18, T-22, T-23, T-15.
**Strukturell**: T-11 (Migrationen), T-22 (Service-Layer), T-23 (typisierte Konfiguration) - alle drei *vor* Beginn von Stufe 2 bzw. Stufe 6.

\* T-07 ist S im Aufwand, braucht aber Live-Daten zur Verifikation.

---

## E. Detaillierte Tasks

### T-12 · CI-Workflow (P0)

- **Ziel / Problem:** Kein automatischer Nachweis, dass Tests, Lint und Typecheck bestehen (F-13). Alle folgenden Tasks brauchen dieses Sicherheitsnetz.
- **Dateien:** neu `.github/workflows/ci.yml`; `pyproject.toml` (`[tool.pytest.ini_options]` um `addopts` fuer Coverage ergaenzen ist optional).
- **Aenderung:** Workflow auf `push` (main) und `pull_request`: Python 3.12 (Matrix 3.12/3.13 optional), `pip install -e ".[dev]"` plus `ruff`, `mypy`, `pip-audit`; Schritte `ruff check .`, `ruff format --check .`, `mypy tender_ai`, `pytest -q --cov=tender_ai --cov-fail-under=80`, `pip-audit`. pip-Cache aktivieren.
- **Vorgehen:** Bis T-13/T-14 erledigt sind, ruff und mypy mit `continue-on-error: true` markieren, damit der Workflow sofort gruen ist und die Befunde sichtbar bleiben; nach T-13/T-14 den Schalter entfernen. Coverage-Schwelle zunaechst 80 % (Ist 89 %), in T-24 auf 85 % anheben.
- **Abhaengigkeiten:** keine.
- **Ergebnis:** Gruener Check auf jedem PR.
- **Akzeptanzkriterien:** (1) Workflow laeuft auf einem PR durch. (2) Ein absichtlich fehlschlagender Test macht den Check rot. (3) Laufzeit < 3 Minuten.
- **Tests:** keine Codeaenderung; Nachweis ueber den Workflow-Lauf.
- **Risiken/Hinweise:** `pip-audit` braucht Netzzugang zu PyPI/OSV; bei Sandbox-Runnern ggf. mit `continue-on-error`. Keine Secrets im Workflow noetig.

### T-01 · Savepoints und korrekte Zaehler im Ingest (P0)

- **Ziel / Problem:** Ein fehlschlagender `upsert` verwirft die vorher geflushten Datensaetze derselben Quelle; Report zaehlt sie trotzdem (F-01, reproduziert in Review H.3).
- **Dateien:** `tender_ai/pipeline/ingest.py` (Zeilen 168-227); `tender_ai/database/repository.py` (`UpsertResult` unveraendert); `tests/test_ingest.py`.
- **Aenderung:**
  1. In der Tender-Schleife jeden Upsert in `with self.session.begin_nested():` kapseln; bei Exception nur der Savepoint wird zurueckgerollt (`begin_nested` erledigt das beim Verlassen mit Exception).
  2. Zaehler (`new`, `updated`, `duplicates`, `unchanged`) und `new_tender_ids`/`updated_tender_ids` erst **nach** erfolgreichem Verlassen des Savepoints erhoehen.
  3. Neues Feld `SourceReport.failed: int` und `SourceReport.failed_ids: list[str]`; `IngestReport.errors` um Datensatzfehler (`{"source", "tender_id", "error"}`) erweitern; in `IngestReport.as_dict()` und in der CLI-Tabelle (`_print_source_reports`) eine Spalte "Fehlgeschlagen".
  4. Den bisherigen `self.session.rollback()` entfernen.
- **Vorgehen:** Zuerst den Reproduktionsfall aus Review H.3 als Test anlegen (rot), dann implementieren.
- **Abhaengigkeiten:** keine.
- **Ergebnis:** Ein fehlerhafter Datensatz kostet genau diesen Datensatz.
- **Akzeptanzkriterien:** (1) Test: Quelle liefert `ok`, `bad`, `ok2`; `bad` wirft im Upsert -> DB enthaelt `ok` und `ok2`, `report.new == 2`, `report.sources[0].failed == 1`, `report.errors` enthaelt `two:bad`. (2) Bestehende 74 Tests gruen. (3) `runs`-Befehl zeigt fehlgeschlagene Datensaetze im Laufprotokoll (`IngestRunRecord.errors`).
- **Tests:** `tests/test_ingest.py::test_failed_upsert_keeps_other_records`, Erweiterung `test_broken_source_does_not_stop_run` um Zaehlerpruefung.
- **Risiken/Hinweise:** SQLite unterstuetzt Savepoints; `begin_nested()` erfordert, dass keine `autobegin`-Konflikte entstehen - die Session ist im Standardmodus, `flush()` innerhalb des Savepoints ist erlaubt. Nicht `session.commit()` innerhalb der Schleife aufrufen.

### T-02 · `parse_amount` locale-bewusst (P0)

- **Ziel / Problem:** `"1234.50"` -> `123450.0` (F-03).
- **Dateien:** `tender_ai/sources/parsing.py` (`parse_amount`, Zeilen 120-146); `tests/test_parsing.py`.
- **Aenderung:** Neue Hilfsfunktion `_normalize_decimal(text) -> str`: Waehrungszeichen/Leerzeichen entfernen; wenn sowohl `.` als auch `,` vorkommen, ist das **letzte** Vorkommen das Dezimaltrennzeichen, das andere wird entfernt; kommt nur eines vor und danach folgen genau 1-2 Ziffern, ist es Dezimaltrennzeichen, bei genau 3 Ziffern und weiteren Gruppen (z. B. `1.234.567`) Tausendertrennzeichen, sonst (z. B. `1.234`) **mehrdeutig** -> als Tausender interpretieren, aber nur wenn Gruppenlaenge 3; `parse_amount` gibt bei nicht aufloesbarer Mehrdeutigkeit weiterhin einen Wert zurueck, jedoch soll eine zweite Funktion `parse_amount_with_confidence(text) -> tuple[float | None, int]` die Konfidenz (100 eindeutig, 60 mehrdeutig) liefern, damit Stufe 4 sie nutzen kann.
- **Vorgehen:** Tabellen-Test zuerst schreiben.
- **Abhaengigkeiten:** keine.
- **Akzeptanzkriterien:** Tabelle muss exakt gelten: `"1234.50"`->1234.5, `"1,234.50"`->1234.5, `"1.234,50"`->1234.5, `"1.234.567"`->1234567, `"1 234 567,89"`->1234567.89, `"EUR 420000"`->420000, `"420.000 EUR"`->420000, `{"amount": 1234.5}`->1234.5, `"abc"`->None, `"12,5 %"`->12.5. Konfidenz: `"1.234"`->60, `"1.234,50"`->100.
- **Tests:** `tests/test_parsing.py::test_parse_amount_table` (parametrisiert).
- **Risiken/Hinweise:** Keine Aenderung der Signatur von `parse_amount`; TED-Aufrufer (`ted.py`) bleiben unveraendert.

### T-03 · RSS-Fristenerkennung praezisieren (P0)

- **Ziel / Problem:** `frist` matcht Binde-/Lieferfrist (F-04).
- **Dateien:** `tender_ai/sources/rss.py` (`_DEADLINE_PATTERN` Zeilen 30-33, `_extract_deadline` Zeilen 194-208, `_to_tender`); `tests/test_source_rss.py`.
- **Aenderung:** Drei Muster: `_SUBMISSION_PATTERN` (`angebotsfrist|abgabefrist|einreichungsfrist|teilnahmefrist|schlusstermin|angebotsabgabe bis|abgabe bis`), `_BINDING_PATTERN` (`bindefrist|zuschlagsfrist`), `_DELIVERY_PATTERN` (`lieferfrist|ausfuehrungsfrist|leistungszeitraum`). `_extract_deadline` wird zu `_extract_dates(*texts) -> dict[str, tuple[datetime|date, str]]`. `_to_tender` fuellt `submission_deadline`, `binding_period_end`, `delivery_deadline` getrennt; Hinweis in `notes` nur, wenn mindestens ein Datum extrahiert wurde, mit Nennung des Feldes.
- **Abhaengigkeiten:** keine.
- **Akzeptanzkriterien:** `"Bindefrist: 01.12.2026"` -> `submission_deadline is None`, `binding_period_end == date(2026,12,1)`; `"Lieferfrist: 30.11.2026"` -> `delivery_deadline` gesetzt, `submission_deadline is None`; `"Angebotsfrist: 15.10.2036"` -> `submission_deadline` gesetzt (bestehender Test bleibt gruen); Text mit allen drei Begriffen -> alle drei Felder korrekt; `provenance.original_text` enthaelt den Angebotsfrist-Treffer.
- **Tests:** `tests/test_source_rss.py::test_deadline_kinds_are_separated` (parametrisiert), bestehende Tests.
- **Risiken/Hinweise:** `Tender.binding_period_end` und `delivery_deadline` sind `date`, `submission_deadline` ist `datetime` - Typen beachten. Keine Aenderung an `SearchQuery.matches`.

### T-04 · Secrets als `SecretStr`, generische Quell-Keys (P1)

- **Ziel / Problem:** Nicht-TED-Keys werden aus `.env` nicht gelesen; Keys erscheinen in `repr` (F-05, F-16).
- **Dateien:** `tender_ai/config.py` (Zeilen 147-194), `tender_ai/sources/ted.py` (`_headers`, `__init__`), `.env.example`, `tests/test_config.py` (neu), `tests/test_source_ted.py::test_api_key_is_sent_as_header`.
- **Aenderung:** `ted_api_key: SecretStr | None`; neues Feld `source_api_keys: dict[str, SecretStr] = {}` (Umgebung: `TENDER_AI_SOURCE_API_KEYS='{"dtvp": "..."}'` als JSON **oder** je Quelle `TENDER_AI_SOURCE_API_KEYS__DTVP=...` ueber `env_nested_delimiter`). `secret_for_source(name)` liefert `SecretStr | None`: erst `source_api_keys[name]`, dann fuer `ted` das Legacy-Feld. `TedSource._headers` nutzt `.get_secret_value()`. `.env.example` entsprechend anpassen; `TENDER_AI_DTVP_API_KEY` entfernen.
- **Abhaengigkeiten:** keine.
- **Akzeptanzkriterien:** (1) Test: `.env` mit `TENDER_AI_SOURCE_API_KEYS__FOO=geheim` -> `secret_for_source("foo").get_secret_value() == "geheim"`. (2) `repr(settings)` enthaelt weder `geheim` noch den TED-Key (`**********`). (3) TED-Header-Test bleibt gruen. (4) Kein Vorkommen von `os.environ.get` fuer Secrets in `config.py`.
- **Tests:** `tests/test_config.py` (neu, nutzt `monkeypatch.chdir(tmp_path)` und schreibt `.env`).
- **Risiken/Hinweise:** pydantic-settings parst `dict`-Felder aus Umgebungsvariablen als JSON; das nested-Delimiter-Format fuer Dict-Eintraege ist ebenfalls unterstuetzt - beide Varianten testen.

### T-05 · Sichere Dokumentpfade (P1)

- **Ziel / Problem:** Path-Traversal ueber `source_id` (F-06).
- **Dateien:** `tender_ai/sources/base.py` (neue Funktion), `tender_ai/sources/ted.py:233`, `tests/test_source_ted.py` oder neu `tests/test_paths.py`.
- **Aenderung:** `safe_document_path(base: Path, source: str, source_id: str, suffix: str) -> Path`: `source`/`source_id` auf `[A-Za-z0-9._-]` reduzieren (andere Zeichen -> `_`), fuehrende Punkte entfernen, Laenge auf 120 Zeichen kuerzen, bei Kollision/Leerstring SHA-256-Praefix anhaengen; `resolved = (base/source/f"{clean}{suffix}").resolve()`; `if not resolved.is_relative_to(base.resolve()): raise ValueError`. `TedSource.download_documents` nutzt die Funktion.
- **Abhaengigkeiten:** keine.
- **Akzeptanzkriterien:** `safe_document_path(Path("/d"), "ted", "../../evil", ".pdf")` liegt unter `/d/ted/`; `"00123456-2026"` bleibt unveraendert; `"a/b"` wird `a_b`; leerer String erzeugt einen deterministischen Namen; TED-Download-Test schreibt unter `documents_dir`.
- **Tests:** parametrisierter Test der Funktion; TED-Download-Test mit respx (siehe T-24).
- **Risiken/Hinweise:** Stufe 2 muss dieselbe Funktion fuer alle Quellen verwenden - in `TenderSource.download_documents`-Docstring verweisen.

### T-06 · Rich-Markup escapen (P2)

- **Dateien:** `tender_ai/cli.py` (`_tender_table`, `show`, `_print_source_reports`), `tests/test_cli.py`.
- **Aenderung:** Helfer `_safe(value) -> str` = `rich.markup.escape(display(value))`; ueberall, wo Quelltexte in Tabellen/Panels landen, verwenden. Eigene Formatierung (`[green]OK[/green]`) bleibt.
- **Akzeptanzkriterien:** CLI-Test mit Fixture-Titel `[bold red]X[/]` zeigt die Zeichenfolge literal in `result.stdout`; keine Rich-`MarkupError` bei Titel `[unclosed`.
- **Tests:** `tests/test_cli.py::test_markup_in_source_data_is_escaped` (eigene Fixture-Datei im Test).

### T-07 · TED-Status aus `notice-type` (P1)

- **Dateien:** `tender_ai/sources/ted.py` (`_to_tender`, Zeile 286; neue Funktion `_status_from(notice_type, deadline)`), `tests/test_source_ted.py`.
- **Aenderung:** Mapping nach Praefix des eForms-`notice-type`: `cn-`, `pin-`, `qs-`, `subco` -> OPEN (falls Frist vorhanden und abgelaufen -> CLOSED); `can-` -> AWARDED; `corr`/`change` -> AMENDED; unbekannt/fehlend -> UNKNOWN (nicht mehr OPEN). Mapping als Modul-Konstante `NOTICE_TYPE_STATUS`, per `config.yaml` (`sources.ted.status_map`) ueberschreibbar, da die eForms-Codes versioniert sind.
- **Akzeptanzkriterien:** Tests fuer `cn-standard` mit Frist in der Zukunft (OPEN), `cn-standard` mit Frist in der Vergangenheit (CLOSED), `can-standard` ohne Frist (AWARDED), fehlender Typ ohne Frist (UNKNOWN). `list --open` filtert weiterhin ueber die Frist, `status` zusaetzlich sichtbar.
- **Risiken/Hinweise:** Die genauen `notice-type`-Werte muessen nach dem ersten Live-Lauf (`tender-ai doctor`, `search --json`) gegen echte Daten geprueft werden; deshalb konfigurierbar.

### T-08 · `Crawl-delay` nur verschaerfend, robots.txt ueber Request-Pfad (P1)

- **Dateien:** `tender_ai/core/http.py` (Zeilen 139-142), `tender_ai/core/ratelimit.py` (neue Methode `tighten_host(host, rps)`), `tender_ai/core/robots.py` (`_load`), `tests/test_http.py`.
- **Aenderung:** `RateLimiter.tighten_host(host, rps)` setzt nur, wenn das neue Intervall groesser (langsamer) ist. `HttpClient` ruft `tighten_host` statt `configure_host`. `RobotsGuard._load` erhaelt einen Callable fuer den Abruf (`fetch: Callable[[str], Awaitable[httpx.Response]]`), den `HttpClient` mit `self.request("GET", url, check_robots=False, use_cache=True)` belegt - damit gelten Rate-Limit, Retry und Cache auch fuer robots.txt.
- **Akzeptanzkriterien:** Test: Quelle 0,5 req/s, robots `Crawl-delay: 1` -> effektives Intervall bleibt 2 s; Quelle 2 req/s, `Crawl-delay: 1` -> Intervall 1 s. Test: robots.txt-Abruf zaehlt in `HttpStats.requests`. Kommentar in `http.py` stimmt mit dem Verhalten ueberein.
- **Risiken/Hinweise:** Rekursion vermeiden - der robots-Abruf darf keine robots-Pruefung ausloesen (`check_robots=False`).

### T-09 · `changes_for(tender_id)` (P2)

- **Dateien:** `tender_ai/database/repository.py` (neue Methode neben `recent_changes`), `tender_ai/cli.py:366`, `tests/test_repository.py`.
- **Aenderung:** `changes_for(tender_id: str, limit: int = 50) -> list[TenderChangeRecord]` mit `WHERE tender_id = :id ORDER BY detected_at DESC LIMIT :limit`; `show` nutzt sie.
- **Akzeptanzkriterien:** Test: 250 Aenderungen an Tender A, 1 an Tender B -> `changes_for("B")` liefert genau 1; `show B` zeigt sie.

### T-10 · Dedup-Blocking und Index (P1)

- **Ziel / Problem:** Cap 500 + `SequenceMatcher` (F-02): Recall-Verlust und 56 ms/Upsert gemessen.
- **Dateien:** `tender_ai/database/models.py` (`TenderRecord.blocking_key: str`, Index), Alembic-Migration (nach T-11), `tender_ai/pipeline/dedup.py` (Zeilen 69-109), `tender_ai/database/repository.py` (`_create_record`, `_update_existing` setzen `blocking_key`), `tender_ai/models/common.py` (`blocking_key(title, authority) -> str`), `tests/test_repository.py`, `tests/test_dedup_bench.py` (markiert `slow`).
- **Aenderung:** `blocking_key = normalize_text(authority)[:24] + "|" + " ".join(normalize_text(title).split()[:3])`; Stufe 3 sucht Kandidaten mit gleichem `blocking_key` **oder** gleichem Vergabestellen-Praefix im Zeitfenster; kein festes `LIMIT 500`, stattdessen Ordnung nach `publication_date`-Naehe und Abbruch bei `title_score >= 0.97`. Optional: `rapidfuzz.fuzz.ratio` statt `difflib`, wenn installiert (weiches Import, Fallback bleibt). Backfill-Migration fuellt `blocking_key` fuer Bestandsdaten.
- **Abhaengigkeiten:** T-11 (Migration), T-26.
- **Akzeptanzkriterien:** (1) Benchmark aus Review H.2 mit 1.000 fremdquelligen Kandidaten: < 5 ms/Upsert (Ist 56 ms). (2) Recall-Test: 600 Datensaetze einer Quelle im Fenster, dann eine Dublette des aeltesten -> wird gefunden (heute: nicht, wegen Cap). (3) Bestehende Dedup-Tests gruen. (4) Kein falsch-positiver Merge in `test_different_tenders_are_not_merged`.
- **Risiken/Hinweise:** Blocking senkt Recall bei stark abweichenden Titeln/Vergabestellen-Schreibweisen - deshalb zweiter Kandidatenpfad ueber Vergabestellen-Praefix. Schwellen unveraendert lassen.

### T-11 · Alembic-Migrationen (P1, vor Stufe 2)

- **Dateien:** neu `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_initial.py`; `tender_ai/database/session.py` (`create_all` aus `session_scope` entfernen), `tender_ai/cli.py` (`init` fuehrt `alembic upgrade head` programmatisch aus; neuer Befehl `db upgrade`), `pyproject.toml` (Abhaengigkeit `alembic>=1.13`), `tests/conftest.py` (Schema per `create_all` **oder** per Migration - Migrationstest separat), README.
- **Aenderung:** Initiale Migration aus dem heutigen Schema autogenerieren und pruefen; `session_scope` prueft per `alembic.runtime.migration.MigrationContext`, ob `head` erreicht ist, sonst klare Fehlermeldung "tender-ai init ausfuehren". Tests: `create_all` bleibt fuer Unit-Tests; ein Test `test_migrations_match_models` laeuft `alembic upgrade head` auf leerer SQLite und vergleicht mit `Base.metadata` (`alembic.autogenerate.compare_metadata` leer).
- **Abhaengigkeiten:** T-26 (gleiche Datei `session.py`).
- **Akzeptanzkriterien:** (1) Frische DB: `tender-ai init` legt Schema per Migration an. (2) Bestehende Stufe-1-DB (per `create_all` erzeugt): `alembic stamp 0001` dokumentiert, `init` erkennt und stempelt automatisch, wenn Tabellen existieren und keine `alembic_version` vorhanden ist. (3) `compare_metadata` leer. (4) Alle Tests gruen.
- **Risiken/Hinweise:** SQLite `ALTER TABLE` ist eingeschraenkt - `render_as_batch=True` in `env.py` setzen. Keine Aenderung der Tabellen in diesem Task.

### T-13 · ruff-Konfiguration und Auto-Fixes (P1)

- **Dateien:** `pyproject.toml` (`[tool.ruff]`, `[tool.ruff.lint]`), alle `tender_ai/**/*.py`, `tests/**/*.py`.
- **Aenderung:** `line-length = 100`, `target-version = "py312"`, `select = ["E","F","W","I","UP","B","BLE","DTZ","SIM","C4","ISC","PYI","FURB"]`, `ignore = ["B008"]` (Typer-Konvention) mit Kommentar; `per-file-ignores`: `tests/*: DTZ`, sowie `BLE001` nur an den 10 bewusst breiten `except`-Stellen mit `# noqa: BLE001 - <Grund>`. `ruff check --fix` und `ruff format` ausfuehren. `DTZ011` (`date.today()`) durch `datetime.now(UTC).date()` ersetzen (4 Stellen).
- **Abhaengigkeiten:** T-12 (Workflow existiert), sonst keine.
- **Akzeptanzkriterien:** `ruff check .` und `ruff format --check .` ohne Befund; Tests gruen; keine Verhaltensaenderung (Diff besteht nur aus Import-Sortierung, Annotationen, `datetime.UTC`, Formatierung und noqa-Kommentaren).
- **Risiken/Hinweise:** Auto-Fixes nur mit `--fix`, nicht `--unsafe-fixes`. `UP017` ersetzt `timezone.utc` durch `datetime.UTC` - Importe anpassen.

### T-14 · mypy sauber (P2)

- **Dateien:** `tender_ai/sources/parsing.py:45,49,55`, `tender_ai/sources/ted.py:193`, `tender_ai/sources/rss.py:150`, `pyproject.toml` (`[tool.mypy]`: `python_version = "3.12"`, `warn_unused_ignores`, `disallow_untyped_defs = true` fuer `tender_ai`), `.github/workflows/ci.yml` (continue-on-error entfernen).
- **Aenderung:** `first_text`-Rueckgaben in lokale `str | None` Variablen; `body: dict[str, Any]` in `ted.search`; `published_struct` in `rss` explizit als `time.struct_time` typisieren und `datetime(*published_struct[:6])` durch `datetime(published_struct.tm_year, ..., tzinfo=UTC)` ersetzen.
- **Akzeptanzkriterien:** `mypy tender_ai` 0 Fehler; CI-Schritt ohne `continue-on-error`.

### T-15 · Persistenz ausserhalb des Event-Loops (P2)

- **Dateien:** `tender_ai/pipeline/ingest.py`, `tender_ai/database/session.py` (Session-Factory statt Session uebergeben), `tender_ai/cli.py`/Service (T-22).
- **Aenderung:** `IngestService` erhaelt `session_factory: Callable[[], Session]` statt `session`; die Persistenz einer Quelle laeuft als Funktion `_persist_source(tenders) -> SourceReport` in `await asyncio.to_thread(...)`, die im Thread ihre eigene Session oeffnet/committet/schliesst. Quellen weiterhin sequenziell nach Prioritaet persistieren (Dublettenreihenfolge).
- **Abhaengigkeiten:** T-01 (Savepoint-Logik zuerst), T-22 (Aufrufer).
- **Akzeptanzkriterien:** (1) Test mit einer Quelle, die 200 Tender liefert, und einem parallel laufenden `asyncio.sleep(0)`-Zaehler: der Event-Loop bleibt reaktiv (Zaehler > 1 waehrend der Persistenz). (2) Alle Ingest-Tests gruen. (3) SQLite mit `check_same_thread=False` bleibt funktionsfaehig (T-26 PRAGMAs gelten je Connection).
- **Risiken/Hinweise:** Keine ORM-Objekte ueber Threadgrenzen reichen - `SourceReport` und IDs zurueckgeben, nicht `TenderRecord`.

### T-16 · Eine Dependency-Quelle und Lockdatei (P1)

- **Dateien:** `pyproject.toml`, `requirements.txt` (generiert), neu `requirements-dev.txt` (generiert) oder `uv.lock`, README, `.github/workflows/ci.yml`.
- **Aenderung:** Obergrenzen fuer Major-Versionen (`httpx>=0.27,<1`, `pydantic>=2.7,<3`, `SQLAlchemy>=2.0,<3`, `typer>=0.12,<1`, `feedparser>=6,<7`, `structlog>=24,<27`); Lockdatei erzeugen (`uv lock` bevorzugt; sonst `pip-compile --extra dev -o requirements-dev.txt`); `requirements.txt` Kopfzeile "generiert, nicht manuell pflegen"; CI installiert aus der Lockdatei.
- **Akzeptanzkriterien:** `pip install -r requirements-dev.txt` in leerer venv + `pytest` gruen; `pip-audit -r requirements-dev.txt` ohne Befund; Diff zwischen `pyproject` und `requirements.txt` nicht mehr manuell.
- **Risiken/Hinweise:** `typer>=0.27` buendelt Click - keine separate `click`-Deklaration hinzufuegen.

### T-17 · Cache-Eviction und Auth im Key (P2)

- **Dateien:** `tender_ai/core/cache.py`, `tender_ai/core/http.py` (Key-Bildung), `tender_ai/cli.py` (`cache-clear --expired`), `tests/test_http.py`.
- **Aenderung:** `ResponseCache.evict_expired() -> int` (beim `HttpClient`-Start einmal aufrufen, kostet einen Verzeichnis-Scan); `max_entries` (Default 5000) mit LRU nach `stored_at`; Key erhaelt `sha256` des Header-Werts fuer `Authorization`, falls gesetzt.
- **Akzeptanzkriterien:** Test: abgelaufener Eintrag wird bei Start entfernt; zwei Requests mit unterschiedlichem `Authorization` erzeugen zwei Eintraege; `cache-clear --expired` meldet Anzahl.

### T-18 · Streaming-Download mit Limits (P2, vor Stufe 2)

- **Dateien:** `tender_ai/core/http.py` (`download`), `tender_ai/config.py` (`http.max_download_bytes: int = 50_000_000`, `http.max_feed_bytes: int = 5_000_000`), `tender_ai/sources/rss.py` (`_fetch_feed` prueft `len(response.content)`), `tests/test_http.py`, `tests/test_source_rss.py`.
- **Aenderung:** `download` nutzt `client.stream("GET", ...)` und schreibt chunkweise (64 KiB) in eine `.part`-Datei, bricht bei Ueberschreitung mit `HttpError("... ueberschreitet max_download_bytes")` ab, benennt erst nach Erfolg um; optionaler Parameter `expected_types: set[str]` prueft `Content-Type`. Feed-Antworten ueber `max_feed_bytes` werden abgelehnt (Mitigation F-29).
- **Abhaengigkeiten:** T-05.
- **Akzeptanzkriterien:** Test: 60 MB-Mockantwort -> `HttpError`, keine `.part`-Datei bleibt; 1 MB -> Datei korrekt, `HttpStats.bytes_downloaded` stimmt; Feed > `max_feed_bytes` -> `SourceError`.
- **Risiken/Hinweise:** respx unterstuetzt Streaming-Antworten (`httpx.Response(stream=...)`).

### T-19 · Konfigurationsdoppelung und README-Zahl (P2)

- **Dateien:** `config.yaml`, `tender_ai/config.py`, `README.md:140,195`, `docs/architecture.md` (Verweis).
- **Aenderung:** `criteria`/`scoring` in `config.yaml` unter einem klar kommentierten Block "ab Stufe 5 wirksam - heute ohne Funktion" belassen, aber `criteria.minimum_days_until_deadline` entfernen und im Kommentar auf `search.min_days_until_deadline` verweisen; `SearchConfig.min_days_until_deadline` Default auf 3 setzen (wie config.yaml) oder config.yaml auf 0 - **eine** Wahrheit; README: "74 Tests" durch "`pytest -q`" ohne Zahl ersetzen.
- **Akzeptanzkriterien:** grep nach `minimum_days_until_deadline` liefert nur noch einen Treffer (Kommentar); Tests gruen; README enthaelt keine Testanzahl.

### T-20 · Laufstatus und verwaiste Laeufe (P3)

- **Dateien:** `tender_ai/database/models.py` (`IngestRunRecord.status`), Migration, `tender_ai/pipeline/ingest.py`, `tender_ai/database/repository.py` (`mark_stale_runs()`), `tender_ai/cli.py` (`runs` zeigt Status).
- **Aenderung:** Status `running` beim Start, `finished` am Ende, `aborted` bei Exception im `run` (try/finally); `mark_stale_runs(older_than=timedelta(hours=6))` beim Start eines neuen Laufs.
- **Abhaengigkeiten:** T-11.
- **Akzeptanzkriterien:** Test: Exception in `run` -> `status == "aborted"`; Lauf mit `finished_at IS NULL` aelter als 6 h -> beim naechsten Start `aborted`.

### T-21 · Log-Kontext `run_id`/`source` (P2)

- **Dateien:** `tender_ai/pipeline/ingest.py`, `tests/test_ingest.py`.
- **Aenderung:** `bind_contextvars(run_id=run_record.id or uuid)` zu Beginn von `run`, `bound_contextvars(source=source.name)` in `_search_one` und um die Persistenz; `clear_contextvars` am Ende.
- **Akzeptanzkriterien:** Test faengt Logausgabe (structlog `capture_logs`) und prueft, dass `http_retry`/`source_failed`-Events `run_id` und `source` tragen.

### T-22 · Service-Layer (P2, vor Stufe 6)

- **Dateien:** neu `tender_ai/services/__init__.py`, `tender_ai/services/search.py`, `tender_ai/services/health.py`; `tender_ai/cli.py` (Befehle `search`, `doctor` rufen nur noch Services); `tests/test_services.py` (neu).
- **Aenderung:** `async def run_search(settings, query, *, only_sources=None, store=True, download_documents=False) -> IngestReport` kapselt HTTP-Client-Aufbau, `build_sources`, Session-Handling (mit `session_scope`), Fehlerfall "keine Quelle" als `ConfigError`. `async def check_sources(settings, only=None) -> list[SourceStatus]`. CLI faengt `ConfigError` und rendert. Kein Rich in Services.
- **Abhaengigkeiten:** T-01.
- **Akzeptanzkriterien:** `cli.py` importiert weder `build_http_client` noch `IngestService`; `grep -c "IngestService" tender_ai/cli.py == 0`; Service-Tests decken `store=False`, unbekannte Quelle, Quellausfall ab; CLI-Tests unveraendert gruen.

### T-23 · Typisierte Quellkonfigurationen (P2)

- **Dateien:** `tender_ai/config.py` (`TedSourceConfig`, `RssSourceConfig`, `FeedConfig`, `FixtureSourceConfig`, `SourceConfig = Annotated[Union[...], Field(discriminator="type")]`), `tender_ai/sources/ted.py`, `rss.py`, `fixture.py` (`getattr(self.config, ...)` durch Attributzugriff ersetzen), `tender_ai/sources/registry.py`, `tests/conftest.py`, `tests/test_config.py`.
- **Abhaengigkeiten:** T-04.
- **Akzeptanzkriterien:** Unbekannter Schluessel (`page_sze`) in `config.yaml` fuehrt zu `ValidationError` mit Feldpfad `sources.ted.page_sze`; alle Adapter-Tests gruen; kein `getattr(self.config` mehr in `tender_ai/sources/`.
- **Risiken/Hinweise:** Eigene Adapter Dritter brauchen einen Registrierungsweg fuer ihre Config-Klasse (`register_source(cls, config_cls)`) - vorsehen.

### T-24 · Testluecken und Coverage-Schwelle (P1)

- **Dateien:** `tests/test_source_ted.py` (+`get_tender_details`, `download_documents` mit respx-Binaerantwort), `tests/test_cli.py` (+`show` mit Losen/Dokumenten/Aliassen/Aenderungen, `runs`, `export --format xlsx`, `cache-clear`), `tests/test_http.py` (+`crawl_delay`), `tests/test_registry.py` (neu: unbekannter Typ, Init-Fehler, `only` mit unbekanntem Namen), `tests/test_source_rss.py` (+Billion-laughs-Feed wird ohne Speicherexplosion abgelehnt, F-29), `pyproject.toml`/CI (`--cov-fail-under=85`).
- **Abhaengigkeiten:** T-12, T-01…T-08 (deren Tests entstehen in den jeweiligen Tasks).
- **Akzeptanzkriterien:** Gesamt-Coverage >= 85 %; `ted.py`, `cli.py`, `cache.py` jeweils >= 85 %; CI-Schwelle aktiv; Testlaufzeit < 15 s.

### T-25 · Dockerfile und Version aus Metadaten (P2)

- **Dateien:** neu `Dockerfile`, `.dockerignore`; `tender_ai/__init__.py` (`__version__ = importlib.metadata.version("tender-ai")` mit Fallback), README.
- **Abhaengigkeiten:** T-16.
- **Akzeptanzkriterien:** `docker build` < 2 Minuten, Image laeuft als non-root, `docker run --rm -v $PWD/data:/app/data tender-ai search --source fixture` liefert die Demotreffer; `python -c "import tender_ai; print(tender_ai.__version__)"` entspricht `pyproject.toml`.

### T-26 · SQLite-PRAGMAs (P1)

- **Dateien:** `tender_ai/database/session.py`, `tests/test_repository.py`.
- **Aenderung:** `@event.listens_for(engine, "connect")` fuer SQLite: `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`, `PRAGMA foreign_keys=ON`, `PRAGMA synchronous=NORMAL`.
- **Akzeptanzkriterien:** Test: `SELECT journal_mode` liefert `wal`; Test: `ON DELETE CASCADE` wirkt (Alias verschwindet mit Tender - heute nur ORM-seitig); zwei Sessions, eine haelt eine Schreibtransaktion 1 s, die zweite schreibt danach ohne `database is locked`.
- **Risiken/Hinweise:** WAL erzeugt `-wal`/`-shm`-Dateien neben der DB - `.gitignore` um `data/*.db-*` ergaenzen.

### T-27 · `raw` aus `payload` auslagern (P3)

- **Dateien:** `tender_ai/database/models.py` (`TenderRecord.raw: JSON` oder Tabelle `tender_raw`), Migration, `tender_ai/database/repository.py` (`_create_record`, `_update_existing`, `to_tender`), Tests.
- **Abhaengigkeiten:** T-11.
- **Akzeptanzkriterien:** `payload` enthaelt kein `raw` mehr; `to_tender` liefert weiterhin `raw`; DB-Groesse fuer 1.000 TED-Datensaetze sinkt messbar (Vorher/Nachher im PR dokumentieren); Round-Trip-Test gruen.

---

## F. Empfohlene Reihenfolge

Blocker und Parallelisierung sind so gewaehlt, dass sich Tasks keine Dateien
teilen, wenn sie parallel laufen.

**Welle 0 - Sicherheitsnetz (parallel, je S):** T-12 (CI) → danach parallel T-13 (ruff), T-16 (Lockdatei).
*T-12 zuerst, weil jede weitere Aenderung darueber abgesichert wird.*

**Welle 1 - P0-Bugs (parallel, je S, disjunkte Dateien):**
T-01 (`ingest.py`), T-02 (`parsing.py`), T-03 (`rss.py`), T-04 (`config.py`, `ted.py:_headers`), T-05 (`base.py`, `ted.py:download`), T-08 (`http.py`, `ratelimit.py`, `robots.py`).
*T-04 und T-05 beruehren beide `ted.py` an verschiedenen Stellen - nacheinander mergen, nicht gleichzeitig editieren.*

**Welle 2 - Datenqualitaet und Betrieb (parallel):**
T-26 (`session.py`) → T-11 (Alembic; baut auf T-26 auf) ; parallel dazu T-07 (`ted.py`), T-09 (`repository.py`, `cli.py`), T-06 (`cli.py`, nach T-09).

**Welle 3 - Skalierung und Absicherung:**
T-10 (Dedup; braucht T-11 fuer die Migration) ; parallel T-24 (Tests, braucht Welle 1) und T-14 (mypy, braucht T-13).

**Welle 4 - Struktur vor Stufe 2 / Stufe 6:**
T-22 (Service-Layer, braucht T-01) → T-15 (Persistenz im Thread, braucht T-22) ; parallel T-23 (typisierte Configs, braucht T-04) und T-18 (Streaming, braucht T-05).

**Welle 5 - Betrieb und Feinschliff (parallel):** T-17, T-19, T-21, T-25 (braucht T-16), T-20 (braucht T-11), T-27 (braucht T-11).

**Gate fuer Stufe 2 (Dokumentenanalyse):** Wellen 0-3 abgeschlossen sowie T-18 und T-22 - andernfalls werden Path-Handling, Download-Limits und Orchestrierung in der neuen Stufe erneut gebaut.

Kritischer Pfad: T-12 → T-26 → T-11 → T-10 (bzw. T-11 → T-20/T-27).

---

## G. Definition of Done (Roadmap gesamt)

Die Roadmap gilt als abgeschlossen, wenn auf `main` **alle** folgenden Kriterien messbar erfuellt sind:

1. **CI gruen** auf jedem PR: `pytest` (Coverage >= 85 %), `ruff check`, `ruff format --check`, `mypy tender_ai` (0 Fehler), `pip-audit` (0 bekannte Schwachstellen) - keine `continue-on-error`-Schritte mehr.
2. **Keine offenen P0/P1-Findings**: F-01, F-02, F-03, F-04, F-05, F-06, F-08, F-09, F-12, F-13, F-14, F-15, F-16, F-24, F-27 sind durch die zugeordneten Tests abgedeckt und die Tests laufen in CI.
3. **Reproduktionen aus Review Anhang H schlagen nicht mehr fehl**: H.3 (kein Datenverlust, korrekter Report), H.4 (Secrets aus `.env` fuer beliebige Quellen), `parse_amount("1234.50") == 1234.5`, Binde-/Lieferfrist landen nicht in `submission_deadline`.
4. **Benchmark H.2**: < 5 ms pro Upsert bei 1.000 fremdquelligen Kandidaten im Zeitfenster; Recall-Test ueber 600 Kandidaten gruen.
5. **Schema-Migrationen**: `alembic upgrade head` auf leerer DB erzeugt ein Schema, das `compare_metadata` leer laesst; bestehende Stufe-1-Datenbanken werden ohne Datenverlust gestempelt.
6. **Eine Dependency-Quelle**: `pyproject.toml` mit Major-Obergrenzen, Lockdatei eingecheckt, `requirements*.txt` generiert; reproduzierbare Installation in leerer venv nachgewiesen.
7. **Dokumentation stimmt mit dem Code ueberein**: die Diskrepanzen aus Review B.9 sind behoben (Kommentar `http.py` zu `Crawl-delay`, README-Testzahl, Konfigurationsdoppelung); README und `architecture.md` verweisen auf diese Roadmap und markieren erledigte Tasks.
8. **Keine Verhaltensaenderung der Fachlogik** ausserhalb der in den Tasks beschriebenen Korrekturen (Fristen, Betraege, Status): die Stufe-1-Testsuite von Commit `38a7054` bleibt - abgesehen von den in T-01/T-02/T-03/T-07 explizit angepassten Erwartungen - unveraendert gruen.
