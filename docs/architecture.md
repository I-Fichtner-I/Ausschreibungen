# Architektur und Umsetzungsstrategie

Dieses Dokument haelt die Analyse **vor** der Implementierung fest: Zielbild,
Schnittstellen, Datenmodelle, technische Risiken, getroffene Standard-
entscheidungen und den stufenweisen Umsetzungsplan.

Grundsatz des Projekts: **kein Web-Scraper, sondern ein Procurement
Intelligence Agent.** Die wichtigste Kennzahl ist nicht der niedrigste
Einkaufspreis, sondern der realistisch erwartbare Gewinn nach allen Kosten und
Risiken - und jede Empfehlung muss auf die zugrunde liegenden Ausschreibungs-
und Preisdaten zurueckfuehrbar sein.

---

## 1. Umsetzungsstrategie: eine Stufe nach der anderen

Der Auftrag umfasst eine lange Kette (Recherche → Analyse → Artikel →
Preise → Kosten → Marge → Score → Freigabe → Angebotsentwurf). Diese Kette
wird **nicht** auf einmal gebaut. Jede Stufe wird einzeln lauffaehig,
testbar und nutzbar gemacht, bevor die naechste beginnt - so ist nach jeder
Stufe ein echter Zwischenstand vorhanden statt eines halbfertigen Ganzen.

| Stufe | Inhalt | Status |
|-------|--------|--------|
| **1** | **Ausschreibungen recherchieren** - Quell-Adapter, einheitliches Datenmodell, Dubletten, Speicherung, Aenderungserkennung, Export, CLI | **fertig, testbar** |
| **2** | **Ausschreibung analysieren** - Dokumentendownload, Textextraktion (PDF/DOCX/XLSX/HTML/CSV), Anforderungserkennung und begruendeter Risiko-Score | **fertig, testbar** |
| **3** | **Artikel extrahieren** - Spaltenrollen und Einheiten erkennen, `TenderItem` mit Menge, Einheit, Hersteller/Typ, Merkmalen und Fundstelle | **fertig, testbar** |
| 4 | Produkt-Matching und Preisrecherche - Lieferanten, `PriceOffer`, Preisstatistik | offen |
| 5 | Kosten, Profitabilitaet, Szenarien, Score, Entscheidungsvorlage | offen |
| 6 | Dashboard, Benachrichtigungen, Scheduler, Angebotsentwurf | offen |

Die Stufen 4-6 haben ihre Andockpunkte bereits im Code: `Tender.documents`,
`TenderRequirements`, die Konfigurationsbloecke `criteria`/`scoring` und die
Fremdschluessel-Konvention `tender_id` in der Datenbank.

---

## 2. Zielarchitektur

```
                    +---------------------+
   config.yaml ---> |    Konfiguration    | <--- .env (nur Secrets)
                    +----------+----------+
                               |
   +---------------------------+---------------------------+
   |                     Kernbausteine                      |
   |  HTTP-Client (Retry, Backoff, Rate-Limit, Cache,       |
   |  robots.txt)  |  strukturiertes Logging  |  Fehler     |
   +---------------------------+---------------------------+
                               |
   +-----------+------------+------------+-----------+--------+
   | sources/  | extraction/|  analysis/ |  items/   |pricing/|  (Stufe 2-4)
   | TED, RSS, | PDF, DOCX, | Anforderun-| Positionen| Liefe- |
   | Fixture   | XLSX, HTML | gen, Risiko| Mengen    | ranten |
   +-----------+------------+------------+-----------+--------+
                               |
                    +----------v----------+
                    |   Pipeline (ingest) |   Dubletten, Aenderungen
                    +----------+----------+
                               |
              +----------------+----------------+
              |         database (SQLAlchemy)   |  SQLite / PostgreSQL
              +----------------+----------------+
                               |
        +----------+-----------+-----------+-----------+
        |   CLI    |  export   | analysis/ | dashboard |   (Stufe 5-6)
        +----------+-----------+-----------+-----------+
```

### Abweichungen von der Struktur im Auftrag (dokumentierte Entscheidungen)

| Vorschlag | Umsetzung | Begruendung |
|-----------|-----------|-------------|
| `app/` als Paketwurzel | `tender_ai/` | Ein Paketname, der beim Import spricht; die zusaetzliche `app`-Ebene bringt keinen Nutzen. Unterpakete heissen wie vorgeschlagen. |
| `TenderSource.search()` synchron | `async def search()` | Anforderung 28 verlangt asynchrone Architektur; mehrere Quellen laufen parallel. |
| `main.py` | `cli.py` + Konsolenskript `tender-ai` | Einstiegspunkt ueber `pyproject.toml`, kein Pfad-Gebastel. |
| Fehlende Werte | intern `None`, in jeder Ausgabe `UNKNOWN` | Rechnen mit `None` schlaegt laut fehl statt still falsch zu werden; die Ausgabe bleibt eindeutig. |

---

## 3. Datenmodelle

### Stufe 1 (implementiert)

`Tender` - die standardisierte Ausschreibung. Zusaetzlich zu den im Auftrag
genannten Feldern:

- `national_id` - amtliche Vergabenummer, staerkstes Dublettenmerkmal
- `provenance` - Quelle, Quell-ID, URL, Abrufzeitpunkt, Methode, optional
  Dokument/Seite/Abschnitt/Originaltext und Confidence (fuer KI-Ergebnisse
  ab Stufe 2 verpflichtend)
- `raw` - die unveraenderten Quelldaten, damit spaetere Stufen nichts verlieren
- `value_is_estimated` - Schaetzungen sind nie als amtlicher Wert lesbar
- `notes` - Hinweise fuer den Menschen (z. B. "Frist aus Feed-Text extrahiert")
- `fingerprint()` / `content_hash()` - Dubletten- bzw. Aenderungserkennung

Weitere Modelle: `TenderLot`, `TenderDocument` (mit `access`: PUBLIC /
REGISTRATION / RESTRICTED - geschuetzte Unterlagen werden vermerkt, nicht
umgangen), `TenderRequirements` (ab Stufe 2 gefuellt), `Provenance`,
`EstimatedValue`.

### Stufe 3 (implementiert)

`TenderItem` - eine Position des Leistungsverzeichnisses: `position`, `title`,
`description`, `quantity` (+ `quantity_estimated`), `unit` (normiert) und
`unit_original`, `manufacturer`, `model_number`, `article_number`,
`specifications`, `brand_locked`, `provenance` und `warnings`.

Zwei getrennte Guetemasse, weil zwei verschiedene Dinge unsicher sein koennen:

- `confidence` (0-100) - wie vollstaendig die **Zeile gelesen** wurde. Sie
  erreicht nie 100: auch eine perfekt gefuellte Tabellenzeile bleibt eine
  Auslegung des Dokuments.
- `match_confidence` - Guete der **Produktzuordnung**. Bleibt `None` bis
  Stufe 4; eine unsichere Zuordnung wird nicht durch eine Zahl kaschiert.

`ItemExtractionResult` haelt daneben die Kennzahlen des Laufs (gescannte
Dokumente und Tabellen, genutzte Tabellen, Warnungen), damit ein leeres
Ergebnis erklaerbar bleibt: "keine Tabelle gefunden" ist etwas anderes als
"keine Unterlagen vorhanden".

### Datenbank (Stufe 1)

`tenders`, `tender_aliases` (weitere Fundstellen derselben Ausschreibung),
`tender_documents`, `tender_changes` (Aenderungshistorie), `ingest_runs`
(Laufprotokoll), `source_states` (Quellenstatus, Fehler in Folge).

Seit Stufe 2: `document_extracts` (Text, Tabellen und Metadaten je Unterlage,
1:1 zum Dokument - der Text bleibt aus den Listenabfragen heraus) und
`risk_analyses` (Score, Stufe, begruendete Faktoren und Funde je Ausschreibung;
der mitgespeicherte `content_hash` verhindert unnoetige Neubewertungen).

Seit Stufe 3: `tender_items` (eine Zeile je erkannter Position, mit Fundstelle
und Originalzeile) und `item_extractions` (Kennzahlen des Laufs je
Ausschreibung, ebenfalls mit `content_hash` fuer den Stapellauf).

Geplant ab Stufe 4: `suppliers`, `price_offers`, `cost_calculations`,
`profitability_analyses`, `analysis_history`.

---

## 4. Schnittstellen

```python
class TenderSource(ABC):
    type_name: ClassVar[str]

    async def search(self, query: SearchQuery) -> list[Tender]: ...
    async def get_tender_details(self, tender_id: str) -> Tender | None: ...
    async def download_documents(
        self, tender: Tender, destination: Path
    ) -> list[TenderDocument]: ...
    async def health_check(self) -> SourceStatus: ...
```

Eine neue Quelle ergaenzen:

1. Klasse von `TenderSource` ableiten, `type_name` setzen
2. mit `@register_source` dekorieren
3. Modul in `tender_ai/sources/registry.py::_ensure_loaded` importieren
4. Block in `config.yaml` unter `sources:` anlegen

Die Pipeline muss dafuer nicht angefasst werden.

---

## 5. Technische Risiken und wie sie behandelt werden

| Risiko | Behandlung |
|--------|-----------|
| **API-Aenderungen bei TED** (Endpunkt, Feldnamen, Query-Syntax sind versioniert) | Endpunkt, Feldliste und Query-Feldnamen stehen in `config.yaml`; `raw_query` erlaubt eine komplett eigene Expert-Query. Fehlt das Ergebnisfeld in der Antwort, wird ein **Fehler** gemeldet statt "0 Treffer" - eine stille Falschmeldung waere hier der gefaehrlichere Fall. |
| **Heterogene Feldwerte** (String / Liste / mehrsprachiges Dict) | Toleranter Parser (`sources/parsing.py`), der im Zweifel `None` liefert statt zu raten. |
| **Portale ohne API** | RSS/Atom zuerst; HTML-Parsing erst, wenn robots.txt und Nutzungsbedingungen es zulassen. Login-, Captcha- oder Paywall-geschuetzte Inhalte werden **nicht** abgerufen, sondern als `RESTRICTED` vermerkt. |
| **Unvollstaendige Bekanntmachungen** (Frist, Volumen, Mengen fehlen oft) | `UNKNOWN` statt Schaetzung; Fristen, die aus Fliesstext extrahiert wurden, tragen Provenance und einen Hinweis. |
| **Dubletten ueber Portale hinweg** | Dreistufige Erkennung; unterhalb der Schwelle wird bewusst nicht zusammengefuehrt. Primaerquelle nach konfigurierter Prioritaet. |
| **Ausfall einer Quelle** | Jede Quelle ist gekapselt; Fehler landen im Lauf-Report und in `source_states`, der Lauf laeuft weiter. |
| **Rate-Limits / Sperren** | Rate-Limit pro Host, robots.txt inkl. `Crawl-delay`, Exponential Backoff mit Jitter, `Retry-After` wird beachtet, Cache gegen unnoetige Wiederholungen. |
| **Scheingenauigkeit beim Risiko-Score** | Der Score ist additiv und jeder Faktor nennt Punkte, Begruendung und Beleg aus dem Dokument. Fehlende Information senkt den Score nie, sondern erzeugt eigene Faktoren (`deadline_unknown`, `value_unknown`, `documents_missing`) - eine unbekannte Ausschreibung darf nicht unauffaellig wirken. |
| **Falsche Produktzuordnung** (ab Stufe 3) | `match_confidence`; unterhalb der Schwelle keine Zuordnung, sondern `REQUIRES_REVIEW`. |

---

## 6. Compliance-Regeln (gelten fuer alle Stufen)

Erlaubt: oeffentlich zugaengliche Informationen automatisiert abrufen und
analysieren, Preise recherchieren, Daten speichern, rechnen, Angebots**entwuerfe**
vorbereiten.

Nicht erlaubt und im Code nicht vorgesehen: Captchas, Login-Schranken,
Paywalls oder sonstige technische Schutzmassnahmen umgehen, fremde Accounts
nutzen, verbindliche Angebote automatisch abgeben oder rechtlich bindende
Erklaerungen ohne ausdrueckliche Freigabe abgeben.

Der Ablauf bleibt immer: **Analyse → Freigabe durch den Nutzer →
Angebotsentwurf → manuelle Pruefung → manuelle Abgabe.**

Technisch verankert ist das in Stufe 1 durch: `RobotsGuard` (Sperren werden
respektiert und als Quellfehler gemeldet, nicht umgangen),
`AccessRestrictedError`, `DocumentAccess`, Rate-Limiting und einen
User-Agent mit Kontaktadresse.

Zur robots.txt-Ausnahme fuer TED: Die TED-Such-API ist ausdruecklich fuer den
maschinellen Zugriff bereitgestellt und hat eigene Nutzungsbedingungen; die
robots.txt des Webportals regelt das Crawling der Website, nicht die
API-Nutzung. Das Rate-Limit gilt dort unveraendert. Fuer alle HTML-/RSS-
Quellen wird robots.txt geprueft.

---

## 7. Offene Punkte (bewusst entschieden, spaeter zu bestaetigen)

1. **Feed-URL von service.bund.de**: als Standard hinterlegt, aber in dieser
   Entwicklungsumgebung nicht live pruefbar (kein Netzzugang zu externen
   Portalen). `tender-ai doctor` zeigt sofort, ob sie stimmt; die URL steht in
   `config.yaml` und ist ohne Codeaenderung korrigierbar.
2. **TED-Query-Feldnamen** (`buyer-country`, `deadline-receipt-request`, ...):
   nach eForms-Notation hinterlegt, ebenfalls in `config.yaml` korrigierbar,
   inklusive `raw_query`-Ausweg.
3. **Weitere Quellen** (Datenservice Oeffentlicher Einkauf, Landesportale,
   kommunale Portale): jeweils erst nach Pruefung von API-Verfuegbarkeit,
   robots.txt und Nutzungsbedingungen. Fuer Portale ohne Schnittstelle gibt es
   seit dem Vergabemarktplatz NRW den generischen `html_list`-Adapter: Zeilen-
   und Feldselektoren stehen in `config.yaml`, `doctor` meldet je Feld die
   Trefferquote. Ein neues Portal ist damit ein Konfigurationsblock statt eines
   weiteren Parsers - und ein geaendertes Markup eine Zeile statt eines
   Releases.
4. **Scheduler**: Stufe 1 laeuft ueber cron/systemd (siehe README). Ein
   eigener Scheduler-Prozess lohnt erst, wenn mehrere Stufen zu takten sind.
5. **KI-Einsatz**: bewusst noch nicht - in Stufe 1 gibt es keine Aufgabe, die
   Regeln nicht besser loesen. Ab Stufe 2 (Dokumentenanalyse, Tabellen,
   Produkt-Matching) mit verpflichtender Confidence und Rueckverweis auf
   Dokument, Seite und Originaltext.

---

## 8. Teststrategie

- **Einheitentests** fuer Parser, Modelle, Fingerprint/Content-Hash, Export
- **Adaptertests** gegen aufgezeichnete Antworten (`respx`), nicht gegen
  Live-Portale - dadurch schnell, offline und unabhaengig von der Tagesform
  fremder Server
- **Verhaltenstests** fuer die Regeln, die im Betrieb zaehlen: Retry und
  Backoff, `Retry-After`, robots.txt-Sperre, Cache, Rate-Limit, Ausfall einer
  Quelle, Dubletten, Aenderungserkennung
- **CLI-Tests** ueber den echten Befehlspfad
- **`tender-ai doctor`** als Live-Pruefung gegen die echten Endpunkte -
  bewusst ausserhalb der Testsuite, weil es Netzzugang braucht
