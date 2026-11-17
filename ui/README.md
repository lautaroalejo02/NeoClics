# muestra-ui (WP8)

Local browser UI for muestra: waveform player, A/B between render
versions, loop, per-track mute/solo over render stems, rating, and
timestamped comments written to `feedback.jsonl`.

Implemented by WP8 (see `docs/spec.md` sections 5 and 12). Talks only to
`muestra-api` (WP7) — nothing console- or OS-specific here.
