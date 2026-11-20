# Experimental opponent-difficulty metric

This diagnostic asks how difficult a fixed position was for a specified panel of UCI engines to play at a specified depth. The player being tested is the side to move. To study a proposed move, enter the position *after* that move, with the full legal history from the initial position. The corpus stores both the moves and FEN so castling, en passant and repetition history can be checked. Terminal positions and positions with only one legal move are excluded from scoring.

## Four components

| Component | Measurement |
|---|---|
| Evaluation instability | Range of the diagnostic engine's rank-one expected score over the specified increasing depths, at the same MultiPV setting. The raw lines also show how often the preferred move changed. |
| Critical-move scarcity | At the deepest diagnostic depth, a reply is viable if its expected score falls within the tolerance of rank one. With `L` legal moves, `K` moves analyzed and `B` observed outside tolerance, the fraction outside tolerance lies between `B/L` and `(B+L−K)/L`, given the observed scores. The bounds coincide if all moves were analyzed. |
| Tactical complexity proxy | Fraction of the first six available plies of the deepest rank-one PV that capture, check or promote. A move satisfying several criteria counts once. Captures, checks and promotions can be individually inspected. Short PVs have smaller denominators. |
| Observed opponent error rate | Each panel engine chooses a legal move in an independent single-PV search. A separate reference engine chooses its best move, then searches that move and the opponent's move separately from the same root at identical depth using UCI `searchmoves`. Error means that the reference's estimated loss in expected score strictly exceeds the tolerance. Each distinct panel engine has one vote per position. |

All evaluations refer to the root side to move. A win is worth one point and a draw half a point. Native UCI WDL supplies expected points as `(wins + draws/2)/1000`. For exact CP scores from engines without WDL, python-chess's pinned SF16.1 conversion uses the root ply. This fallback has no calibration guarantee for a different engine family. A forced mate maps to its outcome with no fabricated centipawn value. The program rejects incomplete depths, score bounds, incompatible CP conversions within a comparison, and references that ignore the requested root move. Winning mate lengths and already saturated WDL may have equal expected scores despite different practical conversion difficulty.

A negative signed loss records an opponent choice that scored better on the forced re-search. Reported regret clips the signed loss at zero. Transposition tables are given a new-game boundary for each search. A single-thread fixed-depth search improves replayability, subject to each engine's handling of UCI options.

The experimental composite averages instability, scarcity, forcing share and observed panel error rate with equal weights. The two scarcity endpoints yield the two reported composite endpoints. This range captures unexamined legal moves. Statistical uncertainty, finite-depth error, evaluator calibration, opponent correlation and the short PV horizon can each change the estimate. The individual components and denominators are retained for analysis.

## Run

Use Python 3.11+ and install the pinned chess library. Supply locally trusted UCI executables. Assign accurate engine family names. The program enforces distinct reference and opponent labels and executable hashes. Engine genealogy and independence within the panel require source-level verification by the operator. The diagnostic engine may belong to the reference family. Its role is to characterize the root position.

```sh
python3 -m pip install 'chess==1.11.2'
python3 research-lab/adversarial/difficulty.py \
  --engine "$SF_BASELINE" --reference "$STOCKFISH_19" --reference-family stockfish \
  --opponent "fruit=$FRUIT" --opponent "ethereal=$ETHEREAL" --opponent "weiss=$WEISS" \
  --depths 4 6 8 --multipv 4 --opponent-depth 5 --reference-depth 11 \
  --threshold 0.05 --split discovery --limit 12 --output difficulty-discovery.json
```

The output path must be new. All judgments and searched PVs include move identities. The report records the corpus digest, executable digests, UCI engine identities, configured settings, chess library version, search depths and tolerance. For external NNUE, books or tablebases, retain the corresponding file hashes and configuration alongside the output. When an engine exposes the switches, the runner sets a 16 MiB hash, one thread, native WDL on, local opening books off and online Syzygy off. A fresh engine process uses the remaining defaults.

The default split is discovery. Freeze the candidate, settings, reference, opponents and analysis before explicitly selecting a holdout split. Compare practical wins using matched candidate and baseline games against each opponent and assess objective strength separately with candidate-versus-baseline games. Those outcomes and their uncertainty follow the [match analysis plan](analysis-plan.md). The composite has no Elo interpretation, and selective position sampling limits extrapolation to other engines and players.

## Recorded discovery measurement

The [discovery report](../results/EXP-ADVERSARIAL-001/difficulty-discovery.json) contains 36 observed engine choices across 12 positions. The dev Stockfish diagnostic used depths 4, 6 and 8 with up to four root choices. Stockfish 19 adjudicated at depth 11. Fruit 2.1, Ethereal 12.00 and Weiss 2.0 chose at depth 5. Respectively, 2, 3 and 4 choices out of 12 exceeded a loss of 0.05 expected points. Mean composite endpoints were 0.1665 and 0.3721. All 12 position records reproduced in a second full run pinned to one CPU. These observations describe three shallow-search opponents on the fixed discovery suite.
