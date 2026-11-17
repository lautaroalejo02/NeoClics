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
