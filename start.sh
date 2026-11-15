#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then echo "Not installed yet — running install.sh"; ./install.sh || exit 1; fi
exec .venv/bin/python run.py "$@"
