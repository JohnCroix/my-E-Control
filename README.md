# my E-Control

Lade deine Strommesswerte (Smart Meter) aus dem [eService-Portal der Netz OÖ](https://eservice.netzooe.at)
als CSV – Tagesverbrauch und 15-Minuten-Werte, **inkrementell**: Es werden nur Tage
geladen, deren Daten noch nicht lokal vorhanden sind.

> Inoffizielles Community-Projekt, nicht mit der Netz Oberösterreich GmbH verbunden.

## Features

- Login + CSRF-Session automatisiert (Spring-Security-Portal)
- Tagesverbrauch (kWh/Tag) als CSV, eine Datei pro Kalendertag + Gesamt-CSV
- 15-Minuten-Werte (96 Intervalle/Tag) als optionales Archiv
- `--latest`: aktueller Verbrauch der letzten 15 Minuten, ohne Download
- `report.py`: Monatsbericht (Leistungsspitzen) + Lastprofil-Heatmaps als HTML
- Keine externen Abhängigkeiten (nur Python-Standardbibliothek)
- Funktioniert mit jedem Scheduler (Cron, CI, n8n, …)

## Voraussetzungen

- Python ≥ 3.9
- Zugangsdaten für das eService-Portal (`https://eservice.netzooe.at`),
  identisch zum Browser-Login

## Installation

```bash
git clone https://github.com/JohnCroix/my-E-Control.git
cd my-E-Control
cp .env.example .env      # Zugangsdaten eintragen
python3 download_readings.py                 # startet mit dem Tages-Archiv
```

Optional ein 15-Minuten-Archiv auch für die Vergangenheit nachladen
und danach den Auswertungs-Bericht erzeugen:

```bash
python3 download_readings.py --include-qh    # 15-Minuten-Werte (neu + historisch)
python3 report.py                            # Monatsbericht + HTML-Dashboard
```

> `report.py` benötigt das 15-Minuten-Archiv (`days15/`) – das lädt `--include-qh`.

## Nutzung

```bash
python3 download_readings.py                # inkrementeller Download
python3 download_readings.py --include-qh   # zusätzlich 15-Minuten-Werte
python3 download_readings.py --latest       # aktueller Verbrauch (letzte 15 min)
python3 download_readings.py --dry-run      # Vorschau ohne Download
```

| Option              | Bedeutung                                                         |
|---------------------|-------------------------------------------------------------------|
| `--since`           | nur ab diesem Datum laden (Standard: Beginn des Profils)           |
| `--until`           | nur bis zu diesem Datum (Standard: heute bzw. Profil-Ende)         |
| `--include-qh`      | 15-Minuten-Werte je Tag zusätzlich archivieren                     |
| `--latest`          | nur aktuelle 15-Minuten-Werte anzeigen (kein Download)             |
| `--latest-window N` | Anzahl der angezeigten Viertelstunden bei `--latest` (Standard 6)  |
| `--force`           | bereits vorhandene Tage erneut laden                               |
| `--dry-run`         | zeigt nur, was geladen würde                                       |
| `--output DIR`      | Ausgabeverzeichnis (Standard `data/readings`)                      |

Zugangsdaten können auch per Kommandozeile (`--username`, `--password`) oder
Umgebungsvariablen (`NETZOOE_USERNAME`, `NETZOOE_PASSWORD`) übergeben werden.

Exit-Codes: `0` erfolgreich · `2` Login fehlgeschlagen · `3` API-Fehler ·
`4` unerwarteter Fehler

## Ausgabe-Struktur

```
data/readings/
├── _last_run.json                        # Infos zum letzten Lauf
└── <Zählpunkt-MA>/                       # ein Ordner pro Zähler
    ├── all_days.csv                      # Tageswerte, alle Tage
    ├── days/2026-10-28.csv               # ein File pro Tag (kWh)
    ├── days15/2026-10-28.csv             # 15-Minuten-Werte (nur mit --include-qh)
    ├── all_15min.csv                     # 15-Minuten-Werte, alle Tage
    ├── months/2026-02.csv                # Monatswerte bei Tageswert-Lücken
    └── report.html                       # Auswertung (report.py): Spitzen + Heatmaps
```

CSV-Format (Semikolon, deutsches Dezimalformat, UTF-8 mit BOM):

```csv
"Datum";"kWh";"Status";
"28.10.2026 00:00";12,664;"VALID";
```

15-Minuten-Dateien enthalten 96 Zeilen pro Tag, jeweils Verbrauch eines
15-Minuten-Intervalls (Startzeit in lokaler Zeit):

```csv
"Datum";"kWh";"Status";
"28.10.2026 00:00";0,064;"VALID";
"28.10.2026 00:15";0,065;"VALID";
```

## Aktueller Verbrauch (letzte 15 Minuten)

```bash
python3 download_readings.py --latest --latest-window 6
```

```text
Letzte 15-Minuten-Werte – Zähler AT00… (28.10.2026):
  23:30–23:45      0,043 kWh  (VALID)
  23:45–00:00      0,041 kWh  (VALID)  ← aktuellster Wert
Tagesverbrauch bisher (28.10.2026): 12,664 kWh (96/96 Viertelstunden)
```

Der aktuelle Tag erscheint, sobald Netz OÖ die Messwerte übermittelt hat
(meist am Folgetag gegen Mittag).

## Auswertung (report.py)

`report.py` erzeugt aus dem 15-Minuten-Archiv einen Monatsbericht und
Lastprofil-Heatmaps – passend zu den neuen Netzentgeltregeln (siehe unten):

```bash
python3 report.py                 # Bericht für alle Zählpunkte + HTML-Dashboard
python3 report.py --month 2026-02 # Detail-Heatmap für einen bestimmten Monat
python3 report.py --year 2025     # Jahresprofil eines anderen Jahres
python3 report.py --no-html       # nur Konsolenausgabe
```

- **Monatsbericht:** höchster 15-Minuten-Leistungswert (kW) je Monat mit Zeitpunkt,
  Verbrauch (kWh) sowie Vergleich zum Vormonat und zum Vorjahr – die Kennzahl,
  auf der der künftige Leistungspreis basiert
- **Jahresprofil:** Ø Leistung (kW) je Monat × Stunde als Heatmap
- **Monatsprofil:** max. Leistung (kW) je Tag × Stunde als Heatmap – zeigt
  Spitzen-Cluster (z. B. „samstags 11 Uhr“)
- **Empfehlungen:** faktenbasierte Tipps aus deinen eigenen Daten – wann deine
  Spitzen entstehen, Anteil im SNAP/WiNAP-Fenster, Verschiebepotenzial am Abend,
  Wallbox-Ladeleistung als größter Hebel – plus Erklärungen zu den 5
  Kernelementen und einer Geräte-Tabelle (was läuft wann am besten)

Das HTML-Dashboard (`<Zählpunkt>/report.html`) ist eine einzelne, eigenständige
Datei ohne externe Abhängigkeiten – einfach im Browser öffnen (offline nutzbar).
Sie enthält die Empfehlungen ganz oben, darunter die Daten-Tabellen als Beleg.

### Demo-Auszug (Beispielausgabe)

Konsolenausgabe von `python3 report.py` (Werte je nach Messhistorie des Zählers):

```text
=== Zähler AT00… ===
Monat     Spitze Zeitpunkt            kWh Δ Vor-Monat   Δ Vorjahr
--------------------------------------------------------------------
2025-10    4,10 kW 25.10.2025 10:45   354    +0,20 kW    +0,29 kW
2025-11    5,22 kW 15.11.2025 10:30   664    +1,12 kW    +1,26 kW
2025-12    5,24 kW 28.12.2025 18:00   729    +0,03 kW    +0,56 kW
2026-01    5,33 kW 08.01.2026 17:00   793    +0,09 kW    +0,25 kW
2026-02    5,50 kW 01.02.2026 11:00   576    +0,17 kW    +0,58 kW
…

Empfehlungen (SNE-G-V / neue Netzentgeltregeln):
  • Leistungsspitze & Wallbox: höchste Monatsspitze 5,50 kW (Ø 4,26 kW) – ohne
    11-kW-Ladeereignis. Mit 11-kW-Wallbox + Wärmepumpe (Grundlast) ≈ 13–15 kW →
    Ladeleistung an den Hausanschluss anpassen, nicht mit Backofen/Boiler stapeln.
  • Wann deine Spitzen entstehen: am häufigsten Samstag um 11:00 Uhr
    (typischer Haushaltsstrom); Wärmepumpe läuft als Grundlast unabhängig davon.
  • Günstige Fenster (SNAP/WiNAP): 9 % im Sommer-Sparfenster (10–16 Uhr),
    13 % im Winter-Sparfenster (23–5 Uhr); 15 % des Verbrauchs im Abendfenster
    17–20 Uhr – das größte Verschiebepotenzial (Startzeitvorwahl nutzen).
  • Entlastete Nachtstunden 01–04 Uhr: Boiler, E-Auto, Speicher dorthin.
```

Im Browser zeigt das HTML-Dashboard dieselben Zahlen als Tabellen mit
Farbskala (Heatmaps) und Tooltips je Zelle.

## Neue Netzentgeltregeln – „Neue Regeln für die Energiewende“ (SNE-G-V)

Die [E-Control](https://www.e-control.at/konsumenten/neue-regeln-f%C3%BCr-die-energiewende)
hat mit der **Systemnutzungsentgelte-Grundsatzverordnung (SNE-G-V)** die Grundlage
für eine neue Netzentgeltstruktur ab **1. Jänner 2027** geschaffen: Netze besser
auslasten, Lastspitzen reduzieren, Netzausbau dämpfen (rund 300 Mio. €/Jahr an
Netzkosten vermeidbar). Für Haushalte bedeutet das: **Netzkosten künftig aktiv
beeinflussbar über die Wahl der Verbrauchszeiten.**

### Die 5 Kernelemente in Kürze

| # | Element | Kernidee | Auswirkung auf dich |
|---|---------|----------|---------------------|
| 1 | **Leistungspreis für alle** | Arbeits-/Leistungspreis schrittweise auf 50:50; Basis: **höchster 15-Minuten-Leistungswert des Monats** | Monatsspitze vermeiden werden belohnt; Übergang bis spätestens 1.7.2028, zunächst 10 kW frei zugestellt |
| 2 | **Zeitvariable Arbeitspreise** | **SNAP** (Apr–Sep, 10–16 Uhr) und **WiNAP** (Okt–Mär, 23–5 Uhr, ab Okt 2027): −20 % Netz-Arbeitspreis im Fenster | Verbrauch in die Sparfenster legen (Startzeitvorwahl) |
| 3 | **Flexible Netznutzung** | Opt-in: Netz steuert max. 2×2 h/Tag | Geringere Netzentgelte für zeit-unkritische Verbraucher (Boiler, Speicher) |
| 4 | **Neues Netzanschlussentgelt** | Einmalige, faire Beiträge; Anreize für netzdienliche Standorte | Bei Wallbox/Wärmepumpe/PV: früh anmelden, gut geplanten Standort wählen |
| 5 | **Systemdienliche Speicher** | 10 Varianten mit Reduktion/Befreiung von Netzentgelten | Speicher in SNAP/WiNAP laden, abends entladen |

### Wichtige Eckdaten

- SNAP-/WiNAP-Fenster gelten nur, wenn der Netzbetreiber die Smart-Meter-Daten
  **viertelstündlich** ablesen kann – die Datenbasis, die dieses Projekt archiviert
- Die konkreten Preise und Fenster legt die Systemnutzungsentgelt-Tarifverordnung
  (bzw. die Preissheets deines Netzbetreibers) fest; das Projekt bereitet die
  Kennzahlen dafür auf (15-min-Spitzen, Fenster-Anteile)
- Diese Darstellung ersetzt keine Energieberatung – Offizielles:
  [e-control.at – Neue Regeln für die Energiewende](https://www.e-control.at/konsumenten/neue-regeln-f%C3%BCr-die-energiewende)

## Automatisierung (Cron)

```cron
15 8 * * * /pfad/zu/my-E-Control/sync.sh
```

`sync.sh` aktualisiert Tages- **und** 15-Minuten-Archiv (`--include-qh`) und schreibt
das Log nach `sync.log`. Für den aktuellen Verbrauch der letzten 15 Minuten kann
`--latest` (z. B. alle 15 Minuten) zusätzlich in den Scheduler – z. B. auch als
n8n-Workflow – eingebunden werden.

## Wie es funktioniert

Das Portal ist eine React-SPA; die inoffiziellen API-Endpunkte liegen unter
`https://eservice.netzooe.at/service/v1.0`:

| Schritt | Request |
|---|---|
| Login | `POST /service/j_security_check` mit JSON `{j_username, j_password}` + Header `client-id: netzonline` |
| Session/XSRF | `GET /service/v1.0/session` setzt das `XSRF-TOKEN`-Cookie |
| Stammdaten | `GET /service/v1.0/dashboard`, `GET /service/v1.0/consumptions/profiles` |
| Messwerte | `POST /service/v1.0/consumptions/profile/active` mit `X-XSRF-TOKEN`-Header |
| CSV | `POST /service/v1.0/consumptions/profile/active/download` (`Accept: text/csv`) |

POST-Anfragen benötigen den `X-XSRF-TOKEN`-Header (Spring Security CSRF).

Die API liefert je nach angefragtem Zeitraum unterschiedliche Granularität:
`QUARTER_OF_AN_HOUR` (pro Tag anfragbar), `DAY` (Blöcke bis ~30 Tage) und `MONTH`
(längere Zeiträume). Das Skript fragt Tageswerte in 30-Tage-Blöcken ab und halbiert
Blöcke mit Datenlücken rekursiv. Monatswerte werden bei `MONTH`-Antworten nur als
volle Kalendermonate abgefragt, da der Server ansonsten nur die Teilsumme des
angefragten Zeitraums liefert.

## Verwandte Projekte

- [superbox-dev/netzooe_eservice_api](https://github.com/superbox-dev/netzooe_eservice_api) –
  Python-Wrapper (aiohttp) für dieselbe API
- [mrtnsch/netz-ooe-api-client](https://github.com/mrtnsch/netz-ooe-api-client) –
  Node.js/TypeScript-Client
- Home-Assistant-Integrationen:
  [VarChar42/hassio-netzooe-eservice](https://github.com/VarChar42/hassio-netzooe-eservice),
  [superbox-dev/netzooe_eservice](https://github.com/superbox-dev/netzooe_eservice),
  [Chaosflo/hacs-netzooe](https://github.com/Chaosflo/hacs-netzooe),
  [DerSepl1/netzooe-smartmeter](https://github.com/DerSepl1/netzooe-smartmeter),
  [VenzlMa/hassio-netzooe](https://github.com/VenzlMa/hassio-netzooe)

## Lizenz

[MIT](LICENSE)
