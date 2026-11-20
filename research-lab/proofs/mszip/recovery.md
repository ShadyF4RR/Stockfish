# Historical recovery of the reported MSZIP configuration

The owner recalls a Stockfish 19 artifact shrinking from about 135 MB to 88 MB with MSZIP. They recall using it directly in a chess GUI and a custom HTML interface. The original files, command, GUI path, HTML and launch or extraction logs were unavailable for inspection. The two size readings and the point at which the original system decoded the data remain unverified.

## What the history reveals

On 2026-11-20 the fork's public `master` and upstream `HEAD` both resolved to `1819b9ea37588be8adb044179fb54512f87c8249`. I checked the five advertised fork branches, the complete 7,356-commit ancestry of `master`, the `sf_19` release tree, and the provided workspace attachments. The other advertised heads were the new lab branches. Across the `master` history, diff searches for `MSZIP`, `CreateCompressor`, `CreateDecompressor`, `COMPRESS_ALGORITHM_MSZIP`, `cabinet.dll`, `makecab` and `compact.exe` returned zero hits. Historical pathname searches returned zero tracked CAB, HTML/HTM/HTA, JS, WASM, BAT or CMD files. Searches covered reachable public history and supplied files. A private or never-committed launcher would fall outside that scope. [Fork](https://github.com/ShadyF4RR/Stockfish) · [Official baseline](https://github.com/official-stockfish/Stockfish/tree/1819b9ea37588be8adb044179fb54512f87c8249)

Three upstream features help identify the boundary of a reconstruction.

- Stockfish 19 selects its embedded default network or opens an external `EvalFile` as a binary stream. That stream starts with the native NNUE header. Some parameter arrays use signed LEB128 encoding internally. A CAB file or MSZIP frame requires a separate decoder before this loader can consume its contents. [Stockfish 19 loader](https://github.com/official-stockfish/Stockfish/blob/edb0d9db6731067ec50ce619ff372b463bc4dd5d/src/nnue/network.cpp#L231-L284) · [Parameter decoder](https://github.com/official-stockfish/Stockfish/blob/edb0d9db6731067ec50ce619ff372b463bc4dd5d/src/nnue/nnue_feature_transformer.h#L171-L187)
- The release supplies a UCI engine for an external chess GUI. Its upstream WASM workflow builds Emscripten targets, preloads the selected network and runs the generated `stockfish.js` under Node. It supplies no historical record of the owner's custom HTML. A browser worker loading WASM and a page communicating with a native UCI service are two independently testable options. [Release README](https://github.com/official-stockfish/Stockfish/blob/edb0d9db6731067ec50ce619ff372b463bc4dd5d/README.md) · [WASM workflow](https://github.com/official-stockfish/Stockfish/blob/edb0d9db6731067ec50ce619ff372b463bc4dd5d/.github/workflows/wasm_compilation.yml)
- Microsoft documents MSZIP through its Compression API in buffer and raw block modes. CAB is a container that can identify MSZIP as a folder's compression method. Those formats require the corresponding framing. Windows `compact /EXE` offers XPRESS4K, XPRESS8K, XPRESS16K and LZX. Record logical bytes and allocated disk bytes separately if investigating transparent filesystem compression. [Microsoft Compression API](https://learn.microsoft.com/en-us/windows/win32/cmpapi/using-the-compression-api) · [Windows compact command](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/compact)

## Identifiable Windows reference

The `sf_19` tag resolves to `edb0d9db6731067ec50ce619ff372b463bc4dd5d`. Downloaded the official x86-64 universal Windows ZIP, verified its published SHA-256, then extracted and hashed its executable. The ZIP contains the member `stockfish/stockfish-windows-x86-64-universal.exe` using ZIP method 8 (Deflate). It occupies 80,978,939 compressed bytes within that ZIP. [Release and checksums](https://github.com/official-stockfish/Stockfish/releases/expanded_assets/sf_19) · [Exact release asset](https://github.com/official-stockfish/Stockfish/releases/download/sf_19/stockfish-windows-x86-64-universal.zip)

| Component | Logical bytes | SHA-256 |
|---|---:|---|
| Official ZIP | 81,431,614 | `3c8bf1f9ea66a09350a40df4f632288285ac206d99f33ab5842c408fc30b48a7` |
| Windows console x86-64 PE | 103,046,300 | `45bc8e4969147db9c2eb533810637994619bff0eacc81ccfd9854394901bcbd0` |
| Embedded `nn-1a298aa575a0.nnue` | 98,511,183 | `1a298aa575a085434d29027978dc36867fe9c5bcea9376654b7a8eba1e52dfc2` |

The complete serialized network occurs at PE file offset 3,189,792 (`0x30ac20`). The SHA-256 of that byte range matched the independently exported SF19 default network. The PE import table lists KERNEL32, msvcrt and SHELL32. Its source and static imports give no evidence of a native MSZIP decoding path. The ZIP, PE and NNUE are three distinct candidate inputs for comparison. None has the recalled 135 MB logical size.

## Evidence that would identify the original route

| Recoverable detail | What it distinguishes |
|---|---|
| Filename, first bytes, full file hash, exact byte count, codec command/options | PE, CAB, Windows Compression API data, raw NNUE, WASM module, or a packaged bundle |
| Engine command configured in the GUI, working directory, UCI startup output and reported `EvalFile` | Direct PE, a UCI-compatible launcher, or a previously extracted engine and network |
| Original HTML/JS or a browser network trace identifying .wasm, worker files, localhost requests or WebSocket connections | In-browser engine, JS decoder, or service forwarding UCI to a native engine |
| Cold launch trace of child processes, file opens and temporary writes, compared to a warm relaunch | Installation-time extraction, per-launch extraction, cached output, filesystem transparency, or an in-process decoder |

A successful reconstruction can establish GUI or HTML compatibility for its own recorded path. Establishing the historical path would still require evidence from the original environment.
