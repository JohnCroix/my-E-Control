#!/usr/bin/env bash
# my E-Control – Netz-OÖ-Messwerte-Update (für Cron & Co)
# Ruft download_readings.py auf und schreibt Log nach sync.log
set -uo pipefail
cd "$(dirname "$0")" || exit 1
python3 -u download_readings.py >> sync.log 2>&1
exit $?