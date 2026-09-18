#!/bin/sh
exec "$(dirname "$0")/.venv/bin/python" -m keyworld.cli "$@"
