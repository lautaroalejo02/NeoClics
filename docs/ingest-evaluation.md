# WP19 ingest evaluation - 2026-11-17

CPU-only Windows 11; no listening claims, paid services, commercial recording
downloads, or LLM inference. Audio stays outside this repository.
**The final consolidated tables at the end supersede the historical checkpoints
below**, including their pending statements. Final whole-repository validation
passes; one additional separation control is still running at this checkpoint.

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

## Loop scoring and paired-stem checkpoint

The scorer now checks **both 80/320-ms continuation contexts** plus the actual
sample join, normalized by the natural local 95th-percentile derivative.
Weights and tonal/energy/onset/drift components are unchanged. It catches a
bad end sample that the old AFTER-end comparison never saw, and delayed
harmonic mismatch beyond the old 80-ms window. Two generated-signal regression
tests cover those exact failures. Full 1,216-loop remeasurement gives Spearman
**.201231 -> .870479**, top-three proxy **.499486 -> .726128** (all-loop mean
unchanged .435608). Every metadata-genre stratum improves in the paired
experiment, not just the overall mean. This is a **development-set proxy**,
not held-out musical/perceptual validation; the alternate 200-ms horizon is
not an independent source of phrase labels.

A paired **estimated htdemucs drum** cohort (Britpop, Disco, Funk Jazz, same
first 20 seconds and cropped published annotations) gives mix/drums/fusion
beat and downbeat F70 **1.0/1.0 in all three modes**. Their source.analyze wall
totals are 11.89/18.51/21.45 s, **excluding separation**, which is shared and
cached in a separate audit project. This small/easy cohort does not establish
that estimated drums help difficult tracks. It does support keeping cheap
mix-first analysis, and prevents confusing the 23-track oracle drum ablation
with a real separation experiment. `evaluation --separate-model htdemucs
--max-seconds 20 --input-mode drums|fusion` reruns this comparison.

Parity-only reliability ablation (recompute `assess_reliability` without pulse
evidence on the corrected cached grids) equals the combined real-track map.
Thus **all observed real-track F70 and false-reliable improvements come from
the parity fix**, not the new pulse filter. The filter rejects generated
silence/sustained-tone intros without changing this already-pulsed corpus.
Its benefit on real drumless/nonrhythmic intros is still unverified.

Key-estimator agreement: KS vs weighted scale cosine **18/23**. On the ten
reference-reconstruction-valid tracks, mix KS vs harmonic-reference KS agrees
**8/10**. These are agreement, not accuracy: Country 1 changes G major ->
D major; Punk changes D minor -> G major. No real-key accuracy or calibrated
confidence is established, and the product key estimator remains unchanged.

Actual four-minute Disco repetition, htdemucs: **402.63 s**, **2977 MiB**
sampled process-tree peak, SDR drums/bass/vocals/other
**14.17/14.16/14.32/8.02 dB**. Peak includes reference-scoring buffers as well
as separator/model load, so it is not app-only separation RAM. The fine-tuned
four-minute run and additional 20-second quality controls are still running.

### Restricted datasets not acquired

The official MUSDB18 page explicitly requires a manually reviewed access
request for the full dataset: https://sigsep.github.io/datasets/musdb.html.
MedleyDB/MedleyDB 2.0 full audio similarly requires Zenodo access requests:
https://medleydb.weebly.com/downloads.html. No form or account was submitted.
The freely distributed MDB Drums subset above needs neither. The MUSDB short
preview was not needed once this larger annotated subset was obtained.

Potential training overlap between these common MIR tracks and pretrained
models has **not** been audited. These are not demonstrated held-out model
test tracks. Relative in-corpus comparisons and CPU costs must not be sold
as generalisation to the owner's recordings.

## Final consolidated corpus

23 recordings / **1,308.75 seconds**, each 13-125 seconds. Source for EVERY
row: MDB Drums, revision and attribution above. EVERY row: **CC-BY-NC-SA 4.0**,
published beat-position annotations (position 1 = downbeat), released stems.
No row has key/section/phrase labels. `S+` means the -25 dB reference-sum gate
passes, `S-` fails: these are reference-validity labels, NOT separator quality.
The Beatles/Hendrix/etc titles are Music Delta style demos, not those artists'
commercial recordings. These short studio demonstrations do not establish
behaviour on full-length tape-era Argentine rock or city pop.

| Music Delta track | Source | Licence | Why chosen | Reference |
|---|---|---|---|---|
| 80s Rock | MDB Drums | CC-BY-NC-SA 4.0 | Closest 80s full-band rock/vocal demo | B/D, S- |
| Beatles | MDB Drums | CC-BY-NC-SA 4.0 | Melodic full-band pop/vocals | B/D, S- |
| Bebop Jazz | MDB Drums | CC-BY-NC-SA 4.0 | Genuinely fast annotated pulse | B/D, S+ |
| Britpop | MDB Drums | CC-BY-NC-SA 4.0 | Full-band pop/vocals | B/D, S+ |
| Cool Jazz | MDB Drums | CC-BY-NC-SA 4.0 | Lighter drums / half-time ambiguity | B/D, S- |
| Country 1 | MDB Drums | CC-BY-NC-SA 4.0 | Acoustic/electric contrast | B/D, S+ |
| Disco | MDB Drums | CC-BY-NC-SA 4.0 | Dance groove/vocals, principal bleed test | B/D, S+ |
| Free Jazz | MDB Drums | CC-BY-NC-SA 4.0 | Non-square rhythm stress control | B/D, S- |
| Funk Jazz | MDB Drums | CC-BY-NC-SA 4.0 | Electric bass/piano/funk texture | B/D, S+ |
| Fusion Jazz | MDB Drums | CC-BY-NC-SA 4.0 | Closest instrumental fusion control | B/D, S+ |
| Gospel | MDB Drums | CC-BY-NC-SA 4.0 | Fast pulse/vocals, half-tempo failure | B/D, S- |
| Grunge | MDB Drums | CC-BY-NC-SA 4.0 | Dense distortion/vocals | B/D, S+ |
| Hendrix | MDB Drums | CC-BY-NC-SA 4.0 | Guitar-led rock/vocals | B/D, S- |
| Latin Jazz | MDB Drums | CC-BY-NC-SA 4.0 | Syncopation / extra bar detections | B/D, S- |
| Modal Jazz | MDB Drums | CC-BY-NC-SA 4.0 | Tonal ambiguity / extra downbeats | B/D, S+ |
| Punk | MDB Drums | CC-BY-NC-SA 4.0 | 150 BPM faster-rock control | B/D, S+ |
| Reggae | MDB Drums | CC-BY-NC-SA 4.0 | Offbeat emphasis/vocals | B/D, S+ |
| Rock | MDB Drums | CC-BY-NC-SA 4.0 | Short full-band/boundary stress | B/D, S- |
| Rockabilly | MDB Drums | CC-BY-NC-SA 4.0 | Rock/acoustic rhythmic contrast | B/D, S- |
| Shadows | MDB Drums | CC-BY-NC-SA 4.0 | Instrumental guitar band | B/D, S- |
| Speed Metal | MDB Drums | CC-BY-NC-SA 4.0 | Dense subdivisions / parity failure | B/D, S- |
| Swing Jazz | MDB Drums | CC-BY-NC-SA 4.0 | Swing rather than straight dance | B/D, S- |
| Zeppelin | MDB Drums | CC-BY-NC-SA 4.0 | Heavy rock / meter instability | B/D, S- |

## Final consolidated grid results

Before/after are the SAME 23 mixes. Macro per-track means; CML/AML use
mir_eval 0.8.2, standard 17.5% relative phase/period tolerances, no initial
five-second trim. Severe phase flag: beat F70 >= .9 and downbeat F70 < .5.
Its zero count does NOT mean partially wrong bar maps are correct.

| Metric | Before | After |
|---|---:|---:|
| Successful tracks | 23/23 | 23/23 |
| Tempo mean absolute percentage error | 5.354267% | 5.354267% |
| Tempo within 4% | 21/23 | 21/23 |
| Half / double / 3:2 / 2:3 errors | 2 / 0 / 0 / 0 | 2 / 0 / 0 / 0 |
| Beat F70 | .899166 | .941285 |
| Beat precision / recall | .917902 / .895842 | .960022 / .937961 |
| Beat CMLc / CMLt | .768589 / .815240 | .811388 / .858039 |
| Beat AMLc / AMLt | .871099 / .934185 | .871758 / .934844 |
| Downbeat F70 | .851565 | .895043 |
| Downbeat precision / recall | .822569 / .905033 | .866048 / .948511 |
| Downbeat CMLc / CMLt | .730171 / .782776 | .732728 / .785333 |
| Downbeat AMLc / AMLt | .772384 / .842009 | .774941 / .844566 |
| Marked reliable track time | .736613 | .731276 |
| Actually correct complete-bar time | .664177 | .703733 |
| False-reliable track time | .072436 | .043409 |
| False-reliable conditional on marked time | .167930 | .120311 |
| Reliable precision conditional on marked time | .832070 | .879689 |
| Severe beats-right/bar-phase-wrong flag | 0/23 | 0/23 |
| Key accuracy / relative / parallel / fifth | unmeasured | unmeasured |
| source.analyze wall-time total | 192.10 s | 275.75 s |

Before timing contains one cached first track; after uses `--fresh` for all 23.
Concurrent workloads differ: **not a controlled speed comparison**. The range
policy still misses genuinely fast Bebop Jazz/Gospel (about 222 -> 111 BPM).
All observed F70 and false-reliable improvements are the single Speed Metal
parity correction; the other 22 tracks' event scores are unchanged. The pulse
filter does not change this already-pulsed corpus, though it rejects generated
silence/sustained-tone intros. Its benefit on real drumless intros is unverified.
Phase-confidence capping changes no timings; it makes unverified phase explicit.

### Matched input comparisons

Use only the SAME **ten reconstruction-valid tracks** for the primary oracle
comparison; individual drum alignment in the other 13 is not established.

| Input | Beat F70 | Downbeat F70 | Tempo MAPE | False-reliable track time |
|---|---:|---:|---:|---:|
| Mix | .965086 | .941506 | 5.925926% | .192818% |
| Reference drums | .962717 | .902300 | 5.925926% | 8.883367% |
| 50:50 reference drums + mix | .963986 | .934578 | 5.925926% | .194620% |

All-23 `drums-final` and `fusion-final` reports are exploratory because of the
invalid sums. The actual estimated-htdemucs three-track/20-second paired
cohort above scores 1.0 beat/downbeat F70 in all modes. Neither experiment
justifies replacing mix-first. The harmonic-key comparison is also restricted
to these ten valid tracks: 8/10 agreement, not accuracy. Product key estimation
is unchanged; calibration and real-key correctness remain unmeasured.

## Final separation tables and recommendation

SDR = sample-aligned energy ratio, NOT filtered BSS Eval. SI-SDR is in raw JSON.
RSS includes model loading AND reference-scoring buffers: not app-only memory.
240 s repeats Disco's excerpt, not four minutes of unique performance.

| Material | Model | Audio s | Separation s | Peak MiB | Drums SDR | Bass SDR | Vocals SDR | Other SDR |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Disco | htdemucs | 20 | 40.69 | 1273 | 14.44 | 14.38 | 13.44 | 8.34 |
| Disco | htdemucs_ft | 20 | 113.29 | 1849 | 15.04 | 14.83 | 14.01 | 8.91 |
| Disco | mel-roformer+htdemucs | 20 | 450.57 | 2785 | 15.22 | 14.79 | 17.18 | 9.47 |
| Disco repeated | htdemucs | 240 | 402.63 | 2977 | 14.17 | 14.16 | 14.32 | 8.02 |
| Disco repeated | htdemucs_ft | 240 | 1490.48 | 3309 | 14.78 | 14.61 | 14.80 | 8.53 |
| Four-minute RoFormer | not run | - | unmeasured | unmeasured | - | - | - | - |

Wrong-source simultaneous projection **into other**, percent of estimated
other energy (correlated-source attribution proxy, not identifiable audible bleed):

| Audio | Model | Vocals | Bass | Drums |
|---|---|---:|---:|---:|
| Disco 20 s | htdemucs | .11295% | .02744% | .08574% |
| Disco 20 s | htdemucs_ft | .09251% | .02520% | .07608% |
| Disco 20 s | mel-roformer+htdemucs | .01307% | .02203% | .09794% |
| Disco 240 s | htdemucs | .09587% | .02463% | .08129% |
| Disco 240 s | htdemucs_ft | .09851% | .02249% | .07934% |

Keep htdemucs default. Fine-tuned is an explicit slower quality override
(~3.7x CPU for four minutes, roughly .5 dB better SDR), NOT a reliable vocal
bleed cure: the four-minute vocal projection slightly increases. RoFormer is
a provisional vocal-cleanup option, not a default: Disco 20 s gives ~8.6x
less vocal projection and +3.74 dB vocal SDR, but more drum projection and
~11x CPU. Do not extrapolate its four-minute time or peak RAM. Existing
`model` overrides already implement these tiers; no new config/schema alias.
Additional 20-second controls (same reference gate and metrics):

| Track | Model | CPU s | Peak MiB | Drums SDR | Bass SDR | Vocals SDR | Other SDR |
|---|---|---:|---:|---:|---:|---:|---:|
| Funk Jazz | htdemucs | 35.39 | 1152 | 13.00 | 17.05 | n/a | 16.66 |
| Funk Jazz | htdemucs_ft | 138.64 | 1682 | 13.32 | 16.48 | n/a | 16.11 |
| Funk Jazz | mel-roformer+htdemucs | 457.59 | 2630 | 13.00 | 17.05 | n/a | 16.65 |
| Britpop | htdemucs | 38.07 | 1117 | 9.81 | 9.12 | 10.16 | 8.65 |
| Britpop | htdemucs_ft | 150.01 | 1666 | 10.33 | 9.80 | 10.61 | 8.95 |

| Track/model | Vocal projection into other | Bass projection | Drum projection |
|---|---:|---:|---:|
| Funk Jazz / htdemucs | no vocal reference | .04374% | .03739% |
| Funk Jazz / htdemucs_ft | no vocal reference | .12547% | .04402% |
| Funk Jazz / RoFormer hybrid | no vocal reference | .04387% | .03736% |
| Britpop / htdemucs | .97777% | .39583% | .16736% |
| Britpop / htdemucs_ft | .83192% | .34713% | .14332% |

These controls strengthen the decision NOT to change the default: FT loses
.56 dB bass and .54 dB other SDR on instrumental Funk Jazz, with ~2.9x
more bass projection into other; RoFormer adds almost no value there.
Britpop RoFormer is the one remaining running control. Raw checkpoints:
`separation-controls-20s.{md,json}` in the external evaluation directory.

## Final chop results

1,216 reference-grid 8/16-beat loops. Spearman .201231 -> **.870479**;
top-three proxy .499486 -> **.726128**; all-loop mean .435608 unchanged.
This is a development-set, alternate-horizon seam/join proxy, NOT held-out
perceptual validation or phrase-start truth.

| Metadata genre | Spearman before | After |
|---|---:|---:|
| Jazz | .177546 | .844657 |
| Musical Theatre | .491835 | .874062 |
| Pop | .190888 | .903932 |
| Rock | .153782 | .873771 |
| Singer/Songwriter | .386395 | .922883 |

Real `source.find_chops` on inferred grids proposes **112** 8/16-beat loops:
**107/112** true downbeat starts, **105/112** both true downbeat endpoints,
**103/112** correct annotated beat count, **100/112** passing all three.
Remaining failures: Free Jazz, Gospel, Latin Jazz, Rock. Bebop Jazz and Modal
Jazz yield no default candidates. These are measured remaining risks, not a
claim that every proposal loops. Harmonic return cannot distinguish a true
phrase start from a cyclic rotation; no unlabelled phrase heuristic shipped.

## Owner workflow, validation and handoff gaps

One-line owner command, no annotations required (replace the songs folder):

```powershell
uv run --with mir-eval python -m muestra.ingest.evaluation "C:/path/to/your/songs" --report "$HOME/Documents/MuestraLibrary/_test-audio/eval/own-songs.md"
```

It runs real tools in separate audit projects, writes per-track/aggregate
Markdown and JSON after each track, reports confidence/alternatives/reliability
and click paths, and does NOT invent accuracy. JSON for new runs freezes
raw/effective grids. Default clicks cover the first 20 s; use full-file maps
and `source.preview_grid(window_s=[start,end])` for later musical windows.
`--fresh` reruns models; cache presence is explicit. Manifest, model comparison,
separation and composite-source commands are in docs/ingest.md. `--analysis-report
after.json` adds real proposal correctness to `chop_evaluation`.

Runtime: Python 3.12.16; numpy 2.5.5; scipy 1.18.1; torch 2.15.0;
beat-this 1.1.0 (final0 CPU, no DBN); mir_eval .8.2; demucs-infer 4.4.0;
audio-separator .48.0. No new product dependency or schema change.

Final whole suite: **849 passed, 10 skipped**, **962.29 s**. Ruff check passes;
format check: **249 files already formatted**. Ingest-only: **76 passed,
3 skipped**, 57.62 s. Opt-in real-track test: **1 passed** (three tracks),
78.28 s, two upstream Torch autocast warnings. A final diagnostic-text encoding
repair is additionally covered by **6 pulse tests passed**, 3.64 s, and repeat
ruff/format checks. No audio/model downloads are required by default tests.

The unannotated real-audio smoke run also passed: 80s Rock, zero annotation
metrics invented, five actual stereo 20-second click WAVs, grid alternatives
and reliability map, `ask_human=true`. Click headers and file existence were
checked; no listening was performed.

All source downloads are **1,340,076,252 bytes**. The evaluation directory,
including duplicate audit-project imports and derived previews, currently uses
about **3.1 GiB**, slightly over the intended approximate 3 GB budget. A cleanup
of reproducible oracle-fusion audit copies was refused by environment policy;
no source data was deleted. Final disk usage/clean-tree status follows below.

Needed outside ingest: optional typed tool/UI source selector for instrumental
and no-drums sources (CLI plus existing contracts already works), and agent/UI
handling that actually honours `grid.ask_human` before treating bar phase as
truth. No UI, render, song, API, project, ears, skill or schema files changed.

Unverified: target artists/full-length tape-era songs; genuine slow tempo and
real drumless intros/metre changes; general octave correction; section/phrase
boundaries; real key accuracy/calibration; four-minute RoFormer/app-only RAM;
held-out model generalisation; subjective separation/loop quality. Restricted
full MUSDB18/MedleyDB access was not requested (official links above). **No audio
was heard here; all conclusions are measurements or limited recommendations.**
