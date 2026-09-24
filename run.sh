#!/usr/bin/env bash
cd "$(dirname "$0")"
[ -f .venv/bin/python ] || ./setup.sh
exec ./.venv/bin/python main.py "$@"
