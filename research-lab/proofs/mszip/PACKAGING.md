# Building the Stockfish 19 MSZIP launchers

Both launchers carry one CAB v1.3 containing the unchanged, NNUE-embedded
universal release engine for their platform. A native chess GUI can select the
launcher as its UCI engine. The first launch restores the engine into the
user's private cache. Later launches verify the cached digest and reuse the
engine. The on-disk cache therefore adds one full-size engine to the installed
footprint. The package size and total installed size measure different things.

| Platform | Original engine bytes | CAB bytes | Complete launcher bytes |
| --- | ---: | ---: | ---: |
| Linux x86-64 | 103,153,472 | 86,252,676 | 88,174,740 |
| Windows x86-64 | 103,046,300 | 86,206,725 | 88,265,216 |

These counts came from local Go 1.24.6 / zlib 1.3.2 builds using the sources
in this proof. Two clean builds per platform yielded identical launcher bytes.
The original release tag is `sf_19` at
`edb0d9db6731067ec50ce619ff372b463bc4dd5d`. The independently recovered
input digests, archive digests and launcher digests are recorded in
`manifest.json` for each build. See [recovery.md](recovery.md) for the
distinction between these measured inputs and the historical size observation.

## Build

Use a Linux build host, Bash, GNU coreutils, Python 3.8+ with zlib, the
libarchive shared library with CAB support, and Go 1.24.6 for the reference
builds. Verify the release-asset SHA-256 against the official publication
before extracting the universal engine. The build command pins the SHA-256 of
the extracted engine for each OS. [Stockfish 19 release](https://github.com/official-stockfish/Stockfish/releases/tag/sf_19)

```sh
# Run in this directory. Choose new output directories.
GO=/path/to/go/bin/go ./build.sh linux /path/to/stockfish-linux-x86-64-universal /tmp/mszip-linux
GO=/path/to/go/bin/go ./build.sh windows /path/to/stockfish-windows-x86-64-universal.exe /tmp/mszip-windows
```

The Linux output is `stockfish19-mszip` and the Windows output is
`stockfish19-mszip.exe`. Each output directory also holds `payload.cab` and
`manifest.json`. The launcher embeds the cabinet and original digest. It can
run with the CAB and build manifest elsewhere. Rebuilds use fresh staging
directories, disallow external Go module downloads and strip build paths.
Exit 0 means the original digest, independent CAB extraction and launcher
build all passed. Exit 2 indicates incorrect usage. Other failures return
nonzero.

The packer stores one named member in a single folder with compression type 1.
Each CFDATA record has at most 32,768 restored bytes, a Cabinet XOR checksum,
a `CK` signature and one raw DEFLATE stream. The next stream uses the previous
restored block as its initial history. The default encoder uses zlib level 1
and memLevel 4. This preset trades compression ratio for encoder memory and
speed and was selected during the attempt to reproduce the approximate size.
Set `MSZIP_LEVEL` and `MSZIP_MEM_LEVEL` to values from 1 through 9 to compare
valid presets. The CAB length comes entirely from the compressed member and
standard container framing. A separate system libarchive decoder reads the
complete CAB and checks the restored byte count and SHA-256 before embedding.

## Distribution

The engine's GPLv3 license, AUTHORS and upstream notices travel with the
distribution. Provide corresponding source for the exact engine, network,
launcher and packaging scripts, along with the practical instructions and
toolchain versions needed to rebuild. The Go runtime's BSD notice and
disclaimer belong with the binary and source distribution. This repository's
Actions workflow assembles a source companion for both packages. Keep it
available for recipients together with the binaries and their notices.
[Stockfish 19 license](https://github.com/official-stockfish/Stockfish/blob/edb0d9db6731067ec50ce619ff372b463bc4dd5d/Copying.txt) · [Go license](https://go.dev/LICENSE)
