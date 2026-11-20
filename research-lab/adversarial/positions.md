# Adversarial opening corpus v1

[positions.json](positions.json) contains 24 hand-curated, unfinished opening lines starting from the standard chess position. Each entry records an opening family, the curator's reason for inclusion, provenance, PGN movetext, UCI moves and the resulting six-field FEN. The en passant field records a square only when a legal en passant capture exists. The lines illustrate opening ideas and carry no historical player, game or outcome attribution.

Each split has 12 roots drawn from 12 distinct families. Each contains four tactical roots, four quiet controls and four general positions. In each group White moves first in two positions and Black in two. The tactical label reflects the choice of opening line, not an observed score or measured complexity. The discovery Alien Gambit line ends after White's knight sacrifice on f7 has been accepted and White plays 7.Nf3. It is one of several tactical motifs.

| Split | SHA-256 of the ordered entries |
| --- | --- |
| Discovery | `c141e818758c5a9f51b98a331171b83dbdf332980e802c765c75b7916fea6507` |
| Holdout | `9a19221a1336fc131e37f4a866487da9ed57d64f8e7705f16109e8bc29972884` |

Use the original array order, select the entries for one split, and serialize the array using Python's `json.dumps(entries, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`. Hash the ASCII bytes with no trailing newline. The SHA-256 of the entire UTF-8 v1 file as distributed is `e8ddf0965834cca649488413fc9b8437669c715d8f1c8fa74a64578fba106238`. Changes to the corpus require a new version.

## Use in an experiment

Develop hypotheses and set search parameters on discovery only. Select any subset by a recorded rule before collecting results. Freeze the candidate and baseline SHAs, opponent builds and settings, resource limits, opening IDs or sampling seed, outcome measure, stopping rule and uncertainty method before evaluating holdout. The holdout is public and prevents reuse of discovery families. Its use relies on recording when outcomes were first inspected. Never pass holdout outcomes back into tuning and then claim an independent confirmation on the same holdout. A change prompted by those outcomes needs a new prospectively reserved set of families.

Give both color assignments the same starting position and settings. Begin free engine play at the recorded FEN. The opening prefix belongs in the test harness. For a root-position diagnostic, the side to move makes the first measured decision. For paired matches, exchange the engines' colors and retain both game records, including failures and terminations. Preserve completed and incomplete outcomes by opening ID. Treat color-reversed games as a pair for inference. Report the split, group and opponent with each result. The sample is small and deliberately selected. General playing-strength evidence requires an independently selected broad opening suite.

## Reproduce the positions

From the repository root, install the Python distribution `chess==1.11.2` in the test environment for legal SAN and UCI replay. If a built Stockfish is available, supply its absolute path in `STOCKFISH_TEST_ENGINE` to cross-check every final FEN through Stockfish's UCI console. Run:

```sh
python3 -m unittest discover -s research-lab/tests -p test_adversarial_positions.py -v
```

The tests verify the frozen split hashes, group and color counts, unique roots and disjoint families. Legal replay also verifies that no endpoint of either split occurs at an intermediate position of a line in the other split.
