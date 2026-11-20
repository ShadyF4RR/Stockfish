# Local HTML position analysis

Use Python 3.8+ and the MSZIP launcher built for the host operating system. Start the server in this directory with the executable you would select in a chess GUI.

```sh
python3 serve.py --engine /absolute/path/to/stockfish19-mszip --port 8765
```

On Windows run `py -3 serve.py --engine "C:\path\to\stockfish19-mszip.exe"`.
Open the printed `http://127.0.0.1:8765/` URL in a browser on the same machine. Use `--port 0` to select an available port. Enter `startpos` or a standard six-field FEN, then click **Find best move**. The board displays the analyzed position and highlights the source and destination of the returned UCI move. Stop the server with Ctrl+C.

The HTML client requests a local native search. The Python server accepts one position per request on loopback, validates its FEN syntax and runs only the executable specified at startup. Each query sends UCI initialization, one search bounded to 20,000 nodes with one thread and a 16 MiB hash, then quits the engine. Its 45-second wall limit includes launcher startup. Only one search runs at a time. Scripts and styling are served locally. The launcher and its cached native engine must run on the server's computer.

On 2026-11-20, a Chromium smoke test drove the Linux x86-64 MSZIP launcher through this page. From `startpos`, Stockfish 19 returned `e2e4` at depth 13 after 20,002 nodes. At a 375-pixel viewport the client recognized stalemate for `7k/5Q2/7K/8/8/8/8/8 b - - 0 1`. The server rejected a request from another origin, an incorrect Host header, an injected second UCI line and a JSON field attempting to choose another engine. These results cover the Linux launcher and loopback browser path.
