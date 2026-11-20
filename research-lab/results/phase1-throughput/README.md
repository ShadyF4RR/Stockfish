# Phase 1 worker calibration

The Codex Cloud worker played the pinned baseline against itself on November 20, 2026. Its CPU quota permitted four cores. After reserving two cores and 4 GiB of RAM, Fastchess ran on two cores. Each allocation played the same two openings in both colors at 4+0.04 with a 16 MiB hash per engine.

| Concurrent games | Threads per engine | Completed games | Wall seconds | Games/hour |
| ---: | ---: | ---: | ---: | ---: |
| 2 | 1 | 4 | 36.54 | 394.0 |
| 1 | 2 | 4 | 66.85 | 215.4 |

These rates describe a single short sample on this cloud worker. Recalibrate on a newly connected worker to measure its throughput. Both match records classify the playing-strength evidence as inconclusive.

The [throughput summary](throughput.json) links both match manifests. Each directory also contains the PGN and Fastchess output. To replay, use Fastchess commit `894616028492ae6114835195f14a899f6fa237d3`, its `app/tests/data/openings.epd` and the baseline build identified in the manifests.
