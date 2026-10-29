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
python3 download_readings.py
```

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
    ├── days15/2026-10-28.csv              # 15-Minuten-Werte (nur mit --include-qh)
    ├── all_15min.csv                      # 15-Minuten-Werte, alle Tage
    └── months/2026-02.csv                 # Monatswerte bei Tageswert-Lücken
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

## Automatisierung (Cron)

```cron
15 8 * * * /pfad/zu/my-E-Control/sync.sh
```

`sync.sh` ruft das Skript auf und schreibt das Log nach `sync.log`. Für tagesaktuelle
15-Minuten-Werte kann `--latest` (z. B. alle 15 Minuten) in den Scheduler – z. B. auch
als n8n-Workflow – eingebunden werden.

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
