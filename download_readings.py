#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
my E-Control – Netz-OÖ-eService-Messwert-Downloader
===================================================
Lädt den täglichen Stromverbrauch (kWh/Tag) sowie optional die 15-Minuten-Werte
aus dem eService-Portal der Netz OÖ (https://eservice.netzooe.at) und speichert
sie als CSV ab – inkrementell: Tage, deren CSV bereits existiert, werden übersprungen.

API (inoffiziell, reverse-engineered aus dem Web-Portal):
  POST /service/j_security_check          – Login (Spring Security, JSON)
  GET  /service/v1.0/session              – Session-Check, setzt XSRF-TOKEN-Cookie
  GET  /service/v1.0/dashboard            – Verträge / Zählpunkte
  GET  /service/v1.0/consumptions/profiles– Profil-Historie je Zählpunkt
  POST /service/v1.0/consumptions/profile/active        – Messwerte (JSON)
  POST /service/v1.0/consumptions/profile/active/download – CSV (Accept: text/csv)

Keine externen Abhängigkeiten (nur Python-Standardbibliothek).
Ablauf:
  1. Einloggen (Credentials aus .env oder Kommandozeile/Umgebungsvariablen)
  2. Alle Zählpunkte + Profilzeiträume ermitteln
  3. Fehlende Tage in Blöcken à 30 Tagen anfragen (Tageswerte)
  4. Pro Tag eine CSV ablegen + Gesamt-CSV (all_days.csv) neu aufbauen

Ausgabe-Struktur (Standard: data/readings):
  <Zählpunkt-MA>/
    _meta.json                  Metadaten des letzten Laufs
    all_days.csv                Gesamt-CSV (alle Tage, semikolongetrennt, de-CH/de-AT Format)
    days/YYYY-MM-DD.csv         je ein File pro Tag (Tagesverbrauch)
    days15/YYYY-MM-DD.csv       je ein File pro Tag (15-Minuten-Werte, nur mit --include-qh)
    raw/…                       rohe Serverantworten bei unerwarteter Granularität
"""

from __future__ import annotations

import argparse
import calendar
import csv
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PORTAL = "https://eservice.netzooe.at"
SERVICE = f"{PORTAL}/service"
API = f"{SERVICE}/v1.0"
VERSION = "2026-10-29"
TZ = ZoneInfo("Europe/Vienna")

CHUNK_DAYS = 30          # maximale Reichweite, für die die API Tageswerte liefert
SLEEP_BETWEEN_REQUESTS = 0.35
MAX_RETRIES = 3

CSV_FIELDS = ["Datum", "kWh", "Status"]
CSV_DIALECT = "excel"     # Komma als Dezimaltrenner; wir trennen Felder mit ";"


# --------------------------------------------------------------------------- #
#  Fehler / Logging
# --------------------------------------------------------------------------- #
class EControlError(Exception):
    pass


class AuthError(EControlError):
    pass


def log(msg: str, level: str = "INFO") -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {level:7s} {msg}", flush=True)


# --------------------------------------------------------------------------- #
#  API-Client
# --------------------------------------------------------------------------- #
class NetzOOEClient:
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )
        self.opener.addheaders = [("User-Agent", "Mozilla/5.0")]
        self.xsrf_token: str | None = None
        self._logged_in = False

    # -- Low-level -----------------------------------------------------------
    def _request(self, method: str, path: str, *, json_body=None, accept="application/json",
                 headers: dict | None = None, retry: bool = True):
        h = {
            "Content-Type": "application/json",
            "Accept": accept,
            "client-id": "netzonline",
            "User-Agent": "Mozilla/5.0",
        }
        if headers:
            h.update(headers)
        if json_body is not None or method == "POST":
            h["X-XSRF-TOKEN"] = self.xsrf_token or ""
        data = json.dumps(json_body).encode() if json_body is not None else None

        req = urllib.request.Request(f"{SERVICE}{path}", data=data, headers=h,
                                     method=method)
        try:
            resp = self.opener.open(req, timeout=90)
            return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            # CSRF/Cookie-Refresh: Session neu holen (setzt neues XSRF) und einmal wiederholen
            if e.code == 401 and retry and self._logged_in:
                log("401 – versuche Session/XSRF zu erneuern …", "WARN")
                self._refresh_session()
                return self._request(method, path, json_body=json_body,
                                     accept=accept, headers=headers, retry=False)
            if e.code == 401:
                raise AuthError(f"HTTP {e.code} – nicht authentifiziert ({path})")
            if e.code == 404 and path == "/j_security_check":
                # Passwort-Hash-Flow: Session zurücksetzen und erneut senden
                raise AuthError("Login fehlgeschlagen (HTTP 404)")
            return e.code, e.read()

    # -- Login ---------------------------------------------------------------
    def _seed_cookies(self) -> None:
        try:
            req = urllib.request.Request(f"{PORTAL}/app/login",
                                         headers={"User-Agent": "Mozilla/5.0"})
            self.opener.open(req, timeout=60).read()
        except urllib.error.HTTPError:
            pass  # egal

    def _refresh_session(self) -> None:
        st, _ = self._request("GET", "/v1.0/session", retry=True)
        if st != 200:
            raise EControlError(f"Session-Refresh fehlgeschlagen (HTTP {st})")
        self._read_xsrf()

    def _read_xsrf(self) -> None:
        for c in self.cookies:
            if c.name == "XSRF-TOKEN" and c.value:
                self.xsrf_token = c.value
        if not self.xsrf_token:
            raise EControlError("Kein XSRF-TOKEN-Cookie erhalten")

    def login(self) -> None:
        self._seed_cookies()
        # Erst CSRF initialisieren, dann Login
        try:
            req = urllib.request.Request(
                f"{API}/session/csrf",
                headers={"User-Agent": "Mozilla/5.0", "client-id": "netzonline",
                         "Accept": "application/json"})
            self.opener.open(req, timeout=60).read()
        except urllib.error.HTTPError:
            pass

        st, body = self._request("POST", "/j_security_check",
                                 json_body={"j_username": self.username,
                                            "j_password": self.password},
                                 retry=False)
        if st != 200:
            raise AuthError(f"Login fehlgeschlagen (HTTP {st}, {body[:120]!r})")
        st, session = self._request("GET", "/v1.0/session")
        if st != 200:
            raise AuthError(f"Session nach Login nicht verfügbar (HTTP {st})")
        self._read_xsrf()
        self._logged_in = True
        try:
            user = json.loads(session).get("user", {})
            log(f"Angemeldet als {user.get('login')} ({user.get('name')})", "OK")
        except Exception:
            log("Angemeldet", "OK")

    # -- Daten ---------------------------------------------------------------
    def dashboard(self) -> dict:
        st, body = self._request("GET", "/v1.0/dashboard")
        if st != 200:
            raise EControlError(f"Dashboard-Fehler (HTTP {st})")
        return json.loads(body)

    def consumption_profiles(self, branch: str = "STROM", active_only: bool | None = None) -> list:
        q = urllib.parse.urlencode({"branch": branch,
                                    **({"activeOnly": str(active_only).lower()
                                        if active_only is not None else "false"})})
        st, body = self._request("GET", f"/v1.0/consumptions/profiles?{q}")
        if st != 200:
            raise EControlError(f"Profile-Fehler (HTTP {st})")
        return json.loads(body)

    def profile_values(self, req_body: dict) -> list[dict]:
        """POST /v1.0/consumptions/profile/active -> Liste von Messwert-Blöcken."""
        st, body = self._request("POST", "/v1.0/consumptions/profile/active",
                                 json_body=req_body)
        if st != 200:
            raise EControlError(f"Messwertabruf fehlgeschlagen (HTTP {st}, {body[:160]!r})")
        return json.loads(body)


# --------------------------------------------------------------------------- #
#  Datum/CSV-Hilfen
# --------------------------------------------------------------------------- #
def parse_nz_datetime(value: str) -> datetime:
    """Server liefert z. B. '2026-10-24T22:00:00Z' (UTC)."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def vienna_datestr(value: str) -> str:
    """Lokales Datum (Europe/Vienna) als ISO-String, z. B. '2026-10-25'."""
    return parse_nz_datetime(value).astimezone(TZ).strftime("%Y-%m-%d")


def fmt_nz(value: float) -> str:
    """Deutsche Dezimalnotation mit Komma."""
    return f"{value:.3f}".rstrip("0").rstrip(".").replace(".", ",") or "0"


def daterange_list(start: date, end: date) -> list[date]:
    out = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def csv_rows(rows: list[tuple[str, str, str]]) -> str:
    """Semikolongetrennte CSV im Portal-Format mit BOM."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(["Datum", "kWh", "Status"])
    for r in rows:
        w.writerow(r)
    return "\ufeff" + buf.getvalue()


def write_csv(path: str, rows: list[tuple[str, str, str]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # csv_rows liefert bereits ein BOM – daher hier normales utf-8 (kein utf-8-sig)
    with open(path, "w", encoding="utf-8") as f:
        f.write(csv_rows(rows))


def last_existing(out_dir: str, sub: str) -> set[date]:
    """Tage, die bereits als CSV auf der Platte liegen."""
    d = os.path.join(out_dir, sub)
    found: set[date] = set()
    if not os.path.isdir(d):
        return found
    for fn in os.listdir(d):
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.csv", fn)
        if m:
            try:
                found.add(date.fromisoformat(m.group(1)))
            except ValueError:
                pass
    return found


# --------------------------------------------------------------------------- #
#  Download-Logik
# --------------------------------------------------------------------------- #
def plan_missing(dates_all: list[date], have: set[date], force: bool) -> list[list[date]]:
    """Fehlende Tage in ≤ CHUNK_DAYS lange zusammenhängende Blöcke gruppieren."""
    missing = [d for d in dates_all if force or d not in have]
    if not missing:
        return []
    chunks, cur = [], []
    for d in missing:
        if cur and (d - cur[-1]).days != 1:
            chunks.append(cur)
            cur = []
        cur.append(d)
        if len(cur) >= CHUNK_DAYS:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def make_request_body(contract_account: str, meter_point: str, profile_type: str,
                      frm: date, to: date) -> dict:
    return {
        "dimension": "ENERGY",
        "pods": [{
            "contractAccountNumber": contract_account,
            "meterPointAdministrationNumber": meter_point,
            "type": profile_type,
            "timerange": {"from": frm.isoformat(), "to": to.isoformat()},
            "bestAvailableGranularity": "DAY",
        }],
    }


def fetch_daily_bisect(client: NetzOOEClient, pod_dir: str, account: str, meter_point: str,
                       profile_type: str, frm: date, to: date,
                       monthly_ranges: list[tuple[date, date]], stats: dict, depth: int = 0) -> None:
    """Tageswerte für frm..to laden; bei Lücken rekursiv halbieren, damit vorhandene
    Tageswerte (z. B. in Teilbereichen eines Monats mit Datenlücke) nicht verloren gehen."""
    span = (to - frm).days + 1
    body = make_request_body(account, meter_point, profile_type, frm, to)
    for attempt in range(MAX_RETRIES):
        try:
            blocks = client.profile_values(body)
            break
        except EControlError as e:
            if attempt == MAX_RETRIES - 1:
                log(f"  Fehler bei {frm}..{to}: {e} – übersprungen", "ERROR")
                stats["unexpected"] += span
                return
            log(f"  Retry {attempt + 1}/{MAX_RETRIES}: {e}", "WARN")
            time.sleep(2)
    else:
        return

    rows: dict[date, tuple[str, str, str]] = {}
    gran: str | None = None
    for block in blocks:
        gran = block.get("granularity")
        for v in block.get("profileValues") or []:
            d = date.fromisoformat(vienna_datestr(v["datetime"]))
            if d < frm or d > to or d in rows:
                continue
            rows[d] = (d.strftime("%d.%m.%Y %H:%M"), fmt_nz(v["value"]), v.get("status", ""))

    if gran == "DAY" and rows:
        for d, row in sorted(rows.items()):
            write_csv(os.path.join(pod_dir, "days", f"{d.isoformat()}.csv"), [row])
            stats["days"] += 1
        if len(rows) < span and span > 1:
            # Teilbereiche fehlen trotzdem – rekursiv nachladen
            for d in sorted(set(daterange_list(frm, to)) - set(rows)):
                fetch_daily_bisect(client, pod_dir, account, meter_point, profile_type,
                                   d, d, monthly_ranges, stats, depth + 1)
        time.sleep(SLEEP_BETWEEN_REQUESTS)
        return

    if span <= 7:
        # Kleiner Bereich ohne Tageswerte → Monatswerte-Fallback
        log(f"  {frm}..{to}: keine Tageswerte (Granularität {gran}) – Monatswerte werden ergänzt", "WARN")
        stats["unexpected"] += span
        self_save_raw(pod_dir, frm, to, blocks)
        monthly_ranges.append((frm, to))
        time.sleep(SLEEP_BETWEEN_REQUESTS)
        return

    # Größerer Bereich ohne Tageswerte → halbieren und rekursiv
    mid = frm + timedelta(days=(span - 1) // 2)
    log(f"  {frm}..{to}: keine Tageswerte ({gran}) – teile Bereich auf …", "INFO")
    time.sleep(SLEEP_BETWEEN_REQUESTS)
    fetch_daily_bisect(client, pod_dir, account, meter_point, profile_type,
                       frm, mid, monthly_ranges, stats, depth + 1)
    fetch_daily_bisect(client, pod_dir, account, meter_point, profile_type,
                       mid + timedelta(days=1), to, monthly_ranges, stats, depth + 1)


def download_contract_days(client: NetzOOEClient, entry: dict, out_dir: str,
                           since: date | None, until: date | None,
                           force: bool, include_qh: bool, dry_run: bool) -> dict:
    contract_number = entry["contractNumber"]
    meter_point = entry["meterPointAdministrationNumber"]
    account = entry["contractAccountNumber"]
    profiles = entry.get("profiles") or []
    pod_dir = os.path.join(out_dir, meter_point)
    stats = {"contract": contract_number, "days": 0, "qh_hours": 0, "skipped": 0,
             "unexpected": 0, "master": False}

    # Profil, das die meisten Tage abdeckt (ACTIVE_CURRENT bevorzugt)
    profiles.sort(key=lambda p: (p.get("profileType") != "ACTIVE_CURRENT",
                                 p.get("from") or ""))
    if not profiles:
        log(f"{meter_point} / Vertrag {contract_number}: keine Profile – übersprungen", "WARN")
        return stats

    prof = profiles[0]
    p_from = date.fromisoformat(prof["from"])
    p_to = date.fromisoformat(prof["to"])
    profile_type = prof["profileType"]

    start = since or p_from
    end = min(until or date.today(), p_to)
    if start > end:
        log(f"{meter_point}: Zeitraum {start}..{end} außerhalb des Profils – übersprungen", "INFO")
        return stats

    have = last_existing(pod_dir, "days")
    all_days = daterange_list(start, end)
    chunks = plan_missing(all_days, have, force)
    if chunks:
        log(f"{meter_point} / Vertrag {contract_number}: {len(all_days)} Tage im Zeitraum, "
            f"{sum(len(c) for c in chunks)} fehlen ({len(chunks)} Blöcke à ≤{CHUNK_DAYS} Tagen)")
    else:
        log(f"{meter_point} / Vertrag {contract_number}: bereits vollständig ({len(all_days)} Tage)", "OK")

    monthly_ranges: list[tuple[date, date]] = []
    for chunk in chunks:
        frm, to = chunk[0], chunk[-1]
        if dry_run:
            log(f"  [dry-run] würde {frm}..{to} laden ({len(chunk)} Tage)")
            stats["days"] += len(chunk)
            continue
        fetch_daily_bisect(client, pod_dir, account, meter_point, profile_type,
                           frm, to, monthly_ranges, stats, depth=0)

    # ---- Monatswerte NUR für Monate, in denen Tageswerte fehlen ----
    # Hinweis: Der Server liefert bei MONTH-Anfragen nur die Teilsumme des angefragten
    # Zeitraums – daher wird pro vollem Kalendermonat einzeln abgefragt.
    if not dry_run:
        missing_months = gap_months(pod_dir, start, end)
        if missing_months:
            have_months = last_existing_months(pod_dir)
            for month in sorted(missing_months):
                if month in have_months:
                    continue
                y, m = int(month[:4]), int(month[5:7])
                last_day = calendar.monthrange(y, m)[1]
                body = make_request_body(account, meter_point, profile_type,
                                         date(y, m, 1), date(y, m, last_day))
                body["pods"][0]["bestAvailableGranularity"] = "MONTH"
                try:
                    blocks = client.profile_values(body)
                except EControlError as e:
                    log(f"  Monatswerte {month}: {e} – übersprungen", "ERROR")
                    continue
                for block in blocks:
                    for v in block.get("profileValues") or []:
                        lbl = vienna_datestr(v["datetime"])[:7]
                        if lbl == month:
                            write_csv(os.path.join(pod_dir, "months", f"{month}.csv"),
                                      [(month, fmt_nz(v["value"]), v.get("status", ""))])
                time.sleep(SLEEP_BETWEEN_REQUESTS)
            rebuild_months(pod_dir)
            log(f"  Monatswerte für Lücken ergänzt: {', '.join(sorted(missing_months))}", "OK")

    # ---- 15-Minuten-Werte (optional) ----
    if include_qh:
        have_qh = last_existing(pod_dir, "days15")
        missing_qh = [d for d in all_days if force or d not in have_qh]
        if missing_qh:
            log(f"  {meter_point}: 15-Minuten-Werte für {len(missing_qh)} Tage laden …")
        for d in missing_qh:
            if dry_run:
                continue
            body = make_request_body(account, meter_point, profile_type, d, d)
            body["pods"][0]["bestAvailableGranularity"] = "QUARTER_OF_AN_HOUR"
            try:
                blocks = client.profile_values(body)
            except EControlError as e:
                log(f"  15-min {d}: {e} – übersprungen", "WARN")
                continue
            blk = blocks[0] if blocks else {}
            vals = blk.get("profileValues") or []
            if blk.get("granularity") == "QUARTER_OF_AN_HOUR" and vals:
                rows = [(
                    parse_nz_datetime(v["datetime"]).astimezone(TZ).strftime("%d.%m.%Y %H:%M"),
                    fmt_nz(v["value"]), v.get("status", "")) for v in vals]
                write_csv(os.path.join(pod_dir, "days15", f"{d.isoformat()}.csv"), rows)
                stats["qh_hours"] += len(rows)
            elif vals:
                log(f"  15-min {d}: Server lieferte {blk.get('granularity')} statt 15-min – übersprungen", "WARN")
            time.sleep(SLEEP_BETWEEN_REQUESTS)

    # ---- Gesamt-CSVs neu aufbauen ----
    if not dry_run:
        stats["master"] = rebuild_master(pod_dir)
        if include_qh:
            rebuild_all_qh(pod_dir)
    return stats


def gap_months(pod_dir: str, start: date, end: date) -> set[str]:
    """Monate im Zeitraum, in denen (noch) nicht alle Tage als Tageswert vorliegen."""
    have = last_existing(pod_dir, "days")
    missing_months: set[str] = set()
    d = start
    cur = None
    while d <= end:
        key = d.strftime("%Y-%m")
        if key != cur:
            cur = key
            y, m = d.year, d.month
            last_day = min(calendar.monthrange(y, m)[1], end.day if (y, m) == (end.year, end.month) else 31)
            month_days = [date(y, m, day) for day in range(1, last_day + 1)
                          if start <= date(y, m, day) <= end]
            if any(day not in have for day in month_days):
                missing_months.add(key)
        d += timedelta(days=1)
    return missing_months


def last_existing_months(pod_dir: str) -> set[str]:
    """Monate, die bereits als CSV in months/ liegen."""
    d = os.path.join(pod_dir, "months")
    found: set[str] = set()
    if not os.path.isdir(d):
        return found
    for fn in os.listdir(d):
        m = re.fullmatch(r"(\d{4}-\d{2})\.csv", fn)
        if m:
            found.add(m.group(1))
    return found


def rebuild_months(pod_dir: str) -> None:
    """months.csv aus den Monatsfiles neu aufbauen."""
    rows = []
    mdir = os.path.join(pod_dir, "months")
    if os.path.isdir(mdir):
        for fn in sorted(os.listdir(mdir)):
            m = re.fullmatch(r"(\d{4}-\d{2})\.csv", fn)
            if not m:
                continue
            with open(os.path.join(mdir, fn), encoding="utf-8-sig") as f:
                for line in f:
                    parts = [p.strip().strip('"').lstrip("\ufeff") for p in line.rstrip("\n").split(";") if p.strip()]
                    if len(parts) == 3 and parts[0] != "Datum":
                        rows.append(parts)
    if rows:
        write_csv(os.path.join(pod_dir, "months.csv"), rows)


def rebuild_master(pod_dir: str) -> bool:
    """Gesamt-CSV all_days.csv aus den Tagesfiles neu aufbauen."""
    day_dir = os.path.join(pod_dir, "days")
    rows = []
    if os.path.isdir(day_dir):
        for fn in sorted(os.listdir(day_dir)):
            m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.csv", fn)
            if not m:
                continue
            with open(os.path.join(day_dir, fn), encoding="utf-8-sig") as f:
                for line in f:
                    parts = [p.strip().strip('"').lstrip("\ufeff") for p in line.rstrip("\n").split(";") if p.strip()]
                    if len(parts) == 3 and parts[0] != "Datum":
                        rows.append(parts)
    if rows:
        write_csv(os.path.join(pod_dir, "all_days.csv"), rows)
        return True
    return False


def rebuild_all_qh(pod_dir: str) -> None:
    """all_15min.csv aus allen Tages-15min-Files neu aufbauen."""
    rows = []
    d15 = os.path.join(pod_dir, "days15")
    if os.path.isdir(d15):
        for fn in sorted(os.listdir(d15)):
            m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.csv", fn)
            if not m:
                continue
            with open(os.path.join(d15, fn), encoding="utf-8-sig") as f:
                for line in f:
                    parts = [p.strip().strip('"').lstrip("\ufeff") for p in line.rstrip("\n").split(";") if p.strip()]
                    if len(parts) == 3 and parts[0] != "Datum":
                        rows.append(parts)
    if rows:
        write_csv(os.path.join(pod_dir, "all_15min.csv"), rows)


def self_save_raw(pod_dir: str, frm: date, to: date, blocks) -> None:
    raw_dir = os.path.join(pod_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    with open(os.path.join(raw_dir, f"{frm}..{to}.json"), "w", encoding="utf-8") as f:
        json.dump(blocks, f, ensure_ascii=False, indent=1)


# --------------------------------------------------------------------------- #
#  Hauptprogramm
# --------------------------------------------------------------------------- #
def load_credentials(opts) -> tuple[str, str]:
    env = {}
    env_file = opts.env
    if os.path.isfile(env_file):
        for line in open(env_file, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    username = opts.username or env.get("NETZOOE_USERNAME") or os.environ.get("NETZOOE_USERNAME")
    password = opts.password or env.get("NETZOOE_PASSWORD") or os.environ.get("NETZOOE_PASSWORD")
    if not username or not password:
        raise EControlError(
            "Keine Credentials. Bitte .env anlegen (siehe .env.example) oder "
            "--username/--password übergeben.")
    return username, password


def show_latest(client: NetzOOEClient, window: int) -> None:
    """Zeigt die neuesten 15-Minuten-Werte (aktueller Verbrauch) ohne Download."""
    profiles = client.consumption_profiles(branch="STROM", active_only=None)
    # das Profil mit dem spätesten Ende wählen (aktuellster Vertrag)
    entries = [p for p in profiles if p.get("profiles")]
    if not entries:
        raise EControlError("Keine Messprofile gefunden")
    best = max(entries, key=lambda p: p["profiles"][0]["to"])
    prof = best["profiles"][0]
    meter_point = best["meterPointAdministrationNumber"]
    account = best["contractAccountNumber"]
    p_from = date.fromisoformat(prof["from"])
    p_to = date.fromisoformat(prof["to"])
    day = min(date.today(), p_to)
    qh = make_request_body(account, meter_point, prof["profileType"], day, day)
    qh["pods"][0]["bestAvailableGranularity"] = "QUARTER_OF_AN_HOUR"
    blocks = client.profile_values(qh)
    vals = (blocks[0].get("profileValues") or []) if blocks else []
    if not vals:
        print(f"Keine 15-Minuten-Werte für {day.isoformat()} (noch nicht übermittelt).")
        return
    print(f"Letzte 15-Minuten-Werte – Zähler {meter_point} ({day.strftime('%d.%m.%Y')}):")
    for v in vals[-window:]:
        start = parse_nz_datetime(v["datetime"]).astimezone(TZ)
        end = start + timedelta(minutes=15)
        mark = "  ← aktuellster Wert" if v is vals[-1] else ""
        print(f"  {start:%H:%M}–{end:%H:%M}   {fmt_nz(v['value']):>8} kWh  ({v.get('status', '')}){mark}")
    day_sum = sum(v.get("value") or 0 for v in vals)
    print(f"Tagesverbrauch bisher ({day.strftime('%d.%m.%Y')}): {fmt_nz(day_sum)} kWh ({len(vals)}/96 Viertelstunden)")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="download_readings.py",
        description="Netz-OÖ-eService: täglichen Stromverbrauch als CSV laden (inkrementell).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--username", help="eService-Login")
    p.add_argument("--password", help="eService-Passwort")
    p.add_argument("--env", default=".env", help="Pfad zur .env Datei mit NETZOOE_USERNAME/PASSWORD")
    p.add_argument("--output", default="data/readings", help="Ausgabeverzeichnis")
    p.add_argument("--since", help="Nur ab diesem Datum laden (YYYY-MM-DD, Default: Profilanfang)")
    p.add_argument("--until", help="Nur bis zu diesem Datum laden (YYYY-MM-DD, Default: heute/Profilende)")
    p.add_argument("--include-qh", action="store_true",
                   help="Zusätzlich 15-Minuten-Werte je Tag archivieren (days15/)")
    p.add_argument("--force", action="store_true", help="Bereits vorhandene Tage neu laden")
    p.add_argument("--dry-run", action="store_true", help="Nur zeigen, was geladen würde")
    p.add_argument("--latest", action="store_true",
                   help="Nur die neuesten 15-Minuten-Werte anzeigen (kein Download)")
    p.add_argument("--latest-window", type=int, default=6,
                   help="Wie viele Viertelstunden bei --latest angezeigt werden")
    args = p.parse_args(argv)

    since = date.fromisoformat(args.since) if args.since else None
    until = date.fromisoformat(args.until) if args.until else None

    try:
        username, password = load_credentials(args)
        client = NetzOOEClient(username, password)
        client.login()

        if args.latest:
            show_latest(client, args.latest_window)
            return 0

        dashboard = client.dashboard()
        accounts = dashboard.get("contractAccounts", [])
        profiles = client.consumption_profiles(branch="STROM", active_only=False)
        by_key = {}
        for prf in profiles:
            by_key[(prf["contractAccountNumber"], prf["contractNumber"])] = prf

        total_days, total_qh = 0, 0
        for account in accounts:
            if account.get("branch") != "STROM":
                continue
            ca = account["contractAccountNumber"]
            ncontracts = 0
            for contract in account.get("contracts", []):
                key = (ca, contract["contractNumber"])
                entry = by_key.get(key)
                if not entry:
                    continue
                ncontracts += 1
                stats = download_contract_days(client, entry, args.output,
                                               since, until, args.force,
                                               args.include_qh, args.dry_run)
                total_days += stats["days"]
                total_qh += stats["qh_hours"]
                if stats["unexpected"]:
                    log(f"  {entry['meterPointAdministrationNumber']}: "
                        f"{stats['unexpected']} Tage ohne Tageswerte (Details in raw/)", "WARN")
            if ncontracts == 0:
                log(f"Kontokonto {ca}: keine Messprofile – übersprungen", "WARN")

        if not args.dry_run:
            with open(os.path.join(args.output, "_last_run.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"updated": datetime.now(timezone.utc).isoformat(),
                           "days_written": total_days,
                           "quarter_hour_rows": total_qh,
                           "script": f"download_readings.py v{VERSION}"}, f,
                          ensure_ascii=False, indent=1)
        log(f"Fertig. {total_days} neue Tages-CSVs, {total_qh} 15-Minuten-Zeilen."
            + (" (dry-run)" if args.dry_run else ""))
        return 0
    except AuthError as e:
        log(str(e), "ERROR")
        return 2
    except EControlError as e:
        log(str(e), "ERROR")
        return 3
    except Exception as e:  # noqa: BLE001
        log(f"Unerwarteter Fehler: {type(e).__name__}: {e}", "ERROR")
        return 4


if __name__ == "__main__":
    sys.exit(main())