#!/usr/bin/env bash
# Usage: benchmark.sh BUILD_DIR [EXPECTED_NODES]
# Linux, Bash, Python >=3.9. Runs the upstream default single-thread bench twice.
# Exit 2 for usage, nonzero for missing/mismatched evidence, timeout, or engine failure.
set -euo pipefail
[[ $# == 1 || $# == 2 && $2 =~ ^[1-9][0-9]*$ ]] || { echo "Usage: $0 BUILD_DIR [EXPECTED_NODES]" >&2; exit 2; }
python3 - "$@" <<'PY'
import hashlib, json, re, resource, subprocess, sys, time
from pathlib import Path

out = Path(sys.argv[1]).resolve()
meta = json.loads((out / 'build.json').read_text())
binary = out / 'stockfish'
with binary.open('rb') as stream:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(block)
if digest.hexdigest() != meta['binary_sha256']:
    sys.exit('Engine differs from build manifest')
if (out / 'benchmark.json').exists():
    sys.exit('Benchmark already recorded')

runs = []
for index in range(2):
    start = time.monotonic()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    log = out / f'bench-{index + 1}.log'
    with log.open('wb') as dest:
        subprocess.run([binary, 'bench'], cwd=out, stdout=dest, stderr=subprocess.STDOUT,
                       check=True, timeout=180)
    elapsed = time.monotonic() - start
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    output = log.read_text()
    def measure(label):
        match = re.findall(r'^' + re.escape(label) + r'\s*:\s*([1-9][0-9]*)$', output, re.M)
        if len(match) != 1:
            sys.exit(f'Missing or ambiguous {label} in {log}')
        return int(match[0])

    runs.append({'nodes': measure('Nodes searched'), 'nps': measure('Nodes/second'),
                 'engine_ms': measure('Total time (ms)'), 'wall_seconds': elapsed,
                 'cpu_seconds': after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime})
if runs[0]['nodes'] != runs[1]['nodes']:
    sys.exit('Canonical bench signature differs between runs')
if len(sys.argv) == 3 and runs[0]['nodes'] != int(sys.argv[2]):
    sys.exit(f"Reference bench mismatch: got {runs[0]['nodes']}, expected {sys.argv[2]}")
report = {'commit': meta['commit'], 'binary_sha256': meta['binary_sha256'],
          'command': ['stockfish', 'bench'], 'signature': runs[0]['nodes'], 'runs': runs,
          'max_rss_kib': after.ru_maxrss,
          'expected_nodes': int(sys.argv[2]) if len(sys.argv) == 3 else None}
(out / 'benchmark.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
PY
