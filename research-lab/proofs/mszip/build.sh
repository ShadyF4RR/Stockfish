#!/usr/bin/env bash
# Build a reproducible self-contained launcher from exactly one official SF19 release binary.
set -euo pipefail
if [[ $# != 3 || ( $1 != linux && $1 != windows ) ]]; then
    echo "Usage: build.sh linux|windows OFFICIAL_ENGINE NEW_OUTPUT_DIR" >&2
    exit 2
fi
platform=$1
input=$(realpath -- "$2")
output=$(realpath -m -- "$3")
proof=$(cd -- "$(dirname -- "$0")" && pwd -P)
go_bin=$(command -v "${GO:-go}")
if [[ $("$go_bin" version) != "go version go"* ]]; then
    echo "Set GO to a Go 1.24+ toolchain binary." >&2
    exit 1
fi
name=stockfish19-mszip
if [[ $platform == linux ]]; then
    digest=0f83d24cc46d2c66c60f16001af5444873bc112b7d028594513426894c12da19
else
    name+=.exe
    digest=45bc8e4969147db9c2eb533810637994619bff0eacc81ccfd9854394901bcbd0
fi
if [[ $(sha256sum < "$input" | cut -d' ' -f1) != "$digest" ]]; then
    echo "Input differs from the verified official Stockfish 19 $platform release executable." >&2
    exit 1
fi
# Reserve the new output directory and stage all expensive operations outside it.
mkdir -m 700 -- "$output"
stage=$(mktemp -d -- "$(dirname -- "$output")/.mszip-build.XXXXXXXX")
trap 'rm -rf -- "$stage"; rmdir -- "$output" 2>/dev/null || true' EXIT
python3 "$proof/pack.py" "$input" "$stage/payload.cab" \
    --level "${MSZIP_LEVEL:-1}" --mem-level "${MSZIP_MEM_LEVEL:-4}" > "$stage/packed.json"
python3 "$proof/verify_cab.py" "$stage/payload.cab" "$(stat -c %s -- "$input")" "$digest" > "$stage/verified.json"
cp -- "$proof"/launcher/*.go "$proof/launcher/go.mod" "$stage/"
printf '%s\n' "$digest" > "$stage/payload.sha256"
(
    cd -- "$stage"
    env CGO_ENABLED=0 GOOS="$platform" GOARCH=amd64 GOAMD64=v1 GOTOOLCHAIN=local \
        GOENV=off GOFLAGS='' GOWORK=off GOPROXY=off GOSUMDB=off \
        "$go_bin" build -p 2 -trimpath -buildvcs=false -ldflags='-s -w -buildid=' -o "$name" .
)
python3 - "$stage" "$name" "$platform" "$("$go_bin" version)" <<'PY'
import hashlib, json, sys
from pathlib import Path
stage, name, platform, go = sys.argv[1:]
stage = Path(stage)
result = json.loads((stage / "packed.json").read_text())
result.update(json.loads((stage / "verified.json").read_text()))
binary = stage / name
digest = hashlib.sha256()
with binary.open("rb") as stream:
    while data := stream.read(1024 * 1024):
        digest.update(data)
result.update({"platform": platform + "/amd64", "launcher_file": name,
               "launcher_bytes": binary.stat().st_size, "launcher_sha256": digest.hexdigest(),
               "original_tag": "sf_19", "original_commit": "edb0d9db6731067ec50ce619ff372b463bc4dd5d",
               "go_version": go})
(stage / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
PY
mv -- "$stage/$name" "$stage/payload.cab" "$stage/manifest.json" "$output/"
cat -- "$output/manifest.json"
