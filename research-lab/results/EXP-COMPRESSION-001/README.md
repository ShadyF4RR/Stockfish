# EXP-COMPRESSION-001 — recovery and independent reproduction

**Original experiment unrecovered.** The reported 135 MB → 88 MB would save 34.81% using consistent units. The original input, output, platform, codec, flags and units remain unidentified. Reproductions below use independently identified artifacts.

## Recovery coverage, November 20, 2026

- The connected Stockfish fork had only the master ref and three newly created lab refs, all pointing to the same upstream commit. The fork had no pull refs, tags, releases or Actions runs at inspection. GitHub's direct artifact-list endpoint was unavailable through the connector. There were no runs from which to enumerate artifacts. The fork's pull-request search for compression returned no matches.
- Searched the complete 7,356-commit history reachable from the starting fork SHA for distinctive sizes, archive/codec terms and lossless engine/network changes. Upstream commit `a46087ee` added LEB128 compression to a different NNUE and reported 70 MiB → 39 MiB. Commit `69a01b88` concerns shared network pages. Neither identifies the reported files. [Historic LEB128 change](https://github.com/official-stockfish/Stockfish/commit/a46087ee)
- Inspected available project files, uploads, linked artifacts, matching Pages results and the accessible Stockfish-related Codex setup history. They supplied no original binary pair, script, benchmark or digest. This cloud task could not access earlier ChatGPT project history. Deleted, expired and inaccessible artifacts are outside the search. A supplied archive, transcript or hashes could reopen the recovery question.

## Verified independent trials

The source is the official Stockfish 19 Linux x86-64 universal release, tag `sf_19`, commit `edb0d9db6731067ec50ce619ff372b463bc4dd5d`. The published 81,388,977-byte tar.gz matched the release SHA-256. Extracted the executable and exported its default NNUE. Since the executable embeds this network, the two rows are separate packaging comparisons. Sizes are bytes, including codec framing. [Release](https://github.com/official-stockfish/Stockfish/releases/tag/sf_19)

| Input | Original | gzip level 1 | Reduction | xz preset 1 | Reduction |
| --- | ---: | ---: | ---: | ---: | ---: |
| Executable | 103,153,472 | 82,783,671 | 19.75% | 80,003,676 | 22.44% |
| Exported NNUE | 98,511,183 | 80,631,428 | 18.15% | 78,951,720 | 19.86% |

All four restores reproduced the full original SHA-256 and byte length. The gzip-restored engine and network also passed direct `cmp` checks. Packing and restoring introduced measurable one-time CPU, I/O and memory costs. See per-trial wall/CPU seconds, peak RSS, versions and compressed/restored digests. [Codec evidence](sf19-codecs.json) · [Pinned isolated proof and reproduction commands](https://github.com/ShadyF4RR/Stockfish/tree/48f05a733339aa09c5fcb9cb22bed74deaa0e2d3/research-lab/proofs/compression)

After unpacking, original and restored copies ran the same instructions and network. The cloud worker measured four fresh processes per side for startup and four per side for bench, alternating the execution order on one CPU. All eight benches returned 2,497,913 nodes. Explicitly loading the original and restored NNUE files yielded identical raw and scaled evaluation scores on three positions.

| Median | Original | Restored |
| --- | ---: | ---: |
| Startup to UCI readyok | 0.489 s | 0.484 s |
| Bench CPU | 3.103 s | 3.045 s |
| Bench peak RSS | 324,090 KiB | 325,030 KiB |
| Bench NPS | 992,299 | 990,546 |

**Decision: documented packaging result.** These observations provide no evidence of improved runtime or playing strength. Startup measurements used pre-read files. Cold-cache behavior was unmeasured and the VM exposed no CPU cache PMU. The archives decode before launch, leaving identical executable and network bytes. No Elo games or public Fishtest resources were spent on this comparison. An integrated loader or a recovered original artifact would warrant a separately identified comparison. [Runtime observations](sf19-runtime.json) · [Research protocol](../../experiments/compression-001.md)
