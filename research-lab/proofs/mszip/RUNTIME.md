# Linux runtime and UCI compatibility

The measured 88,174,740-byte MSZIP launcher restored and ran the official 103,153,472-byte Stockfish 19 Linux universal executable. First use added about 2.27 seconds to median UCI readiness. A primed cache reduced that difference to 0.16 seconds. The experiment found no evidence of a smaller engine working set or faster search. [Build and archive verification](PACKAGING.md) · [Raw trials, hashes and statistics](evidence/linux-runtime.json)

## Ten interleaved blocks on one cloud worker

Each block launched native Stockfish, the package with a new private cache directory, and the package with an already verified cached executable. Mode order rotated and reversed across blocks. All 30 processes used the same logical CPU, one search thread and 16 MiB of hash. The process monitoring harness shared the host with other cloud activity. The operating system's filesystem page cache and Stockfish shared NNUE memory were uncontrolled. First use here means an empty launcher cache.

| Median over 10 runs | Native | MSZIP first use | MSZIP cached |
| --- | ---: | ---: | ---: |
| Process start to UCI handshake, s | 0.574 | 2.842 | 0.737 |
| Process start to readyok, s | 0.595 | 2.864 | 0.755 |
| Process start to first 100k-node best move, s | 0.807 | 3.005 | 0.915 |
| End-to-end trial wall time, s | 5.021 | 6.888 | 5.235 |
| Engine and launcher user CPU, s | 4.659 | 6.318 | 4.755 |
| Engine and launcher system CPU, s | 0.252 | 0.489 | 0.367 |
| Kernel maximum process RSS, KiB | 324,116 | 324,316 | 324,352 |
| Sampled simultaneous process-group peak RSS, KiB | 323,908 | 324,276 | 323,862 |
| Engine PSS after readiness, KiB | 262,193 | 262,450 | 262,439 |
| Engine PSS after first search, KiB | 262,463 | 262,746 | 262,712 |
| Steady three-position suite, nodes/s | 643,368 | 719,770 | 637,909 |
| Default bench, nodes/s | 848,871 | 912,994 | 857,214 |

Linux replaces the running decoder process image with the restored engine using `exec`. Sampling every 10 ms found median peak RSS of 91,630 KiB for the decoder image on first use and 2,408 KiB during cached verification. The process-group observer included any children and time-matched their RSS. Shared mappings can count once per process in RSS. The kernel high-water mark captures single-process peaks between samples. The raw records include PSS, shared/private memory, page faults and block I/O. First use wrote a median 201,496 512-byte blocks. Cached launches wrote a median 8 blocks. All modes recorded zero median disk input blocks, consistent with warm filesystem pages. A separate seven-trial Go benchmark ran the same production MSZIP decoder over the actual embedded Linux CAB, writing to a discard sink. The median decoder time was 1.637 seconds, or 63.00 MB/s of restored bytes. That measures decoding with block checksums, excluding hashing of the restored file, disk installation and engine startup. [Decoder benchmark](evidence/linux-decode.txt)

Every trial resolved the running executable through procfs and checked its full SHA-256 against the official release. All 30 reported the expected default bench of **2,497,913 nodes**. Across three positions the default embedded NNUE returned identical evaluation text. Searches with a fresh hash returned the same node counts, scores and best moves for every mode. The three steady searches used a 250,000-node budget each. UCI recorded 250,048, 250,188 and 250,180 nodes, including Stockfish's small deterministic overshoot. [Raw searches and evaluations](evidence/linux-runtime.json)

For each block, divide total nodes by total search time for both engines, then divide the packaged rate by native. The median paired ratio was **0.968** for cached launches, with an interval of **0.825–1.189**. First use gave **1.101**, with an interval of **0.959–1.307**. The intervals use the second and ninth ordered ratios across ten blocks, nominally 97.85% coverage under independent blocks. Shared-host load limits that assumption. Linux denied access to hardware cycles, cache references and cache misses with `EPERM` on `perf_event_open`. Hardware CPU cache effects remain unmeasured.

## Chess clients

**XBoard 4.9.1** ran the launcher and native binary as its first and second UCI engines through PolyGlot 2.0.4 on a private Xvfb display. With colors reversed in the second game, each engine played the legal mate `Qg7#` from the same position. XBoard saved both finished games and displayed the final match score. The trace records both executable paths. [GUI PGN](evidence/linux-xboard.pgn) · [GUI trace](evidence/linux-xboard.log)

**Fastchess** ran one complete color-reversed opening pair, at 4+0.04, with one thread, 16 MiB hash and pondering disabled. Both games ended in checkmate. Each player won one. The test used Fastchess commit `894616028492ae6114835195f14a899f6fa237d3`, its `app/tests/data/openings.epd` book (SHA-256 `421a4efcddb95c3e70b1f9ad6abbeab1edcb6ab8e9ae743d94c42a5d78a6e82a`) and seed 20261120. These two games exercise realistic manager communication. They provide insufficient evidence for an Elo conclusion. [Manager PGN](evidence/linux-fastchess.pgn) · [Match output](evidence/linux-fastchess.log)

## Reproduction

Run the following from this directory on Linux with a trusted official executable, the built package and a new report path. The standard-library Python harness requires Linux procfs and Python 3.9+. It uses ten blocks by default, one pinned CPU, and a 60-second deadline per measured trial. A first run and cached runs each reconstruct or verify the designated release hash. The script exits with an error on mismatches.

```sh
python3 measure.py "$NATIVE" "$PACKED" runtime.json
xvfb-run -a ./gui-smoke.sh "$NATIVE" "$PACKED" new-gui-output
```

For a decoder-only run, stage the launcher source, the verified CAB and its expected member hash into an empty directory. Use the same Go toolchain as the package build. The sample pins to CPU 0 for the separate decoder benchmark. Pick a CPU allowed by your worker.

```sh
mkdir decode-bench
cp launcher/*.go launcher/go.mod "$BUILD_DIR/payload.cab" decode-bench/
sha256sum "$NATIVE" | cut -d' ' -f1 > decode-bench/payload.sha256
(cd decode-bench && GOMAXPROCS=1 taskset -c 0 go test -run '^$' -bench '^BenchmarkMSZIPDecode$' -benchtime=1x -count=7 -cpu=1)
```

For the manager replay, create a fresh output directory. Set `BOOK` to an absolute opening-book path and run the pinned Fastchess from that directory.

```sh
fastchess -engine "cmd=$PACKED" name=mszip -engine "cmd=$NATIVE" name=native \
  -each proto=uci tc=4+0.04 option.Threads=1 option.Hash=16 option.Ponder=false \
  -openings "file=$BOOK" format=epd order=random -srand 20261120 \
  -games 2 -rounds 1 -repeat -concurrency 1 -report penta=true -pgnout file=games.pgn min=false
```
