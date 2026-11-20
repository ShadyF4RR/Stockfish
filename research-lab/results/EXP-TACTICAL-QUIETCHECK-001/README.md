# EXP-TACTICAL-QUIETCHECK-001 · Order all qualifying quiet checks

In a discovered check, the checking piece can stay on its original square while a blocker moves away. Stockfish's quiet-move scoring used a destination-square test for checks by the moving piece. The candidate uses the existing `Position::gives_check` predicate and applies the same +16,384 bonus and −75 static-exchange threshold as before. This change applies to every legal position and introduces no opening recognition or UCI option. [Candidate](https://github.com/ShadyF4RR/Stockfish/commit/c72e2482c77f8eabca37550f019ed55bfb8a7b79) · [Protocol](plan.json) · [Independent review](review.json)

After the diagnostic move `e4g5`, both engines identify the rook on e1 as checking the king on e8. The knight delivers no direct check from g5. Both count 69 legal two-ply continuations after the move. This confirms a case where the broader predicate can apply. [Position and measurements](mechanism.json)

## Correctness and cost

The pinned baseline is `1819b9ea37588be8adb044179fb54512f87c8249`. Both engines use GCC 14.2.0, x86-64 AVX2, and the same network. UCI, all 20 standard/Chess960 perft cases and reproducibility across 20 node budgets passed. The independent reviewer found no correctness defects and flagged the possible cost of checking more quiet moves. [Validation log](validation.log)

| Engine | Default depth-13 bench nodes, both runs | First NPS | Second NPS |
|---|---:|---:|---:|
| Baseline | 1,488,683 | 269,396 | 270,620 |
| Candidate | 1,058,541 | 239,597 | 235,283 |

Bench used 16 MiB hash, fresh single-thread processes on logical CPU 0, in baseline/candidate/candidate/baseline order. In these two observations the candidate searched 28.89% fewer nodes and averaged 12.06% fewer nodes per second. The worker was shared and the visited nodes changed. The timing flags a possible efficiency cost that merits investigation. It supplies no Elo estimate. [Builds and per-run measurements](bench.json)

The statistical testing agent owns the separately registered opening and opponent panels. Their holdout rules govern tactical and practical-strength claims. [Panel analysis plan](../../adversarial/analysis-plan.md)
