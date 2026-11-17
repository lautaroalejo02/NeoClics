# Ears: objective audio metrics (WP6)

WP15: summaries list skipped checks and reasons. Instrument sidechain analysis
reports the lack of a dry instrument reference rather than asking for a chop
stem. `tonal.detune[].confidence` and optional alarm `confidence` expose the
signal strength (heuristic); weak/predicted detune is informational, explicitly
asks for listening confirmation, and has no automatic pitch fix. Possibly
intentional fourth/fifth key readings also omit their destructive fix. Drafts
support opt-in ears through `render(draft=true, analyze=true)` or
`ears.analyze(version=<draft_id>)`, using the draft's own song/audio snapshot.

The coding agent that edits `song.yaml` cannot hear. After every render,
`muestra.ears` measures the audio and tells it, in terms it can act on,
what is probably wrong. Metrics are **alarms, not ears** (spec 10): a
green report does not mean the loop sounds good (see "Blind spots").

- Entry points: `muestra.ears.core.analyze(project, version) -> Metrics`
  (side-effect free), `write_metrics` (also writes
  `renders/vNNN/metrics.json`), `compare(project, a, b) -> MetricsDelta`.
- Tools: `ears.analyze(project, version)` (writes metrics.json, returns
  the model and a compact `summary`), `ears.compare(project, version_a,
  version_b, reanalyze=False)`.
- Contract: `src/muestra/schemas/metrics.py`, `docs/contracts.md`,
  decisions entry "2026-11-17 — WP6".

## Inputs

Everything comes from the version's folder and the project:

| input | used for |
|---|---|
| `renders/vNNN/mix.wav` (24-bit, post-master) | loudness, true peak, clipping, spectrum, DC, stereo |
| `renders/vNNN/stems/<track>.wav` (float32, post track fx, pre master) | per-track spectrum, low end, sidechain, timing, key, clicks, levels |
| `renders/vNNN/stems/<track>.<lane>.wav` (pre track fx) | kick hit window, drum-lane timing |
| `renders/vNNN/meta.json` | sample rate, BPM, `range_bars`, track/stem list |
| `renders/vNNN/song.yaml` (fallback `git show vNNN:song.yaml`) | where every hit, note and clip is; fx, keys, targets |
| `cache/analysis/<src>.json` | chop `local_bpm` (detune) and the warp time map |
| `cache/stems/<src>/<stem>.wav` (only the chop span is read) | dry reference for the sidechain check |
| `ears.toml` (optional) | spectral reference + threshold overrides |

The song is turned into a **timeline** (`muestra.ears.context.Timeline`):
drum hits per lane (tiles, swing `delay = swing * step / 3` on odd
steps, track window), note events (inline `notes` or the MIDI file,
ticks = project beats), clip instances (`at_bar` + `offset_beats`, each
`repeat`), sidechain triggers (R8: the key track's events, restricted to
`key_lane`), all in render-relative seconds and convertible to
bar/beat. Two contract gaps were assumptions and are now **confirmed
against the renderer** (WP11, `render/core.py::arrange`): a pattern's
tiles are anchored at the track's `at_bar`
(`range(track.at_bar - 1, length_bars, pattern.bars)`, ears uses
`(bar - at_bar) % bars`), and everything outside the track window is
silent (`audio[:lo] = 0; audio[hi:] = 0`, then a 5 ms window fade), so
clip instances outside it are silent and a clip crossing the window edge
sounds only inside it.

## Output for a deaf agent

Every alarm has: `severity` (`info` < `warn` < `error`; `error` is the
"problem" level), a stable `code`, where (`at_s`, `bar`, `beat`, a
capped `where[]` list of occurrences, `track`/`lane`/`clip`/`chop`
ids), the measured `value` against its `threshold` with a `unit`, a
one-line Spanish `message` in musical terms, and a `fix` written as a
`song.yaml` change using the automation path grammar, with the current
value when it exists, e.g.:

```
- PROBLEMA low_end_collision [c.1, x24]: `bass` y el bombo suenan juntos en 40-150 Hz
  en 24 de 32 golpes ... -> agregá `{id: sc, type: sidechain, key_track: drums,
  key_lane: kick, depth: 0.6, release_ms: 120}` a `tracks.bass.fx` ...
```

`metrics.summary` is what the render tool should inline: one headline
(LUFS vs target, true peak, PLR, counts) plus at most 5 alarm lines,
worst first, then `(+N más en metrics.json)`. It stays under ~2 KB.
`metrics.skipped` lists every check that could not run and why — ears
never guesses silently.

`ears.compare(a, b)` gives `MetricsDelta`: LUFS/true-peak/PLR deltas,
`louder|quieter|same` (0.5 LU), `brighter|darker` and
`heavier|lighter` (1 dB change of the 4-20 kHz / 20-250 Hz share of the
mix), per-track RMS deltas, and alarms fixed / introduced / kept
(matched on `code` + `track`, info excluded), plus short Spanish notes.

## The metrics

All numbers below were measured on the synthetic corpus
(`muestra.ears.corpus`, 120 BPM, 8 bars, 24 kHz and 48 kHz) unless
stated; `tests/ears/test_detection.py` asserts them. Thresholds live in
`muestra.ears.config.Thresholds` and can be overridden in `ears.toml`.

### 1. Loudness, true peak, clipping, dynamics (`loudness`)

- **Method.** ITU-R BS.1770-4 implemented in `ears.dsp` (K-weighting
  from the analog prototype — reproduces the BS.1770 48 kHz
  coefficients to 1e-8 — 400 ms blocks, 75 % overlap, -70 LUFS absolute
  and -10 LU relative gates). EBU Tech 3342 LRA from 3 s short-term
  loudness (-20 LU relative gate, 10th-95th percentile). True peak: 4x
  oversampling with a 48-tap windowed-sinc interpolator (BS.1770-4
  Annex 2 style). Clipping = runs of >= 2 consecutive samples at
  |x| >= 0.9999 (flat tops); an isolated full-scale sample is only a hot
  peak. PLR = true peak - integrated loudness.
- **Verified.** EBU Tech 3341 cases 1-4 within 0.1 LU (48 and 44.1
  kHz); Tech 3342 LRA cases (5, 10, 20 LU) within 1 LU; true peak of a
  0 dBFS sine within 0.01 dB up to 19 kHz, -0.22 dB at 20 kHz;
  fs/4-at-45° (sample peak -3 dB, true peak 0) within 0.06 dB.
- **Alarms.** `clipping` error (any flat-top run); `true_peak_hot` warn
  above -0.5 dBTP (the master chain ends in a true-peak limiter, so this
  means it was bypassed or overdriven: lossy encoders add ~0.5-1 dB);
  `loudness_off_target` warn beyond 1.5 LU, error beyond 4 LU of
  `master.target_lufs` (the renderer gains toward the target, so a big
  miss means the limiter is fighting — the fix then says to lower the
  target); `over_compressed` warn below PLR 6 dB, error below 4
  (brick-walled: the kick loses punch and the pump reads less);
  `mix_silent`; `no_loudness_target` info. LRA is reported, never
  alarmed: a loop legitimately has a small LRA.
- **Limits.** Corpus: +1/+2 dB of drive into a TP-safe master is
  correctly reported as inter-sample overs (`true_peak_hot`) but not as
  clipping; +6 dB of hard clipping -> `clipping`. Renders shorter than
  3 s have no LRA / short-term max.

### 2. Spectral balance vs a dance reference (`spectral`)

- **Method.** Long-term power spectrum (170 ms frames, 5.9 Hz bins) of
  the mix and of every track stem, in the three fixed contract bands
  and seven nested detail bands: sub 20-60, bass 60-250, low_mid
  250-500, mid 500-2k, high_mid 2-4k, presence 4-8k, air 8-20k Hz. The
  mix's band **shares** of total power are compared with the reference
  shares (`deviation_db`).
- **Reference ("dance", default).** Pink (equal power per octave —
  the usual long-term balance of mixed music) through the mids, +4 dB
  on sub and bass (four-on-the-floor kick and bass own the low end),
  -3 dB on air (masters roll off above ~8 kHz). Tolerance ±6 dB, ±8 dB
  on sub/presence/air (the bands that vary most between good mixes).
  `"pink"` is available as a neutral reference; any band levels can be
  given in `ears.toml`. **This reference is a model, not a measurement
  of commercial future funk masters** (none were available to
  calibrate against): treat spectral alarms as the least certain ones
  and tune the reference once Lautaro picks his `references/`.
- **Alarms.** `spectral_<band>_excess|deficit`, warn beyond tolerance,
  error beyond twice it, at most 3 (they correlate). The culprit track
  (most power in that band) is named for excesses. Fixes are specific:
  raise the sample's highpass for sub/low-mid excess, open its lowpass
  when dark, lower/raise a track's `gain_db` by about half the
  deviation. If the relevant filter parameter is **automated** (a
  filter sweep, a genre signature) the alarm drops to `info` and says
  so.
- **Limits.** Long-term average only: a mix that is dark for 4 bars and
  bright for 12 averages out. The corpus "dark" defect (sample
  low-passed at 900 Hz) reads -12 dB at 2-4 kHz.

### 3. Low end 40-150 Hz: kick collisions and sidechain (`low_end_masking`)

- **Collisions.** Kick = a lane named like a kick (`kick`, `bombo`,
  `bd`, ...; else the lane with most 40-150 Hz energy). For every kick
  hit (from the song) the kick track's band level over the kick's own
  decay (time to -12 dB, 40-200 ms) is compared with every other
  track's level in the same window, from zero-phase band-passed
  post-fx stems. A hit collides when the other track is within 6 dB of
  the kick. Alarm `low_end_collision` warn when >= 25 % of hits collide
  (and >= 2), error when >= 60 % collide and the other track is as loud
  as the kick or louder (median margin >= 0 dB). The alarm lists the
  bars/beats, and the fix depends on the role: highpass ~150 Hz or
  sidechain for a sample ("el bombo se come al sample"), sidechain on
  the kick lane or moving bass notes to the off-beats for a bass.
  Tracks quieter than the mix's low band by 30 dB are ignored.
- **Sidechain audible.** A single post-fx stem cannot separate a duck
  from music that happens to be shaped like one (a sampled record has
  its own hits on the beat, exactly where the kick ducks it — measured:
  both read +10 dB "rises" at the trigger). So ears uses the **dry
  reference** every project has: the chop's source stem in
  `cache/stems/`, mapped through the chop's warp (resample: one rate;
  stretch: beat-wise on `beats_s`), with a small cross-correlation lag
  correction. gain(t) = stem/dry envelope (dB, 200 Hz highpassed so the
  track's own highpass does not matter); per trigger it is referenced to
  its own pre-trigger value, and the median over triggers is the duck
  profile. Dip depth = minimum in the first 60 ms; recovery = time until
  back within 1 dB.
  - `sidechain_missing` error: dip < 1.5 dB. `sidechain_weak` warn: dip
    < 40 % of the nominal `-20*log10(1-depth)` (capped at 20 dB).
    `sidechain_no_recovery` warn: not back before 90 % of the trigger
    gap ("queda siempre agachado"). `no_sidechain` info: there is a
    sample and a kick but no sidechain at all (the genre's breathing).
  - Measured on the corpus (depth 0.6 declared, release 120 ms): the
    measured dip is ~75 % of nominal (envelope averaging): applied
    depth 0.6 -> 6.1 dB, recovery 44 ms; 0.45 -> 4.1 dB (ok); 0.3 ->
    2.4 dB (weak); 0.2 -> 1.5 dB (weak); 0.1 -> 0.7 dB and 0 -> 0.1 dB
    (missing).
  - No source stem / analysis (sample clips from the library, missing
    cache) -> the check is skipped and listed in `skipped`. Fewer than 4
    measurable triggers -> skipped.

### 4. Grid alignment (`alignment`)

- **Drum lanes and instrument notes (`basis: events`).** The song says
  when each hit/note starts (swing included), so ears searches for the
  attack only around each expected time (first point that stays >= 1 ms
  above floor + 10 % of the rise; multi-burst claps are timed by the
  first burst). Precision on the corpus: lanes -0.6..-0.9 ms bias, MAD
  0 ms. `lane_offset` warn when a lane's median is > 15 ms off with MAD
  <= 8 ms (error > 30 ms): typically a one-shot with leading silence,
  and the fix says to change that lane's `sample`. Kick with 18 ms of
  leading silence -> flagged (+17.3 ms); 14 ms -> not (+13.4).
  Instrument tracks use a stricter MAD (4.8 ms) and never go above warn
  (slow preset attacks are a sound choice).
- **Clip tracks / the sample (`basis: beats`).** The source's own
  rhythm is unknown, so only strong spectral-flux onsets within 1/8 beat
  of a quarter note measure the systematic offset; syncopation, triplets
  and 16th pick-ups fall outside that window and cannot bias it. The
  full distribution vs the nearest 16th is reported (`histogram_ms`),
  plus `pulse_share` (quarters that carry an onset) and the off-beat
  16ths' offset (swing). Nothing is alarmed unless there are >= 8
  on-beat onsets on >= 30 % of the quarters ("not grid-like" material
  is reported in `skipped`, not alarmed). Then, in this order:
  1. `grid_drift` warn: offset changing linearly inside each chop by
     > 20 ms (tight residuals) — the loop's tempo does not match the
     grid; fix: `warp.mode: stretch` (beat-wise) or check `beats_s`.
  2. `clip_offset`: one clip instance > 20 ms away from the others and
     > 15 ms off the grid; fix: that clip's `offset_beats` (computed).
  3. `grid_offset` warn > 15 ms / error > 30 ms (median, MAD <= 8 ms):
     the whole sample is late/early — usually the beat tracker put the
     chop's downbeat a few ms off; fix: `chops.<id>.start_offset_beats`
     (the exact value: N ms = N·bpm/60000 beats, the same in source
     beats for both warp modes).
  4. `swing_mismatch` info: the sample's off-beat 16ths are > 12 ms
     later than the drums' swing; fix: the `pattern.swing` that matches
     (`swing = 3·delay/step`).

  **Honesty gate (WP12).** Before any of the above, the estimator tests
  itself on the same material: every onset is moved by ±`timing_probe_ms`
  (20 ms, kept inside the window) and the on-beat median is re-measured.
  If it misses that known shift by more than `timing_max_window_spread_ms`
  (5 ms) — which onsets fall in the ±1/8-beat window then depends on the
  shift itself — nothing is alarmed and an **info** `grid_unverified`
  says the timing could not be verified (with the onsets per beat and the
  miss). The same note replaces silence when the cluster is too scattered
  for `grid_offset` (MAD > 8 ms) and no drift/clip alarm explains it.
  `TrackAlignment` gains `verified`, `confidence` (count × tightness ×
  self-test, a heuristic, not a probability), `onsets_per_beat` and
  `window_spread_ms` (the self-test miss).
- **Measured.** Flux-peak bias corrected (+0.5 ms, consistent 22-48
  kHz). Late chop: +20 ms reads +20.3, -20 reads -20.0, MAD 0.2-0.5
  ms. **Flagged at |offset| >= 16 ms, quiet at <= 14 ms** (threshold
  15). Drift: 40 ms over a 4-bar chop flagged (fit 34.5 ms), 25 ms not
  (fit 19 ms). Swing 0.5 (20.8 ms) reads 19.5 ms -> info only. Triplet
  hats: no alarm. Single instance 30 ms late: `clip_offset` +30.0 ms,
  no `grid_offset`.
- **Limits.** Offsets beyond ±1/8 beat (±62 ms at 120 BPM) alias onto
  the neighbouring beat: a chop 70 ms late reads 54 ms *early* (still
  flagged, wrong direction). Material without onsets near the beats
  (pads, legato strings) is not evaluated. Perceptual "laid-back" feel
  inside 15 ms is not judged.

### 5. Tonal coherence (`tonal`)

- **Key.** Chroma from spectral peaks (55-2000 Hz, sqrt magnitude,
  tuning-compensated) of the **sum of the tonal stems** (drums excluded:
  they smear the chroma; `source: "mix"` when there are none), matched
  against Krumhansl-Kessler profiles **convolved with a measured
  harmonic template** (one harmonic note lights its fifth at ~1/3 of
  the root through the 3rd/6th harmonics; plain KK profiles therefore
  mistake the dominant for the tonic: 28/72 -> 63/72 scale-compatible
  on the synthetic progressions). Confidence = margin over the best
  non-relative key, scaled to 0..1. `compatible` = same scale (the key
  or its relative). `key_mismatch` warn only when the estimate is >= 2
  accidentals away with confidence >= 0.25; a neighbour key (1
  accidental: modal borrowing is common) is info at confidence >= 0.5.
- **Per track.** Key estimate, tuning, out-of-key share and the
  transposition (-6..+6) that best fits `meta.key`; `track_transposed`
  warn when shifting by k fits clearly better (+0.25 correlation, >=
  0.6) — e.g. a `pitch_semitones` mistake; fix: the exact
  `pitch_semitones` (or transpose the notes).
- **Detune (the future funk classic).** For every resampled chop:
  expected shift = 12·log2(project_bpm/local_bpm) + pitch_semitones from
  the snapshot + analysis, and its distance to the nearest semitone.
  Measured tuning of the stem = mode of a magnitude-weighted histogram
  of spectral-peak deviations from A440 (the mode, not the mean, so the
  naturally flat 5th/7th harmonics do not bias it), relative to the
  instruments' tuning (A440 if none). `sample_detuned` warn > 20 cents,
  error > 35 (measured; "predicted" warn when it cannot be measured).
  Fix: `pitch_semitones: <current - cents/100>`, and — only when the
  prediction explains what is heard — the `meta.bpm` that makes the
  resample land on a whole semitone. A record that is itself off A440
  (tape speed) is measured, not predicted, and the BPM fix is not
  offered.
  Measured: error <= 0.6 cents (5-45 cents); flagged from 22 cents,
  quiet at 18.
- **Bass vs sample.** The bass notes are known exactly from the song:
  their duration-weighted pitch-class histogram is correlated with the
  sample's chroma, also at every transposition. `bass_sample_clash`
  warn when a transposition fits clearly better (fix: transpose the bass
  or the sample by that amount) or when >= 30 % of the bass time sits a
  semitone away from the sample's strongest notes.
- **Limits.** Key estimation on real separated stems is harder than on
  synthetic chords (expect more `info`, rare `warn`). Chroma is
  loop-averaged: a chord that clashes for one beat is invisible.
  Non-12-TET or heavily inharmonic material (bells, detuned chorus)
  confuses tuning; `tuning_min_strength` gates it.

### 6. Clicks (`clicks`)

(WP13 renderer change.) Contiguous chop repeats are now joined by an
equal-power crossfade that starts at the boundary (the previous repeat's
tail of source fades out while the next fades in over `fade_ms`), and a
full render's end is not faded when a clip track wraps into bar 1. The
detector needs no change: the reference window (5 ms ending `fade_ms`
before the edge) is the material being played either way, and the loop
seam stays the junction end→start. Verified on a skill-check render: 0
click events (was 1 `loop_seam_click` before the effects at the wrap were
warmed). `fade_ms` now means the crossfade length, so the `click` fix
("subí `fade_ms`") still applies.

- **Method.** Ears knows every edge (clip instance starts/ends, note
  starts/ends, the loop seam end->start), so it only looks there, ±3
  samples. Score: the 2-sample linear-prediction error (second
  difference) at the edge vs its RMS over the 5 ms **before** the edge,
  ending before the chop's declared `fade_ms` (and before the 5 ms R7
  tail fade for the seam): a step from an un-faded cut is a spike,
  low-frequency content is suppressed by ~(2πf/sr)², and the downbeat
  attack that usually follows a chop edge does not hide the step
  because it is not in the reference. Click = ratio >= 8 and jump >= -54
  dBFS; error above -30 dBFS. Events are mapped to the clip ref and chop;
  fix: `chops.<id>.fade_ms` (doubled, >= 10).
- **Measured.** Un-faded chop edge over a sustained layer: ratio ~600,
  jump -14 dBFS (seam: -21 dBFS); the same edges with the 5 ms fade:
  below -54 dBFS. Cutting through hats/noise without a sustained layer
  does not produce an audible step and is not flagged.
- **Limits.** Drum lanes are not scanned (a hit is an intended
  discontinuity), nor the mix seam (the bar-1 kick is an intended
  attack). Clicks away from known edges (zipper noise from automation, a
  1 ms sidechain attack on a sustained bass) are not searched.

### 7. Technical extras (`technical`)

- `dc_offset` warn above -45 dBFS on the mix (0.006 flagged, 0.004 not);
  fix: a 20 Hz highpass on the master or the offending track (per-track
  DC is reported).
- `low_end_phase` warn when the < 150 Hz L/R correlation is below 0.5 or
  the mono sum loses > 3 dB there (a bass in opposite polarity on one
  side read 0.29 / 1.9 dB). Fix: centre bass/kick, no stereo fx on them.
- `track_silent` warn: a non-muted stem peaks below -70 dBFS.
- `track_inaudible` warn: a track is >= 20 dB below the rest of the mix
  in every detail band in >= 95 % of its active frames (a pad at -45 dB
  -> audible share 0 %).
- Stereo correlation of the full mix and per-track peak/RMS/DC are
  reported.

### 8. Style similarity

Out of scope for v1: `style_similarity` stays `null`. Hook:
`muestra.ears.style.register(scorer)` with a `StyleScorer` (`model`,
`score(mix, sr, references) -> StyleSimilarity`); references come from
the project's `references/` folder. A failing scorer is reported in
`skipped`, never raised.

### 9. Mix balance (`balance`, WP18)

**Why.** The master normalises loudness toward `target_lufs`, so absolute
loudness hides the classic agent failure: one track so hot the rest is
inaudible (found by ear in `session-devin`: the sample sat ~30 dB above
the drums and every metric stayed green). Ears now reports the relative
level of the mix roles — kick, rest of drums, bass, sample, other
sustained tracks — and alarms on imbalance.

**Roles** come from the song, not from stem names: pattern lanes named
like a kick (`kick/bombo/bd/kik/bassdrum`, else the lane with the most
40-150 Hz energy), the remaining pattern lanes as the rest of the kit,
instrument tracks in the bass register (median note < 55) or named like
a bass, clip tracks (minus bass-named ones) as the sample, everything
else as `other`. `other` can bury but is never accused of being buried
(aux layers are legitimately quiet).

**Fairness — every role is measured while it sounds.** Whole-file RMS is
unfair twice over: it charges a sparse kick for its silence and rewards
a continuous pad for filling every frame.

- Drum lanes: mean K-weighted power inside `[hit, hit+W]` at the
  pattern's own hit times (`W = min(0.3 s, 0.8 × median gap)`), on the
  per-lane stem, rescaled to the post-fx track bus by the ratio of bus
  power to lane power over the union of the track's hits (a track
  compressor makes that ratio approximate — documented, not exact).
- Sustained tracks (bass, sample, other): self-gated active blocks —
  400 ms blocks every 100 ms, keeping blocks within
  `balance_active_gate_db` (20 dB) of the track's loudest one.

All levels are K-weighted (BS.1770) mean powers on the pre-master stems,
expressed as a LUFS-like dB and pooled (power-sum) per role; the readout
normalises to the loudest role. A 1-hit/bar sparse kick measures the
same as a 4-hit/bar kick at identical hit level (duty-cycle invariance,
tested), and a pad made of the kick's own one-shot looped continuously
lands within ~1 dB of it (same sound, never silent).

**Alarms** (all `thresholds.*` overridable in `ears.toml`):

| code | severity | fires when | fix |
|---|---|---|---|
| `balance` | info | always: `balance: bombo 0, bajo -2, sample -5, resto de batería -8 dB` | — |
| `balance_buried` | error | a core role (kick/drums/bass/sample) ≥ `balance_buried_db` (16) under the loudest role | lower the dominant track's `gain_db` or raise the buried one — never everything |
| `balance_kick_back` / `balance_drums_back` / `balance_bass_back` | warn | the role ≥ `balance_back_db` (10) under the loudest melodic role (sample or other) | same relative-level fixes |
| `balance_sample_back` | warn | the sample ≥ `balance_sample_back_db` (12) under the kick | raise the sample or lower the kick |
| `balance_bass_front` | warn | the bass ≥ `balance_front_db` (10) over the kick | lower the bass or raise the kick |
| `balance_drums_front` | warn | the rest of the kit ≥ `balance_drums_front_db` (14) over the sample | lower the drums |
| `balance_dominant` | warn | the loudest role ≥ `balance_dominant_db` (14) over the runner-up | "subir `gain_db` ya no lo hace sonar más": the master nivela and the limiter works for that track |

Fixes always change **relative** levels (`tracks.<id>.gain_db` or
`tracks.<id>.pattern.lanes.<lane>.gain_db`), never master gain, and say
so: the normaliser cancels "raise everything".

**Reference points.** The thresholds are starting points calibrated by
measurement on the four real projects (`demo-check-12410`,
`session-codex`, `skill-check`, `session-devin`), *not validated by
listening*: they encode that a healthy future-funk mix keeps every core
role within ~10 dB of its neighbours, that 16 dB under anything means
inaudible (matching `inaudible_margin_db` = 20 on band-resolved
audibility), and that a ≥14 dB single-role margin means that role alone
drives the master. `session-codex` (everything within ±7 dB) stays
quiet; `session-devin` warns from v001 (bass −12 under the sample) and
errors from v006; `demo-check-12410` and `skill-check` v001/v010/v014
fire on real extremes (a bass 15–25 dB under the bus, a drum bus 16 dB
over the loop) — reported, not silenced.

`ears.compare` adds `balance_delta_db` (per-role level change, dB) and
notes like "el sample bajó 28 dB respecto de la batería".

**Limits.** The readout measures stem loudness, not perception:
frequency masking, pan and arrangement decide what a listener actually
hears, so a balanced readout does not prove a balanced mix. The
lane→bus correction approximates any non-linear track fx on the drum
bus. A role missing from the stems is simply absent.

## Configuration: `ears.toml`

```toml
[spectral]
profile = "dance"                 # or "pink"
# levels_db = {sub = 3, bass = 4, low_mid = -3, mid = 0, high_mid = -3, presence = -3, air = -5}
# tolerance_db = {air = 10}       # per band
# default_tolerance_db = 6

[thresholds]                      # any field of muestra.ears.config.Thresholds
timing_warn_ms = 15.0
detune_warn_cents = 20.0
# balance (WP18): active_gate 20, back 10, sample_back 12, buried 16,
# dominant 14, front 10, drums_front 14 — starting points, not tuned by ear
# balance_buried_db = 16.0
```

Unknown keys are errors.

## Performance

16 bars at 120 BPM, 4 tracks + 3 drum lanes, 48 kHz: ~1.3-1.6 s per
analysis on the Windows dev laptop (warm, median 1.31 s of 5 runs, other agents
running); ~0.4 s of it is reading the wavs. Under pytest on a loaded
CPU it measured up to ~4 s; the test only asserts < 12 s. Costs scale
linearly with length x tracks. All audio is processed as float32;
running sums are float64. The WP18 balance check adds ~0.13 s on the
same render (measured: 132 ms warm, 16 bars @ 48 kHz, K-filtering the
stems dominates); the test asserts < 0.3 s.

## Validation on real renderer output (WP11, 2026-11-17)

The synthetic corpus is a mini-renderer; WP11 checked ears against the
product renderer on the demo project (`~/Documents/MuestraProjects/demo-e2e`:
a 2-bar chop of the CC0 1928 "Tiger Rag" — tracked at 250 BPM,
time-stretched to 125 — real htdemucs stems, `disco-01` kit, Surge XT
`slap-bass`, SFZ `rhodes`, 16 bars). Variants differ from the clean song in
one line each and were rendered with the `render` tool.

| variant | expected | ears result |
|---|---|---|
| clean | no timing / click / sidechain / detune alarms | none of those; 2 real warns (see below) |
| sidechain removed | `no_sidechain` | `info no_sidechain` |
| sidechain depth 0.15 (nominal 1.4 dB) | weak, not missing | measured 2.57 dB: no alarm |
| resample at 128 BPM (12·log2(128/250) = -11.59 st) | `sample_detuned` | `warn sample_detuned` 41.1 cents (predicted; matches) |
| resample at 125 BPM (exactly -12 st) | nothing | nothing |
| `fade_ms: 0` | click at chop edges | `warn click` -35.9 dBFS |
| chop 30 ms early (`start_offset_beats: +0.0625`) | `grid_offset` ≈ -30 ms | `warn grid_offset` **-21.4 ms** (see below) |
| chop 30 ms late (`start_offset_beats: -0.0625`) | `grid_offset` ≈ +30 ms | **missed** (no alarm) |

Measured on the clean loop: sidechain dip 5.68 dB vs 6.02 nominal
(dry reference found in `cache/stems/src1/<model-key>/other.wav` through
`cache/stems/src1/active.json`), drums / bass / keys within ±2.6 ms of
their events, key `D# major` (= Eb) at 0.88 confidence, `in_key`.

**Fixed: instrument note onsets read as clicks.** The first real render
raised `error click` on 59 bass note starts (Surge `FM Slap`). They are
real steps — jump vs the next 5 ms is -1..-6 dB, like an un-faded cosine
start (+3 dB) and unlike a smooth attack (-34 dB) — but each note rises
+17.3..+17.6 dB out of the previous note's release tail: it is the
preset's own attack, the instrument equivalent of a drum hit (which ears
already does not scan). A note start that rises at least
`click_onset_rise_db` (12 dB) above what was sounding is now skipped;
retriggers over a sustained sound and note ends are still scanned.

**Fixed (WP12): legato retriggers of an FM-slap preset read as clicks.** The
Yung Bae example with `slap-bass` raised `error click` twice (-27.7 dBFS, ratio
221) at the first pass of a phrase only. Sample-level inspection: every pass has
the identical slap transient (sharpest second difference 0.041), starting 2-5
samples after the nominal note time; it was flagged only where it landed inside
the ±search window, on a note that starts where the previous one ends (legato:
the note-end edge was scanned as a cut). Now a note end that coincides with the
next note start is scanned once, as that start, and a retrigger whose spike is
within `click_attack_margin_db` (6 dB) of the median attack spike of the same
track's notes out of silence (first 3 ms) is the preset's attack. A sustained
note cut dead under the next attack, or anywhere else, is still a click
(synthetic tests). Demo clean render: 0 clicks; Yung Bae + slap: 2 -> 0.

**Clarified: `loudness_off_target` with a healthy PLR.** The clean loop
ends at -13.0 LUFS against a -10 target with PLR 12 dB. Instrumented: the
mix is -10.00 LUFS / +7.97 dBTP *before* the renderer's true-peak safety
limiter (crest 18 dB: kick and clap far above a quiet sample), and the
limiter's ~9 dB of peak reduction costs 3 LU. The alarm is right; its fix
now names the cause (raise the quiet tracks / lower the drums, or lower
the target to what the master gives) instead of "check master.gain_db",
which cannot help because the renderer normalises toward the target.

**Sample grid alignment on dense live material (WP11 finding; WP12 made
the output honest, the estimator is unchanged).** The renderer
places the chop exactly (cross-correlating the variant stems against the
clean one: +30.0 / -30.0 ms). Ears does not follow: per-instance offsets
are tight (clean -21.8 ms, the 8 repeats agree within 2.3 ms) but move
+59.7 ms for the late variant and +0.3 ms for the early one. On a 1928
ragtime band there are onsets every 16th; which of them fall inside the
±1/8-beat window changes with the shift, so the median is not a shift
estimator on this material. The clean loop raised nothing only because
its whole-track MAD (14.8 ms) is above the 8 ms gate; the early variant's
alarm (-21.4 ms, MAD 6.1) has the same value as the clean loop's own
distribution, i.e. it is a coincidence, not a detection. Thresholds were
NOT changed: no single threshold separates these cases. Treat
`grid_offset` on busy live records as unreliable in both directions;
the reliable signals there are the drum/instrument event timings (exact)
and the agent's own `start_offset_beats` changes. A fix needs a different
estimator (e.g. onset-pattern cross-correlation against the dry stem
mapped through the analysis grid), left for a later WP.

WP12 re-ran the three variants with the self-test (median / MAD /
self-test miss -> verdict): clean -25.0 / 13.8 / 13.8 ms -> `info
grid_unverified`; chop 30 ms late +37.8 / 22.2 / 0.0 ms -> `info
grid_unverified` (scattered); chop 30 ms early -21.5 / 1.0 / 6.1 ms ->
`info grid_unverified` (it used to be a coincidental `warn grid_offset`).
The late chop is still not *detected*: ears now says it could not check,
which is the truth on this material. On the synthetic corpus every timing
detection is unchanged (self-test miss 0-3 ms), and the corrected-grid
Tiger Rag example (v030, resample) verifies: -8.5 ms, MAD 5.5, miss
3.1 ms, confidence 0.41, no timing alarm.

Remaining warn on the clean loop: `spectral_sub_deficit` -9.3 dB vs the
modelled "dance" reference (the bass enters at bar 5 and the kick is a
62 Hz 909-style one). It is consistent with the material; whether it is
a problem is exactly what the reference model cannot know (see
"Spectral balance").

### Skill run findings (WP9 → fixed in WP11)

`docs/skill-findings.md` 1-3, regression tests in `tests/ears/test_skill_findings.py`:

1. **`loudness_off_target` never suggests `master.gain_db`.** With `target_lufs` the
   renderer normalises after `master.gain_db`, so it cancels out (measured on the demo
   loop: `master.gain_db +6` → -12.96 LUFS, unchanged). The fix now names the drum track(s)
   to lower (drums -6 dB → -11.34), raising the quiet tracks (sample +6 / bass +3 →
   -12.05), or a realistic target (-13 → -14.39); the "louder than target" branch points
   at `master.fx` / the target instead.
2. **A measurement beats the A440 prediction.** Below `tuning_min_strength` the render's
   tuning used to be ignored and the prediction (which assumes the record is at A440)
   alarmed with a fix that re-detuned a compensated record. Now a weak measurement
   overrides a predicted alarm (silences it when the render measures in tune; otherwise it
   sets the value and the fix, basis "medido, señal débil"). A weak measurement never
   creates an alarm the prediction does not have, so noisy material cannot add false
   alarms. Test: a record at -30 cents compensated by a +30-cent resample, measurement
   forced weak → no alarm (the prediction alone says +30).
3. **Fourths/fifths are not transpositions.** Key profiles cannot tell a tonic from its
   dominant: `track_transposed` at ±5/±7 semitones, or for a track whose bass/sample pair
   fits (`best_shift_semitones` 0, fit ≥ 0.5), is now `info` ("probablemente a
   propósito"). Test: the corpus (F#m) with `meta.key: Bm` → two `track_transposed` at -5,
   both info.

## Validation on the four real projects (WP18, 2026-11-18)

Balance check run on every render of the four real projects (copies;
originals untouched). Readout = each role vs the loudest, dB; alarms:
`W:` warn, `E:` error, `balance_` prefix elided. `session-codex` is the
balanced reference (quiet everywhere); `session-devin` v001-v009 is the
bug that motivated this check; v010 is the same song with the sample
lowered 12 dB.

| version | balance readout (dB) | alarms |
|---|---|---|
| **demo-check-12410** | | |
| v001 | resto batería 0, bombo -6.6, sample -15.7, bajo -15.9, otros -18.9 | W:drums_front |
| v002 | resto batería 0, bombo -6.6, sample -16.6, otros -18.9, bajo -25.3 | E:buried ×2, W:drums_front |
| v003 | resto batería 0, bombo -6.6, sample -15.7, bajo -15.9, otros -18.9 | W:drums_front |
| v004 | resto batería 0, bombo -6.6, sample -15.7, bajo -20.7, otros -27.7 | E:buried, W:drums_front |
| **session-codex** | | |
| v001 | resto batería 0, sample -0.6, bombo -1.1, bajo -2.6 | — |
| v002 | bajo 0, resto batería -2.2, sample -2.9, bombo -3.3 | — |
| v003 | bajo 0, sample -2.1, resto batería -2.2, bombo -5.3 | — |
| v004 | bajo 0, sample -2.4, resto batería -2.8, bombo -5.8 | — |
| v005 | bajo 0, sample -2.5, resto batería -2.8, bombo -5.8 | — |
| v006 | bajo 0, resto batería -2.3, sample -2.9, bombo -5.3 | — |
| v007 | bajo 0, resto batería -0.8, sample -2.9, bombo -4.5 | — |
| v008 | bajo 0, resto batería -0.8, sample -2.6, bombo -4.5 | — |
| **skill-check** | | |
| v001 | bombo 0, resto batería -0.9, sample -4.5, bajo -18.3 | E:buried |
| v002 | resto batería 0, sample -0.6, bombo -1.1, bajo -8.1 | — |
| v003 | resto batería 0, sample -0.6, bombo -1.1, bajo -5.6 | — |
| v004 | resto batería 0, sample -0.6, bombo -1.1, bajo -2.6 | — |
| v005 | bajo 0, resto batería -4.3, sample -4.9, bombo -5.4 | — |
| v006 | bajo 0, sample -4.2, resto batería -4.3, bombo -7.4 | — |
| v007 | bajo 0, sample -2.1, resto batería -2.2, bombo -5.3 | — |
| v008 | bajo 0, sample -2.1, resto batería -2.2, bombo -5.2 | — |
| v009 | resto batería 0, sample -0.6, bombo -1.1, bajo -2.6 | — |
| v010 | sample 0, resto batería -5.1, bombo -6.4, bajo -15.3 | W:bass_back |
| v011 | bombo 0, resto batería -1.8, sample -4, bajo -4.7 | — |
| v012 | sample 0, bombo -7, resto batería -7.7 | — |
| v013 | resto batería 0, bombo -1.1, bajo -9.3, sample -11.2 | — |
| v014 | sample 0, resto batería -5.1, bombo -6.4, bajo -15.3 | W:bass_back |
| v015 | bombo 0, resto batería -1.8, sample -4, bajo -4.9 | — |
| v016 | sample 0, bombo -3.6, resto batería -7.3 | — |
| v017 | bombo 0, resto batería -1.8, sample -4, bajo -6.8 | — |
| **session-devin** | | |
| v001 | sample 0, resto batería -1.7, bombo -3, bajo -11.9 | W:bass_back |
| v002 | sample 0, resto batería -1.6, bombo -2.9, bajo -11.8 | W:bass_back |
| v003 | resto batería 0, sample -0.4, bombo -3.2, bajo -12.2 | W:bass_back |
| v004 | resto batería 0, sample -0.4, bombo -3.2, bajo -12.2, otros -12.9 | W:bass_back |
| v005 | resto batería 0, sample -0.3, bombo -3.2, bajo -12.2, otros -13.4 | W:bass_back |
| v006 | sample 0, bombo -20.7, resto batería -20.8 | E:buried ×2, W:dominant |
| v007 | sample 0, bombo -17.7, resto batería -20.8, bajo -35.4 | E:buried ×3, W:dominant |
| v008 | sample 0, bombo -17.7, resto batería -20.8, bajo -31.9 | E:buried ×3, W:dominant |
| v009 | sample 0, bombo -17.7, resto batería -20.8, bajo -31.9 | E:buried ×3, W:dominant |
| v010 | bajo 0, resto batería -0.1, bombo -1.3, sample -10.5 | — |

Worth saying out loud: the check fires on some versions of the
"acceptable" projects — `demo-check-12410` keeps a drum bus 16 dB over
the loop and a bass 21-25 dB under it in v002/v004; `skill-check` v001
has a bass 18 dB under the kick and v010/v014 one 15 dB under the
sample. Those are real measurements, not noise; nobody ever listened to
those projects, so they are reported rather than assumed wrong.

## Calibration corpus

`muestra.ears.corpus.generate(CorpusSpec(...), root)` writes a complete
project with one render following the R7 output contract: a 4-bar chop
rendered from a synthetic "record" (electric-piano chords, a lead line,
the record's own snare/hats and leaking bass) through a real resample
warp, a kick/clap/open-hat pattern, an off-beat octave bass, a pad, a
true-peak-safe master. `Defects` switches on: late chop (beat tracker
error), one late clip, tempo drift, un-faded cuts, kick+bass collision,
sample without highpass, resample detune, off-A440 record, wrong
`pitch_semitones`, master clipping, missing/weak/absent sidechain,
swung or triplet sample, dark sample, silent track, DC, low-end phase,
kick one-shot with leading silence, inaudible pad, squashed master,
per-role level mistakes (`sample/bass/drums_gain_db`) and a sparse kick
(`sparse_kick`, for the balance fairness test).
It is a mini-renderer for tests, not the product renderer.

## Blind spots (what can sound bad while every metric is green)

Be explicit with the agent: green means "no known defect detected".

- **Taste and arrangement**: a boring loop, a bad chop choice, a chop
  that cuts a word in half, a groove that does not swing, the wrong
  sample for the style. Only Lautaro's ears judge that.
- **Sound quality artifacts**: time-stretch/pitch-shift warble, phasey
  separation artifacts in the stems, aliasing, harsh plugin
  distortion, reverb mud — none is measured.
- **Time-local problems**: spectrum and key are loop averages; a clash
  or a frequency hole lasting one beat, or a filter sweep that closes
  too far for two bars, can pass.
- **Masking outside 40-150 Hz**: kick vs sample is only checked in the
  low band; a clap burying the vocal chop in 1-4 kHz is not.
- **Sidechain without a dry reference** (library sample clips, missing
  cache) is not verified (reported in `skipped`). Pumping that is
  musically wrong (too slow/fast for the tempo) but present passes
  unless it never recovers.
- **Perceived vs measured balance**: `balance` compares stem loudness
  while each role sounds. Masking, pan, note choice and arrangement
  decide what a listener actually hears — a flat readout does not prove
  the mix *sounds* balanced, and the reference ranges were never
  validated by ear.
- **Timing**: offsets beyond ±1/8 beat alias (wrong sign); material
  without on-beat onsets is not evaluated; micro-timing feel under 15 ms
  is not judged; MIDI with slow-attack presets is only reported.
- **Clicks** away from known edges (automation zipper noise, a sharp
  sidechain attack) and inside drum one-shots are not searched.
- **Spectral reference** is a documented model, not fitted to
  commercial masters; mastered references in `references/` should be
  used to re-tune it.
- **Loudness** is measured on the render only; the true peak of the
  final lossy encode is not modelled (hence the -0.5 dBTP warn).
