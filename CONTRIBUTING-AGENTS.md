# Contributing rules for work-package agents

Eight work packages (WP1-WP8) build in parallel against the contracts in
`docs/contracts.md`. To keep merges painless:

- **Each WP edits only its own subpackage** under `src/muestra/<pkg>/`,
  its own test directory under `tests/<pkg>/`, and its own dependency
  group in `pyproject.toml` (one group per WP, named in a comment there).
- **Nobody commits `uv.lock` changes.** The integrator re-locks after
  merging; regenerate it locally as needed but leave it out of commits.
- **Contract changes go through `docs/decisions.md`.** If you must change
  anything described in `docs/contracts.md`, add a dated entry explaining
  what changed and why, update `contracts.md`, and flag it — never edit a
  contract silently. `docs/spec.md` is read-only.
- Tool registration is decentralised: your subpackage's `tools.py`
  exposes a `TOOLS` list; `muestra/registry.py` collects it. Add your
  tools there only.
- Fixtures under `fixtures/` are shared: extend, don't reshape.
