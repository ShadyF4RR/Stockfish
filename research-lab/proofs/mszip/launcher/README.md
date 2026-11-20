# Native UCI launcher

Select the packaged executable as a UCI engine in the desktop chess GUI. It accepts the same arguments, standard input, output and error streams as the native Stockfish 19 child. A chess GUI can send normal UCI commands and read replies directly. On Linux the launcher replaces itself with the engine, preserving the PID. On Windows 10 and later, the launcher creates the engine in a kill-on-close Windows job with its inherited standard handles. The launcher's exit code follows the native process.

On first use, the launcher restores the engine in its private per-user cache. It validates the CAB v1.3 header, folder, member size and block sizes, checks each CFDATA checksum, expands each CK-prefixed raw DEFLATE stream with the preceding 32 KiB window and verifies the full engine SHA-256 before publication. The member name supplies no filesystem path. Output is limited to 512 MiB. The bundled engine and network remain byte-identical to the official input executable.

Each platform uses its user cache directory with a `stockfish-mszip` child. On Linux this is normally `$XDG_CACHE_HOME/stockfish-mszip` or `~/.cache/stockfish-mszip`. Windows uses `%LocalAppData%\stockfish-mszip`. Set **STOCKFISH_MSZIP_CACHE** to an absolute path to use a different private, writable directory on a filesystem that permits execution. Choose a new path on Windows so the launcher can create it with its protected owner/SYSTEM ACL. On Linux an existing directory must belong to the current user with no group or other permissions.

Each launch verifies the cached engine's size and SHA-256. A missing or damaged file triggers a fresh restore. A per-digest OS lock serializes installers within the same cache. Only a synchronized, fully verified temporary file is renamed into place. An interrupted install leaves at most a private temporary file which the next launcher removes. Initialization errors appear on standard error. To clear the cache, close the engines and remove their cache directory. A subsequent launch restores it automatically.

Use the parent directory's build script to produce the Linux amd64 or Windows amd64 package. It embeds the actual CAB and a separately pinned original SHA-256 in a temporary Go module. To exercise the CAB and cache tests directly in an isolated directory with Go 1.22+:

```bash
stage=$(mktemp -d)
cp *.go go.mod "$stage/"
(
  cd "$stage"
  touch payload.cab payload.sha256
  GOTOOLCHAIN=local GOPROXY=off go test -v ./...
)
```

The empty embed files only satisfy the compiler for those unit tests. The tests generate their own small multi-block CAB, exercise retained-dictionary decoding, reject damaged input and check concurrent and interrupted installations. Release verification uses the real cabinet, pinned official executable and protocol tests.
