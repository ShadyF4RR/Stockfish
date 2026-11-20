# Repetition search screen

Candidate 293da879 changed only `src/search.cpp`. The AVX2 binary matched the official 1,488,683-node bench and passed the draw-rule, standard/Chess960 perft and search reproducibility checks.

Fastchess logged 43 finishes before the cloud environment restarted. The 41 complete PGNs scored 8 wins, 27 draws and 6 losses for the candidate (+16.96 descriptive Elo). Both engines selected the immediate repetition in two of eight eligible root decisions in these PGNs. The incomplete paired panel does not establish an Elo improvement or justify promotion. No public Fishtest job was submitted.

[Full report and per-clock results](../results/EXP-REPETITION-001/REPORT.md) · [Exact experimental commit](https://github.com/ShadyF4RR/Stockfish/commit/293da879bd27da174728cda7b5b46d8011f2a3ac)
