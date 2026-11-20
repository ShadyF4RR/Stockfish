#!/usr/bin/env bash
# Usage, validation semantics, dependencies and exit codes are in ../promotion.md.
set -euo pipefail
exec python3 "$(dirname -- "$0")/promotion_preflight.py" "$@"
