# Stockfish 19 packaging experiment

This isolated proof measures lossless, single-threaded gzip compression of two
Stockfish 19 release artifacts. Each restored file has the same byte count and
SHA-256 digest as its input. The network is also embedded in the executable, so
the two reductions describe separate packaging choices and cannot be added.

| Input | Input bytes | gzip level 1 bytes | Reduction | Pack wall (s) | Restore wall (s) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Linux universal x86-64 executable | 103,153,472 | 82,783,671 | 19.75% | 6.898 | 1.939 |
| Standalone default NNUE | 98,511,183 | 80,631,428 | 18.15% | 3.647 | 0.854 |

The published release tarball already occupies 81,388,977 bytes. Its SHA-256
is `9defc0d4e55d49c65a6d042f3e571a39fcea499ade6dbe741b53b8c65e03611f`.
The digest of its `stockfish/stockfish-linux-x86-64-universal` member matches
the executable input digest in [sf19-executable.json](sf19-executable.json).
The official tag `sf_19` resolves to
`edb0d9db6731067ec50ce619ff372b463bc4dd5d`. Its `src/evaluate.h` selects
`nn-1a298aa575a0.nnue`, whose SHA-256 is in [sf19-network.json](sf19-network.json).
Sources: [release asset](https://github.com/official-stockfish/Stockfish/releases/download/sf_19/stockfish-linux-x86-64-universal.tar.gz)
and [tagged network selection](https://github.com/official-stockfish/Stockfish/blob/edb0d9db6731067ec50ce619ff372b463bc4dd5d/src/evaluate.h).

## Protocol

Use Linux, Python 3.8+ with zlib, and enough temporary disk space for the gzip
member and the restored file. These runs used Python 3.12.14 and zlib 1.3.2.
Obtain the exact inputs above, then run once per artifact with a 90-second limit.

```sh
timeout 90s python3 roundtrip.py /path/to/stockfish-linux-x86-64-universal
timeout 90s python3 roundtrip.py /path/to/nn-1a298aa575a0.nnue
```

Pass `--keep-dir /path/to/new-dir` to retain `packed.gz` and `restored.bin`
in a newly created directory. The tool reads one regular file in 1 MiB
chunks and leaves executable permission unset on restored data. It identifies
familiar executable, NNUE and container signatures and labels other inputs
unknown. Classification does not establish provenance. It bounds the input to
256 MiB by default, enforces the input size while decompressing, and never
executes or recursively extracts its input. Exit 0 indicates verified byte
counts and SHA-256, exit 1 indicates a failed measurement and exit 2 indicates
a usage error. A failure with `--keep-dir` can leave partial files.

The output reports gzip length including framing. An empty gzip filename and
a zero header timestamp remove two common sources of run-to-run variation.
Pack and restore timings include streaming hashes and buffered file I/O.
An independent reread hashes the restored file outside the measured restore
interval. Process CPU seconds and full hashes are retained in each JSON.
Timings are single-run observations on shared hardware. Launching the
restored executable requires setting executable permission after verifying its
origin. The storage measurements alone provide no evidence of an engine speed
or playing-strength change.
