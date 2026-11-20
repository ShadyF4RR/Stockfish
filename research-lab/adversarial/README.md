# Adversarial opening and tactical research

This track investigates search and evaluation mechanisms that can help Stockfish find sound, surprising moves. It uses positions with material imbalances, sacrifices, forcing replies and quiet controls. The objective-strength question is whether the ordinary engine scores better across representative play. The practical question is whether a candidate wins more often against a named panel under the measured conditions.

## Study design

1. Record the official baseline SHA, one candidate patch, compiler, architecture, NNUE digests, bench signature and independent review. Preserve UCI compatibility. All engine changes stay on an isolated branch in the fork.
2. Select discovery positions before candidate analysis. Freeze the untouched holdout by opening family, opponent panel, clocks, hash, thread allocation, seed, color swaps, sample size and stopping rule. Keep unsuccessful or incomplete cells visible.
3. Use the opponent-difficulty diagnostic to describe score instability, critical replies, tactical complexity and losses in reference evaluation after actual opponent moves. Assess objective defensibility separately with deeper searches. Report truncated MultiPV, mate scores, missing observations and disagreement between references explicitly.
4. Compare candidate and baseline against each other, and against multiple independently implemented chess engines. Use equal conditions within each matched comparison. Report win/draw/loss, score, relative Elo where estimable, uncertainty, and holdout results by opponent and clock. The local clocks are pilot conditions. Official STC and LTC are separate campaigns that must follow the current Stockfish guidance.
5. Use general opening suites and independently validated official Fishtest results for a general Elo claim. Keep any optional opponent-specific or surprise-oriented style on a separate personal branch. No move book, opening name or player preference belongs in a proposed upstream patch.

Read the [frozen positions](positions.md), [analysis rules](analysis-plan.md), [public submission procedure](../fishtest/interfaces.md) and [fork promotion rules](../promotion.md). Read current official contribution and testing guidance again before preparing an upstream PR. [Stockfish contributions](https://github.com/official-stockfish/Stockfish/blob/master/CONTRIBUTING.md) · [Stockfish testing guidelines](https://github.com/official-stockfish/Stockfish/wiki/Developers)

## First experiment

The first candidate changes a single quiet-move ordering predicate. Its existing exchange-safety threshold still applies. A quiet discovered check illustrates which moves become eligible. The experiment has no opening-specific data or UCI option in the engine. [Engine change](https://github.com/ShadyF4RR/Stockfish/commit/c72e2482c77f8eabca37550f019ed55bfb8a7b79) · [Preregistered mechanism and benchmark](../results/EXP-TACTICAL-QUIETCHECK-001/plan.json)
