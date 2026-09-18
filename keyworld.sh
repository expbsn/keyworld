#!/bin/sh
# PYTHONUNBUFFERED: sonst sieht man bei Umleitung in eine Datei oder im
# Cron-Lauf bis zum Ende der Pipeline gar nichts.
PYTHONUNBUFFERED=1 exec "$(dirname "$0")/.venv/bin/python" -m keyworld.cli "$@"
