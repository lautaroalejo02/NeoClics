# WP15 final-pass evidence (in progress)

Branch: `wp15-final`, based on inherited merge `566f630`. No push.
All real experiments use disposable copies; original projects/library untouched.

Completed implementation: partial grid reliability and mix-first workflow,
time-window grid audition, default reliable-region candidate filtering with
explicit override, nonblocking choice/validation warnings; non-versioned drafts,
preview listing/events, opt-in ears; LAN/UI token reconciliation; lazy render
imports; MCP shared-convention compaction; encoding/help/metrics/library/diff
friction fixes. Final validation and inherited-commit audit are still in progress.

Initial uncached Livery analysis: **26.457 s**, **83.333 BPM**, **15/49** reliable
complete bars (**30.612%**), six reliable regions: **40.34–57.90**, **63.80–66.74**,
**72.64–75.60**, **105.10–107.96**, **114.20–117.08**, **123.50–138.06 s**.
Consistency is a heuristic, not musical beat accuracy. No listening claim.

Baseline before restoring lazy render imports/compacting descriptions (after
the first reliability changes): CLI help **1.997 s**, tools **1.863 s**, song
validate on demo **2.292 s**, MCP initialize **2.789 s**; tools/list **105,969
UTF-8 bytes**, **30** tools. Final same-machine probe pending.

First broad validation (before all final changes): **811 passed, 9 skipped,
1 stale backslash-rejection test failed in 496.34 s**. Updated that test to
check accepted normalization plus continued traversal rejection. First UI run:
typecheck/lint passed, **173 unit tests passed (48.19 s)**; real e2e **9 passed,
20 skipped, 1 test setup failure** (candidate too near source end). LAN and
CLI draft playback passed. Final all-suite results will replace these milestones.

Audit driver: `uv run python -m tests.integration.wp15_audit --root <wp15-copies>`.
It measures real rendering, kills a real child between tagging/publication,
checks manual choice, time filters, seams, loudness, concurrent feedback,
pruning, alias levels, formants, and Livery PCM/grid placement. No audio goes
into git. Final evidence/report pending.
