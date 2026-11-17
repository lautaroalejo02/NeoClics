# WP19 ingest evaluation - 2026-11-17

Status: in progress. CPU-only Windows 11; no listening claims, paid services,
commercial recordings, or LLM inference. Audio stays outside this repository.

## Material and provenance

MDB Drums, published by Carl Southall et al., explicitly licenses audio and
annotations CC-BY-NC-SA 4.0. Evaluation only; do not bundle with the product.
Source: https://github.com/CarlSouthall/MDBDrums, revision
`b29e2d63c3a023506f4bf353c5b2e8a558eed135`.
Its 23 Music Delta recordings include 80s rock, disco, funk/fusion/swing jazz,
rock/pop and harder/slower controls, with published beat-position annotations
and isolated instrument stems. These are stylistic demonstrations/excerpts,
not Argentine rock or city pop. No generalisation to the owner's music is proven.

## Reproduction / pending measurements

`uv sync --extra separation` completed. Dataset setup and benchmark commands,
partial results and final aggregates will be appended at committed checkpoints.
Audio, per-item dated licence notes, hashes and raw results remain outside git.
MDB Drums supplies no key/section/phrase annotations: estimator consensus must
not be called ground truth. Reference stem mixing/scaling needs validation.
Short excerpts cannot establish actual four-minute peak RAM. Human listening
is still required for bar phase and sample bleed. Nothing has been heard here.

## Baseline checkpoint (23/23 tracks, no analysis failures)

Downloaded 180 items / 1,340,076,252 bytes. All 23 tracks carry published
beat positions; position 1 supplies downbeats. Provenance and hashes:
`C:/Users/lauti/Documents/MuestraLibrary/_test-audio/eval/mdb-drums/SOURCES.md`.

Macro averages, **no first-five-second trim**, including pickups/endpoints:

| Metric | Before |
|---|---:|
| Tempo mean absolute percentage error | 5.3543% |
| Tempo correct within 4% | 21/23 |
| Half / double / 3:2 / 2:3 errors | 2 / 0 / 0 / 0 |
| Beat F70 / precision / recall | .899166 / .917902 / .895842 |
| Beat CMLc / CMLt / AMLc / AMLt | .768589 / .815240 / .871099 / .934185 |
| Downbeat F70 / precision / recall | .851565 / .822569 / .905033 |
| Downbeat CMLc / CMLt / AMLc / AMLt | .730171 / .782776 / .772384 / .842009 |
| Reliable track-time fraction | .736613 |
| Actually correct complete-bar track-time fraction | .664177 |
| False-reliable track-time fraction | .072436 |
| False-reliable conditional on marked time (macro, excludes empty maps) | .167930 |
| Beats F70 >= .9 and downbeats F70 < .5 | 0/23 |
| Key accuracy / relative / parallel / fifth errors | unmeasurable: zero labelled keys |
| source.analyze total wall time | 192.10 s; first track was cached |

The two half-tempo cases are Bebop Jazz and Gospel (annotated near 222 BPM,
tracker/effective median near 111). Speed Metal has the right tempo but the
**wrong half-time beat parity**, and F70=0 although tolerant relative-period
CML downbeat scores look good. Its raw downbeat parity changes after irregular
tracker detections; majority downbeat parity selects the quieter offbeats.
Actual spectral-onset strength favours the other parity by >2:1 in both
35-180 Hz and 180-4000 Hz bands. This is a concrete, testable correction target.

### Separation checkpoint

Reference mixing was checked on every track. Some released multitracks do not
reconstruct their published mix (e.g. 80s Rock -10.99 dB, Beatles -5.84 dB,
Swing Jazz -0.78 dB residual). They remain valid **beat** test material but
must not support sample-aligned separation claims. The separation harness
refuses sums worse than -25 dB residual. Disco is -49.28 dB; Funk Jazz -72.81
dB; Britpop -28.76 dB. Reference gains are fitted to input once, never estimates.

Disco first 20 s: default htdemucs 40.69 s / 1273 MiB peak RSS; SDR
drums/bass/vocals/other 14.44/14.38/13.44/8.34 dB. htdemucs_ft 113.29 s /
1849 MiB, 15.04/14.83/14.01/8.91 dB. This is sample-aligned energy-ratio SDR,
not filtered BSS Eval. Wrong-source simultaneous projection into other:
vocals .11295% -> .09251%, bass .02744% -> .02520%, drums .08574% -> .07608%.
These are correlated-source attribution proxies, NOT isolated audible bleed.
RoFormer and actual four-minute throughput measurements are still pending.

## Acoustic hardening checkpoint

Full 23-track remeasurement: beat F70 **.899166 -> .941285**, downbeat F70
**.851565 -> .895043**. Speed Metal changes from 0 -> .96875 beat F70 and
0 -> 1.0 downbeat F70; no other track's beat/downbeat F70 changes. Half-time
parity is selected by onset evidence only at >=16 beats and >1.5:1 contrast.
Weak evidence retains the old parity. The original tracker arrays remain
available for reversible regridding. Existing manual grids/song chops do not
move on cache upgrades. Structural regularity alone no longer claims verified
bar phase: automatic confidence is capped at .55 and `ask_human` is true.

Pulse-free regions additionally need distinct onset support at >=40% of beats
(minimum two) to count as reliable. Cached spectral evidence is reused, and
regrid checks the new grid against audio. Drift/pickup/shuffle, pulse-free
intro, strong/ambiguous parity and manual-cache preservation have fast tests.
`uv run pytest tests/ingest -q`: **71 passed, 3 skipped**, 54.40 s on the
shared laptop. Whole-repository validation is still pending.

False-reliable track-time falls **.072436 -> .043409**; conditional
false-reliable macro fraction **.167930 -> .120311**. Marked reliable time
**.736613 -> .731276**; actually correct complete-bar time **.664177 -> .703733**.
This is not elimination of the dangerous case: Gospel remains half-time and
some structurally consistent bars remain wrong. No new global tempo-octave
policy or automatic downbeat phase shift is justified by this set.

Reference-drums and 50:50 reference-drums/mix runs completed for all 23 tracks.
These are **oracle input ablations**, not measurements of estimated Demucs
drums. Preliminary beat/downbeat F70: drums .909177/.840109, fusion
.889389/.820355. The final consistent-version comparison is pending. This
does not justify replacing the mix-first default.

The 23 tracks contain no annotated keys. The harness now compares KS summed
chroma with an independent frame-normalized weighted diatonic-scale cosine
estimator, and tests harmonic reference stems only where reconstruction passes
the gate. Confidence-bin accuracy is emitted only for supplied labelled keys.
There is no defensible real-recording key accuracy/calibration claim yet.

Disco 20 s RoFormer cascade completed: **450.57 s**, **2785 MiB** sampled peak;
drums/bass/vocals/other SDR **15.22/14.79/17.18/9.47 dB**. Wrong-source
projection into other: vocals **.01307%**, bass **.02203%**, drums **.09794%**.
Compared with htdemucs, vocal projection falls ~8.6x but drums increase.
Actual four-minute Demucs / fine-tuned runs are in progress; RoFormer at four
minutes is not yet measured and cannot inherit a 20-second peak-RAM figure.

An additive CLI now produces instrumental/no-drums sums as **ordinary source
files**, preserving a parent-grid snapshot and remeasuring key/loudness/pulse
reliability without neural retracking. No schema, song, renderer, API, UI or
ears files were changed. Agent-facing native-tool integration is still needed.

Correct-grid chop evaluation: **1,216** 8/16-beat loops across all 23 tracks;
macro Spearman **.201231** against the held-out 200-ms seam/join proxy.
Mean proxy **.435608** across all candidates, **.499486** among top-three
ranked candidates. Phrase-start annotations do not exist here; harmonic return
does not prove a phrase boundary. Scoring changes are not yet justified.
