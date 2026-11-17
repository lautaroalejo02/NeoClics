# Fixtures

- `example-project/` — a complete, valid muestra project (spec 7.1):
  `muestra.toml`, `song.yaml`, an `analysis.json`, a chop-candidate set
  (`cache/chops/src1/cs001.json`), a `renders/v001/` with `meta.json` +
  `metrics.json`, `feedback.jsonl`, a minimal `midi/bass.mid`, and short
  synthesized audio.
- `library/` — the sample library `example-project/muestra.toml` points at
  (`library = ["../library"]`): a tiny `kits/disco-01/` drum kit used by the
  song's `library://` lanes.
- `invalid/` — broken `song.yaml` files, one error class each. The test in
  `tests/schemas/test_invalid_fixtures.py` maps each file to the expected
  issue path + code.

## Regenerating the audio

All audio is synthesized deterministically (numpy + soundfile, seeded,
no copyrighted material, ~0.6 MB total):

```sh
uv run python fixtures/gen_audio.py
```

Layout contract: `sources/fake-source.wav` is 5.7 s at 120 BPM — beats at
`0.25 + 0.5*i` s (11 beats), downbeats at 0.25 / 2.25 / 4.25 s — matching
`cache/analysis/src1.json`.
