#!/usr/bin/env bash
# Linux graphical replay. Requires XBoard 4.9.1, PolyGlot 2.0.4 and a running
# X display (or use xvfb-run -a). Supply NATIVE, LAUNCHER and a NEW result dir.
# Set XBOARD_PIECES to the XBoard theme dir for a relocated installation.
set -euo pipefail
if [[ $# != 3 ]]; then echo "Usage: $0 NATIVE LAUNCHER NEW_OUTPUT_DIR" >&2; exit 2; fi
native=$(realpath "$1")
launcher=$(realpath "$2")
mkdir -- "$3"
out=$(realpath "$3")
cd "$out"
printf '%s\n' '7k/5Q2/6K1/8/8/8/8/8 w - - 0 1' > checkmate.fen
extra=()
if [[ -n ${XBOARD_PIECES:-} ]]; then extra=(-pieceImageDirectory "$XBOARD_PIECES"); fi
# The adapter translates XBoard protocol to UCI for both trusted executables.
# Home and cache here belong only to the throwaway GUI process.
timeout -k 5s 60s env HOME="$out" STOCKFISH_MSZIP_CACHE="$out/cache" xboard \
    -debug -saveSettingsOnExit false -popupExitMessage false -matchPause 100 \
    -fcp "$launcher" -fd "$out" -fUCI -scp "$native" -sd "$out" -sUCI \
    -adapterCommand 'polyglot -noini -ec "%fcp" -ed "%fd"' -mg 2 -tc 1 -depth 5 \
    -lpf checkmate.fen -sgf gui.pgn -soundProgram true "${extra[@]}" > gui.log 2>&1
python3 - "$native" "$launcher" <<'PY'
from pathlib import Path
import hashlib
import sys

def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

native, launcher = sys.argv[1:]
pgn = Path("gui.pgn").read_text()
trace = Path("xboard.debug").read_text()
wanted = "0f83d24cc46d2c66c60f16001af5444873bc112b7d028594513426894c12da19"
if digest(native) != wanted or digest(Path("cache") / ("stockfish-" + wanted)) != wanted:
    sys.exit("SF19 original/cached executable identity failed")
if native not in trace or launcher not in trace or trace.count("move f7g7") < 2:
    sys.exit("missing engine commands or expected moves in GUI trace")
if pgn.count("1. Qg7#") != 2 or pgn.count('[Result "1-0"]') != 2:
    sys.exit("missing two completed checkmates in GUI PGN")
print("XBoard ran both engines, swapped their colors, and saved two completed checkmates.")
PY
