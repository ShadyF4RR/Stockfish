#!/usr/bin/env bash
# Usage: validate.sh BUILD_DIR
# Linux, Bash, Python >=3.9, coreutils, expect at /usr/bin/expect (upstream perft).
# Runs a UCI round-trip, upstream standard/Chess960 perft and reproducible searches.
# Exit 2 for usage, nonzero on any failure. Writes validation.json only on success.
set -euo pipefail
[[ $# == 1 ]] || { echo "Usage: $0 BUILD_DIR" >&2; exit 2; }
out=$(realpath -- "$1")
[[ -x /usr/bin/expect ]] || { echo 'Install expect at /usr/bin/expect' >&2; exit 1; }
expected=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["binary_sha256"])' "$out/build.json")
actual=$(sha256sum < "$out/stockfish")
[[ ${actual%% *} == "$expected" ]] || { echo 'Engine differs from build manifest' >&2; exit 1; }
[[ ! -e "$out/validation.json" ]] || { echo 'Validation already recorded' >&2; exit 1; }
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
ln -s "$out/stockfish" "$tmp/stockfish"
cd "$tmp"
(
    exec > >(tee "$out/validation.log") 2>&1
    expect -f - <<'TCL'
set timeout 60
proc require {pattern} {
    expect {
        -re $pattern {return}
        eof {puts stderr "Early EOF waiting for $pattern"; exit 1}
        timeout {puts stderr "Timeout waiting for $pattern"; exit 1}
    }
}
spawn ./stockfish
send "uci\n"
require {uciok\r?\n}
send "isready\n"
require {readyok\r?\n}
send "ucinewgame\nposition startpos\ngo depth 4 searchmoves e2e4\n"
require {info depth 4 }
require {bestmove e2e4( |\r?\n)}
send "quit\n"
expect {
    eof {}
    timeout {exit 1}
}
set status [wait]
if {[lindex $status 2] != 0 || [lindex $status 3] != 0 || [llength $status] != 4} {exit 1}
TCL
    timeout 12m bash "$out/upstream-tests/perft.sh"
    timeout 6m bash "$out/upstream-tests/reprosearch.sh"
)
python3 - "$out" "$expected" <<'PY'
import json, sys
from pathlib import Path

out = Path(sys.argv[1])
(out / 'validation.json').write_text(json.dumps({
    'commit': json.loads((out / 'build.json').read_text())['commit'],
    'binary_sha256': sys.argv[2], 'uci': 'passed', 'perft': 'passed',
    'reprosearch': 'passed',
}, indent=2) + '\n')
PY
