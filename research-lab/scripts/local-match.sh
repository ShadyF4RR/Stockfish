#!/usr/bin/env bash
# Linux cgroup v2, Python 3.9+, Fastchess on PATH, two build.sh output dirs and a plain
# EPD/FEN book (one distinct position per line). Use --help for arguments.
# Exits 0 on a completed screen, 1 on invalid evidence, 2 on invalid input.
set -eu
exec python3 -B "$(dirname "$0")/local_match.py" "$@"
