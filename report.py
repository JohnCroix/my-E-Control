#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
my E-Control – Auswertung der 15-Minuten-Messwerte
==================================================
Erzeugt aus dem lokalen 15-Minuten-Archiv (data/readings/<Zählpunkt>/days15/):

  1. Monatsbericht  – höchster 15-Minuten-Leistungswert (kW) je Monat mit Zeitpunkt,
                      Vergleich zum Vormonat und zum Vorjahr. Diese Kennzahl ist die
                      Basis des künftigen Leistungspreises (SNE-G-V, ab 2027).
  4. Lastprofil-Heatmaps:
       – Jahresprofil  : Ø Leistung (kW) je Monat × Stunde des Tages
       – Monatsprofil  : max. Leistung (kW) je Tag × Stunde (zeigt Spitzen-Cluster)

Konsolenausgabe + eigenständiges HTML-Dashboard (keine Abhängigkeiten, offline
im Browser zu öffnen). Benötigt das 15-Minuten-Archiv (download_readings.py
--include-qh).

Nutzung:
  python3 report.py                      # Bericht für alle Zählpunkte unter data/readings
  python3 report.py --month 2026-02      # fokussiert einen Monat (inkl. Detail-Heatmap)
  python3 report.py --no-html            # nur Konsolenausgabe
"""

from __future__ import annotations

import argparse
import calendar
import glob
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from html import escape

DATA_DIR = "data/readings"
KW_PER_KWH_15MIN = 4.0  # kWh/15min → kW

# --- Tarif/Förder-Fenster laut SNE-G-V (E-Control) ---
SNAP_MONTHS = frozenset({4, 5, 6, 7, 8, 9})            # Sommer-Nieder-Arbeitspreis Apr–Sep
SNAP_HOURS = frozenset(range(10, 16))                  # 10:00–16:00 Uhr
WINAP_MONTHS = frozenset({1, 2, 3, 10, 11, 12})        # Winter-Nieder-Arbeitspreis Okt–Mär
WINAP_HOURS = frozenset(range(23, 24)) | frozenset(range(0, 5))  # 23:00–05:00 Uhr
EVENING_HOURS = frozenset({17, 18, 19})                # kritische Abendstunden
MIDDAY_HOURS = frozenset({11, 12, 13})                 # Mittagsfenster
WEEKDAY_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag",
              "Freitag", "Samstag", "Sonntag"]
ECONTROL_URL = ("https://www.e-control.at/konsumenten/"
                "neue-regeln-f%C3%BCr-die-energiewende")


# --------------------------------------------------------------------------- #
#  Daten lesen
# --------------------------------------------------------------------------- #
def load_quarter_hours(meter_dir: str) -> list[tuple[datetime, float]]:
    """Liest alle days15/*.csv (lokalzeit, kWh/15min) eines Zählpunkts."""
    rows: list[tuple[datetime, float]] = []
    files = sorted(glob.glob(os.path.join(meter_dir, "days15", "*.csv")))
    for f in files:
        with open(f, encoding="utf-8-sig") as fh:
            for line in fh:
                parts = [p.strip().strip('"') for p in line.rstrip("\n").split(";") if p.strip()]
                if len(parts) == 3 and parts[0] != "Datum":
                    dt = datetime.strptime(parts[0], "%d.%m.%Y %H:%M")
                    rows.append((dt, float(parts[1].replace(",", "."))))
    return rows


def build_monthly(rows: list[tuple[datetime, float]]) -> dict[str, dict]:
    """Monat → {peak_kw, peak_dt, kwh, days}."""
    months: dict[str, dict] = {}
    monthly_kwh = defaultdict(float)
    days_in_month = defaultdict(set)
    peak_by_month: dict[str, tuple[datetime, float]] = {}

    for dt, kwh in rows:
        key = dt.strftime("%Y-%m")
        monthly_kwh[key] += kwh
        days_in_month[key].add(dt.date())
        kw = kwh * KW_PER_KWH_15MIN
        if key not in peak_by_month or kw > peak_by_month[key][1]:
            peak_by_month[key] = (dt, kw)

    for key in sorted(peak_by_month):
        months[key] = {
            "peak_kw": peak_by_month[key][1],
            "peak_dt": peak_by_month[key][0],
            "kwh": monthly_kwh[key],
            "days": len(days_in_month[key]),
        }
    return months


# --------------------------------------------------------------------------- #
#  Konsolen-Ausgabe
# --------------------------------------------------------------------------- #
def print_monthly_report(months: dict[str, dict]) -> None:
    """Tabelle: Monat | Spitze kW | Zeitpunkt | kWh | Δ Vormonat | Δ Vorjahr."""
    keys = sorted(months)
    print(f"{'Monat':8} {'Spitze':>7} {'Zeitpunkt':<18} {'kWh':>8} "
          f"{'Δ Vor-Monat':>11} {'Δ Vorjahr':>11}")
    print("-" * 78)
    for i, key in enumerate(keys):
        m = months[key]
        prev = months.get(keys[i - 1]) if i > 0 else None
        year_ago = months.get(f"{int(key[:4]) - 1}{key[4:]}") if i > 0 else None
        d_prev = f"{m['peak_kw'] - prev['peak_kw']:+.2f} kW" if prev else "—"
        d_year = f"{m['peak_kw'] - year_ago['peak_kw']:+.2f} kW" if year_ago else "—"
        print(f"{key:8} {m['peak_kw']:6.2f} kW {m['peak_dt']:%d.%m.%Y %H:%M} {m['kwh']:8.0f} "
              f"{d_prev:>11} {d_year:>11}")


def last_complete_month(months: dict[str, dict]) -> str | None:
    """Letzter Monat, dessen Tagesanzahl dem Kalendermonat entspricht (vollständig)."""
    for key in sorted(months, reverse=True):
        y, m = int(key[:4]), int(key[5:7])
        if months[key]["days"] >= calendar.monthrange(y, m)[1]:
            return key
    return None


def print_month_detail(months: dict[str, dict], month: str) -> None:
    m = months.get(month)
    if not m:
        print(f"Keine Daten für {month} (verfügbar: {', '.join(sorted(months)) or '–'})")
        return
    print(f"\nMonat {month}: Spitze {m['peak_kw']:.2f} kW am {m['peak_dt']:%d.%m.%Y %H:%M} "
          f"({m['peak_dt']:%a}), Verbrauch {m['kwh']:.0f} kWh in {m['days']} Tagen")


# --------------------------------------------------------------------------- #
#  Aggregationen für die Heatmaps
# --------------------------------------------------------------------------- #
def year_heatmap(rows: list[tuple[datetime, float]]) -> dict[tuple[int, int], tuple[float, int]]:
    """(Monat 1..12, Stunde 0..23) → (Σ kW, Anzahl) – Durchschnitt fürs Jahresprofil."""
    agg: dict[tuple[int, int], tuple[float, int]] = defaultdict(lambda: (0.0, 0))
    for dt, kwh in rows:
        kw = kwh * KW_PER_KWH_15MIN
        s, n = agg[(dt.month, dt.hour)]
        agg[(dt.month, dt.hour)] = (s + kw, n + 1)
    return dict(agg)


def month_heatmap(rows: list[tuple[datetime, float]], month: str) -> dict[tuple[int, int], dict]:
    """(Tag, Stunde) → max kW + Zeitpunkt des Maximums (fürs Monatsprofil)."""
    agg: dict[tuple[int, int], dict] = {}
    for dt, kwh in rows:
        if dt.strftime("%Y-%m") != month:
            continue
        kw = kwh * KW_PER_KWH_15MIN
        key = (dt.day, dt.hour)
        cur = agg.get(key)
        if cur is None or kw > cur["kw"]:
            agg[key] = {"kw": kw, "dt": dt}
    return agg


def heat_color(ratio: float) -> str:
    """Grün → Gelb → Rot."""
    ratio = max(0.0, min(1.0, ratio))
    if ratio < 0.5:
        t = ratio * 2
        r = 46 + (241 - 46) * t
        g = 204 + (196 - 204) * t
        b = 113 + (15 - 113) * t
    else:
        t = (ratio - 0.5) * 2
        r = 241 + (231 - 241) * t
        g = 196 + (76 - 196) * t
        b = 15 + (60 - 15) * t
    return f"rgb({r:.0f},{g:.0f},{b:.0f})"


# --------------------------------------------------------------------------- #
#  Empfehlungen (SNE-G-V)
# --------------------------------------------------------------------------- #
def advisor_facts(rows: list[tuple[datetime, float]], months: dict[str, dict]) -> dict:
    """Kennzahlen für die persönlichen Empfehlungen aus den Messdaten."""
    total = sum(k for _, k in rows)

    def window(months_: frozenset, hours_: frozenset) -> float:
        return sum(k for dt, k in rows if dt.month in months_ and dt.hour in hours_)

    # Ø Leistung je Stunde (alle verfügbaren Jahre)
    hag: dict[int, list] = defaultdict(lambda: [0.0, 0])
    for dt, kwh in rows:
        hag[dt.hour][0] += kwh * KW_PER_KWH_15MIN
        hag[dt.hour][1] += 1
    hours_avg = sorted(((h, s / n) for h, (s, n) in hag.items()), key=lambda x: -x[1])

    peaks = [m["peak_kw"] for m in months.values()]
    peak_hour = Counter(m["peak_dt"].hour for m in months.values())
    peak_day = Counter(WEEKDAY_DE[m["peak_dt"].weekday()] for m in months.values())

    return {
        "total": total,
        "snap": window(SNAP_MONTHS, SNAP_HOURS),
        "winap": window(WINAP_MONTHS, WINAP_HOURS),
        "evening": window(frozenset(range(1, 13)), EVENING_HOURS),
        "midday": window(frozenset(range(1, 13)), MIDDAY_HOURS),
        "hours_top3": hours_avg[:3],
        "hours_flat3": hours_avg[-3:],
        "peak_max": max(peaks), "peak_min": min(peaks),
        "peak_avg": sum(peaks) / len(peaks) if peaks else 0.0,
        "peak_hour": peak_hour.most_common(1)[0][0] if peak_hour else None,
        "peak_day": peak_day.most_common(1)[0][0] if peak_day else None,
        "over10": any(p >= 10.0 for p in peaks),
    }


def fmt_german(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def advisor_items(facts: dict) -> list[tuple[str, str]]:
    """Persönliche Empfehlungstexte – (Überschrift, Text) mit echten Zahlen aus den Daten."""
    items: list[tuple[str, str]] = []
    pct = lambda v: f"{100 * v / facts['total']:.0f} %".replace(".", ",")
    hour_name = lambda h: f"{h:02d}–{h + 1:02d} Uhr"

    items.append(("Leistungsspitze im Griff halten",
                  f"Deine höchste Monatsspitze lag bei {fmt_german(facts['peak_max'])} kW "
                  f"(Durchschnitt {fmt_german(facts['peak_avg'])} kW, Minimum {fmt_german(facts['peak_min'])} kW). "
                  + ("Das bleibt klar unter der 10-kW-Zugangsgrenze des neuen Systems – gut."
                     if not facts['over10'] and facts['peak_max'] < 8 else
                     "Achtung: Spitzen in Richtung 10 kW (kostenlose Zugangsgrenze) – "
                     "große Geräte zeitlich staffeln.")
                  + " Künftig zahlt die höchste 15-Minuten-Spitze des Monats (Leistungspreis)."))

    if facts["peak_day"] and facts["peak_hour"] is not None:
        items.append(("Wann deine Spitzen entstehen",
                      f"Deine Monatsspitzen treten am häufigsten {facts['peak_day']} um "
                      f"{facts['peak_hour']:02d}:00 Uhr auf. An genau diesen Tagen/Stunden "
                      f"Backofen, Wasserkocher, E-Auto-Laden und Wärmepumpe nicht gleichzeitig "
                      f"laufen lassen und Ladevorgänge auf später verschieben."))

    top = [hour_name(h) for h, _ in facts["hours_top3"]]
    items.append(("Stunden mit höchster Last",
                  f"Deine am stärksten ausgelasteten Stunden sind {', '.join(top)}. "
                  f"Hier konzentrieren sich Koch- und Haushaltsstrom – Geräte mit "
                  f"Startzeitvorwahl (Spülmaschine, Waschmaschine, Trockner) auf "
                  f"andere Zeiten legen."))

    flat = [hour_name(h) for h, _ in facts["hours_flat3"]]
    items.append(("Entlastete Nachtstunden",
                  f"Am schwächsten belastet sind {', '.join(flat)}. Zeit-unkritische "
                  f"Verbraucher (Boiler-Vorheizen, E-Auto, Akku-Speicher) gehören dorthin "
                  f"bzw. in die günstigen Fenster unten."))

    items.append(("Günstige Fenster nutzen (SNAP/WiNAP)",
                  f"Schon jetzt liegen {fmt_german(100 * facts['snap'] / facts['total'])} % im "
                  f"Sommer-Sparfenster (Apr–Sep 10–16 Uhr, −20 % Netz-Arbeitspreis) und "
                  f"{fmt_german(100 * facts['winap'] / facts['total'])} % im Winter-Sparfenster "
                  f"(Okt–Mär 23–5 Uhr). Im Abendfenster 17–20 Uhr verbrauchst du "
                  f"{fmt_german(facts['evening'])} kWh ({pct(facts['evening'])} des Gesamtverbrauchs) – "
                  f"das ist das größte Verschiebepotenzial. Waschmaschine, Trockner und "
                  f"Geschirrspüler per Startzeitvorwahl in die Sparfenster legen."))

    items.append(("Flexible Entnahme (Opt-in)",
                  f"Wer sich auf flexible Netznutzung einlässt (max. 2×2 Stunden pro Tag "
                  f"steuerbar), bekommt günstigere Netzentgelte. Sinnvoll für Verbraucher ohne "
                  f"Zeitbindung (Boiler, Speicher, Heizstab) – prüfen, ob dein Netzbetreiber "
                  f"das anbietet."))

    items.append(("Speicher systemdienlich betreiben",
                  f"Batterie-Speicher in den günstigen Fenstern laden (SNAP/WiNAP), abends "
                  f"entladen. Systemdienlicher Betrieb öffnet 10 Vergünstigungs-Varianten "
                  f"(Reduktion/Befreiung von Netznutzungs- und Netzverlustentgelt)."))

    items.append(("Neuanschlüsse rechtzeitig planen",
                  f"Bei Wallbox, Wärmepumpe oder PV-Anlage: Anmeldung frühzeitig, "
                  f"netzdienlicher Standort (vorhandene Netzkapazität) wählt – das neue "
                  f"Netzanschlussentgelt belohnt genau das."))
    return items


APPLIANCES = [
    ("E-Auto (Wallbox)", "3,7–22 kW", "23–05 Uhr (Winter) / 10–16 Uhr (Sommer); nie parallel zu Wärmepumpe + Boiler"),
    ("Wärmepumpe", "1,5–4 kW", "Zeitprogramm nutzen; nicht mit Wallbox oder Boiler gleichzeitig"),
    ("Boiler (Speicher)", "2–3 kW", "Aufheizen in die Nacht bzw. Mittagsfenster legen"),
    ("Backofen / Ceranfeld", "2–4 kW", "Nicht mit anderen Großverbrauchern kollidieren (Staffelung)"),
    ("Waschmaschine / Trockner", "2–3,5 kW", "Startzeitvorwahl auf 10–16 Uhr (Sommer) bzw. 23–05 Uhr (Winter)"),
    ("Geschirrspüler", "1–2 kW", "Nacht- oder Mittagsprogramm"),
]

FIVE_ELEMENTS = [
    ("1. Leistungspreis für alle",
     "Monatliche 15-min-Leistungsspitze wird bepreist; Ziel 50:50 Arbeits-/Leistungspreis bis 2030",
     "Spitzen vermeiden: große Verbraucher staffeln; 15-min-Werte (↑ Heatmaps) beobachten"),
    ("2. Zeitvariable Arbeitspreise",
     "SNAP (Apr–Sep, 10–16 Uhr) und WiNAP (Okt–Mär, 23–05 Uhr), −20 % Netz-Arbeitspreis",
     "Verbrauch per Startzeitvorwahl in die Sparfenster legen"),
    ("3. Flexible Netznutzung",
     "Opt-in: Netz steuert max. 2×2 h/Tag, dafür günstigere Netzentgelte",
     "Zeit-unkritische Verbraucher (Boiler, Speicher) anmelden"),
    ("4. Neues Netzanschlussentgelt",
     "Einmalige, faire Beiträge; Anreize für netzdienliche Standorte",
     "Wallbox/WP/PV früh anmelden; Standort mit Netzkapazität wählen"),
    ("5. Systemdienliche Speicher",
     "10 Varianten mit Reduktion/Befreiung von Netzentgelten",
     "Speicher in SNAP/WiNAP laden, abends entladen"),
]


# --------------------------------------------------------------------------- #
#  HTML-Dashboard
# --------------------------------------------------------------------------- #
def _advisor_html(facts: dict) -> str:
    """Empfehlungs-Sektion fürs HTML-Dashboard (faktenbasiert, erklärt die SNE-G-V)."""
    items = advisor_items(facts)
    tips = []
    for i, (title, text) in enumerate(items):
        tips.append(f"<li{' class="warn"' if i == 0 else ''}><b>{escape(title)}:</b> {escape(text)}</li>")
    app_rows = "".join(
        f"<tr><td>{escape(g)}</td><td>{escape(p)}</td><td>{escape(t)}</td></tr>"
        for g, p, t in APPLIANCES)
    el_rows = "".join(
        f"<tr><td><b>{escape(n)}</b></td><td>{escape(k)}</td><td>{escape(a)}</td></tr>"
        for n, k, a in FIVE_ELEMENTS)
    return f"""<h2>Empfehlungen &amp; Erklärungen zur neuen Netzentgelt-Verordnung (SNE-G-V)</h2>
<p class="muted">Basierend auf deinen Messdaten · offizielle Infos: <a href="{ECONTROL_URL}">
e-control.at – Neue Regeln für die Energiewende</a></p>
<ul class="tips">{''.join(tips)}</ul>
<h3>Geräte &amp; empfohlene Zeitfenster</h3>
<table class="g"><thead><tr><th>Gerät</th><th>typ. Leistung</th><th>Empfehlung</th></tr></thead>
<tbody>{app_rows}</tbody></table>
<h3>Die 5 Kernelemente – kurz erklärt</h3>
<table class="g"><thead><tr><th>Element</th><th>Kernidee</th><th>Was du tun kannst</th></tr></thead>
<tbody>{el_rows}</tbody></table>"""


def build_html(meter_point: str, months: dict[str, dict], rows: list[tuple[datetime, float]],
               focus_month: str | None, year: int, facts: dict) -> str:
    keys = sorted(months)
    y_keys = [k for k in keys if int(k[:4]) == year]

    # ---- Monatsübersicht ----
    rows_html = []
    for i, key in enumerate(keys):
        m = months[key]
        prev = months.get(keys[i - 1]) if i > 0 else None
        year_ago = months.get(f"{int(key[:4]) - 1}{key[4:]}")
        d_prev = f"{m['peak_kw'] - prev['peak_kw']:+.2f}" if prev else "–"
        d_year = f"{m['peak_kw'] - year_ago['peak_kw']:+.2f}" if year_ago else "–"
        cls_p = "down" if (prev and m["peak_kw"] > prev["peak_kw"]) else "up"
        rows_html.append(
            f"<tr><td><b>{key}</b></td>"
            f"<td>{m['peak_kw']:.2f} kW</td>"
            f"<td>{m['peak_dt']:%d.%m.%Y %H:%M} ({m['peak_dt']:%a})</td>"
            f"<td>{m['kwh']:.0f} kWh</td>"
            f"<td class=\"{cls_p}\">{d_prev} kW</td>"
            f"<td class=\"{cls_p}\">{d_year} kW</td></tr>")
    peak_table = (
        "<table><thead><tr><th>Monat</th><th>Spitze (15&nbsp;min)</th><th>Zeitpunkt</th>"
        "<th>Verbrauch</th><th>Δ Vormonat</th><th>Δ Vorjahr</th></tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody></table>")

    # ---- Jahresprofil: Monat × Stunde, Ø kW (nur Werte des gewählten Jahres) ----
    agg = year_heatmap([r for r in rows if r[0].year == year])
    max_avg = 0.0
    if y_keys:
        all_avg = [agg[(m, h)][0] / agg[(m, h)][1] for m, h in agg if m <= 12]
        max_avg = max(all_avg) if all_avg else 0.0
    year_rows = []
    month_idx = {1: "Jan", 2: "Feb", 3: "Mär", 4: "Apr", 5: "Mai", 6: "Jun",
                 7: "Jul", 8: "Aug", 9: "Sep", 10: "Okt", 11: "Nov", 12: "Dez"}
    for mo in range(1, 13):
        cells = []
        for h in range(24):
            s, n = agg.get((mo, h), (0.0, 0))
            avg = s / n if n else 0.0
            color = heat_color(avg / max_avg) if max_avg else "transparent"
            title = f"{month_idx[mo]} {h:02d}:00–{h:02d}:59 · Ø {avg:.2f} kW ({n} Werte)"
            cells.append(f"<td style=\"background:{color}\" title=\"{escape(title)}\">{avg:.2f}</td>")
        year_rows.append(f"<tr><th>{month_idx[mo]}</th>{''.join(cells)}</tr>")
    year_header = "".join(f"<th>{h:02d}</th>" for h in range(24))
    year_table = (f"<table><thead><tr><th>Monat \\ Stunde</th>{year_header}</tr></thead>"
                  f"<tbody>{''.join(year_rows)}</tbody></table>")

    # ---- Monatsprofil: Tag × Stunde, max kW (nur wenn 15-min-Daten vorhanden) ----
    month_table = ""
    if focus_month and focus_month in months:
        mh = month_heatmap(rows, focus_month)
        max_kw = max((v["kw"] for v in mh.values()), default=0.0)
        m_rows = []
        for day in range(1, 32):
            if not any((day, h) in mh for h in range(24)):
                continue
            cells = []
            for h in range(24):
                v = mh.get((day, h))
                if v is None:
                    cells.append("<td class=\"empty\"></td>")
                    continue
                color = heat_color(v["kw"] / max_kw) if max_kw else "transparent"
                title = (f"{focus_month}-{day:02d} {h:02d}:00–{h:02d}:59 · "
                         f"max {v['kw']:.2f} kW (um {v['dt']:%H:%M})")
                cells.append(f"<td style=\"background:{color}\" title=\"{escape(title)}\">{v['kw']:.2f}</td>")
            m_rows.append(f"<tr><th>{day:02d}.</th>{''.join(cells)}</tr>")
        month_table = (f"<h2>Monatsprofil {focus_month} – max kW je Tag × Stunde</h2>"
                       f"<table><thead><tr><th>Tag \\ Stunde</th>{year_header}</tr></thead>"
                       f"<tbody>{''.join(m_rows)}</tbody></table>")

    css = """
    body{font-family:system-ui,Segoe UI,Roboto,sans-serif;margin:2rem;color:#222}
    h1{font-size:1.5rem}h2{font-size:1.15rem;margin-top:2rem}
    table{border-collapse:collapse;font-size:13px;margin:1rem 0}
    td,th{border:1px solid #ddd;padding:2px 6px;text-align:right;white-space:nowrap}
    th{background:#f4f4f4;font-weight:600}
    td.empty{background:#fbfbfb}
    .down{color:#c0392b}.up{color:#27ae60}
    p.muted{color:#666;font-size:12px}
    ul.tips{list-style:none;padding:0}
    ul.tips li{background:#f7f9fb;border-left:4px solid #2ecc71;padding:8px 12px;
               margin:6px 0;border-radius:0 4px 4px 0;font-size:14px;line-height:1.45}
    ul.tips li.warn{border-left-color:#e67e22}
    table.g{text-align:left}
    table.g th, table.g td{text-align:left;padding:4px 10px}
    """
    advisor_html = _advisor_html(facts)
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<title>my E-Control – Auswertung {escape(meter_point)}</title>
<style>{css}</style></head><body>
<h1>Stromlast-Auswertung – Zähler {escape(meter_point)}</h1>
<p class="muted">Stand: {now} · Quelle: lokales 15-Minuten-Archiv (kWh/15min → kW × 4) ·
Basis für den künftigen Leistungspreis (SNE-G-V): höchster 15-Minuten-Leistungswert je Monat</p>
{advisor_html}
<h2>Monatsübersicht – Leistungsspitzen (15&nbsp;min)</h2>
{peak_table}
<h2>Jahresprofil {year} – Ø Leistung (kW) je Monat × Stunde</h2>
{year_table}
{month_table}
</body></html>"""


# --------------------------------------------------------------------------- #
#  Hauptprogramm
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="report.py",
                                description="Auswertung der 15-Minuten-Messwerte "
                                            "(Monatsbericht + Lastprofil-Heatmaps)")
    p.add_argument("--data", default=DATA_DIR, help="Verzeichnis mit den Zählpunkt-Ordnern "
                                                   "(Default: data/readings)")
    p.add_argument("--month", help="Fokussierter Monat YYYY-MM (Detail-Heatmap)")
    p.add_argument("--year", type=int, default=datetime.now().year,
                   help="Jahr des Jahresprofils (Default: aktuelles Jahr)")
    p.add_argument("--no-html", action="store_true", help="Nur Konsolenausgabe, kein HTML")
    args = p.parse_args(argv)

    meter_dirs = sorted(d for d in glob.glob(os.path.join(args.data, "*"))
                        if os.path.isdir(d) and not os.path.basename(d).startswith("_"))
    if not meter_dirs:
        print(f"Keine Zählpunkt-Ordner unter {args.data} gefunden.", file=sys.stderr)
        return 1

    if args.month:
        args.year = int(args.month[:4])

    for meter_dir in meter_dirs:
        if not os.path.isdir(os.path.join(meter_dir, "days15")):
            print(f"{meter_dir}: kein 15-Minuten-Archiv (days15/) – "
                  f"zuerst 'python3 download_readings.py --include-qh'", file=sys.stderr)
            continue
        meter_point = os.path.basename(meter_dir)
        print(f"\n=== Zähler {meter_point} ===")
        rows = load_quarter_hours(meter_dir)
        if not rows:
            print("Keine Daten.", file=sys.stderr)
            continue
        months = build_monthly(rows)
        print_monthly_report(months)
        focus_month = args.month or last_complete_month(months)
        if focus_month:
            print_month_detail(months, focus_month)

        facts = advisor_facts(rows, months)
        print("\nEmpfehlungen (SNE-G-V / neue Netzentgeltregeln):")
        for title, text in advisor_items(facts):
            print(f"  • {title}: {text}")

        if not args.no_html:
            html = build_html(meter_point, months, rows, focus_month, args.year, facts)
            out = os.path.join(meter_dir, "report.html")
            with open(out, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"\nHTML-Dashboard: {os.path.abspath(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
