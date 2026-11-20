#!/usr/bin/env bash
# Usage: build.sh SOURCE_WORKTREE NEW_BUILD_DIR
# Linux, Bash, Git, tar, make, GCC/g++, coreutils and Python >=3.9.
# Requires upstream NNUE download access or verified files in NNUE_DIR (default SOURCE/src).
# Exit 2 for usage, nonzero on dependency, build, hash or manifest failure.
set -euo pipefail
[[ $# == 2 ]] || { echo "Usage: $0 SOURCE_WORKTREE NEW_BUILD_DIR" >&2; exit 2; }
source_root=$(git -C "$1" rev-parse --show-toplevel)
commit=$(git -C "$source_root" rev-parse HEAD)
date=$(git -C "$source_root" show -s --format=%cd --date=format:%Y%m%d "$commit")
jobs=${JOBS:-2}
arch=${ARCH:-x86-64-avx2}
[[ $jobs =~ ^[1-4]$ && $arch =~ ^[a-z0-9-]+$ ]] || { echo "Invalid ARCH or JOBS (1..4)" >&2; exit 2; }
if [[ -z ${ARCH:-} && $(uname -m) != x86_64 ]]; then
    echo "Set ARCH to an upstream target for this CPU" >&2; exit 2
fi

# Build the committed snapshot, independently of generated files and worktree changes.
mkdir -- "$2"
out=$(realpath -- "$2")
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
git -C "$source_root" archive "$commit" src scripts tests | tar -x -C "$stage"
net_dir=${NNUE_DIR:-"$source_root/src"}
mapfile -t nets < <(sed -nE 's/^#define EvalFileDefaultName[^ ]* "(nn-[0-9a-f]{12}\.nnue)".*/\1/p' "$stage/src/evaluate.h")
(( ${#nets[@]} )) || { echo 'Missing pinned network declaration' >&2; exit 1; }
for net in "${nets[@]}"; do
    if [[ -f "$net_dir/$net" ]]; then cp -- "$net_dir/$net" "$stage/src/$net"; fi
done
(
    exec > >(tee "$out/build.log") 2>&1
    cd "$stage/src"
    # The upstream fetcher validates the network name against the SHA-256 prefix.
    timeout 12m bash ../scripts/net.sh
    for net in "${nets[@]}"; do
        digest=$(sha256sum "$net")
        [[ $net == "nn-${digest:0:12}.nnue" ]] || { echo "Invalid network: $net" >&2; exit 1; }
    done
    timeout 20m env -i PATH="$PATH" LC_ALL=C make -j"$jobs" build ARCH="$arch" COMP=gcc \
        CXX=g++ GIT_SHA="${commit:0:8}" GIT_DATE="$date" GIT_DIFFINDEX=
)

cp "$stage/src/stockfish" "$out/stockfish"
cp -r "$stage/tests" "$out/upstream-tests"
python3 - "$out" "$stage/src" "$commit" "$arch" "$jobs" "${nets[@]}" <<'PY'
import hashlib, json, os, platform, shutil, subprocess, sys
from pathlib import Path

out, src = map(Path, sys.argv[1:3])
commit, arch, jobs = sys.argv[3:6]

def digest(path):
    result = hashlib.sha256()
    with open(path, 'rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()

cpu = Path('/proc/cpuinfo').read_text().split('\n\n')[0]
model = next((s.split(':', 1)[1].strip() for s in cpu.splitlines()
              if s.startswith(('model name', 'Hardware', 'CPU part'))), platform.machine())
manifest = {
    'commit': commit, 'binary_sha256': digest(out / 'stockfish'), 'arch': arch,
    'compiler': subprocess.check_output(['g++', '--version'], text=True).splitlines()[0],
    'compiler_binary_sha256': digest(shutil.which('g++')), 'jobs': int(jobs),
    'hardware': {'machine': platform.machine(), 'cpu': model, 'kernel': platform.release(),
                 'cpus_available': len(os.sched_getaffinity(0))},
    'build_flags': {'target': 'build', 'COMP': 'gcc', 'CXX': 'g++', 'ARCH': arch},
    'networks': {name: digest(src / name) for name in sys.argv[6:]},
}
(out / 'build.json').write_text(json.dumps(manifest, indent=2) + '\n')
PY
