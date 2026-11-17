# WP1: ingest, analysis and loop discovery

Implemented on Windows 11 / CPython 3.12.16, CPU only, 2026-11-17.
The thin laptop was also running other work packages; these timings are
pessimistic and are not portable performance guarantees. No LLM calls,
subscriptions, paid APIs, cloud execution or commercial music downloads.

## Install and use

1. `uv sync --extra ingest` installs the optional WP1 stack. Plain `uv sync`
   installs the light core and fast tests; actual inference needs the extra.
2. Install ffmpeg (`winget install Gyan.FFmpeg` / `brew install ffmpeg`).
   Decoding checks `shutil.which("ffmpeg")`; `MUESTRA_FFMPEG` overrides it with
   a full executable path. Restart the terminal after installing. On this
   machine ffmpeg 9.0.2 lives in the winget Gyan.FFmpeg package's `bin/`.
3. Use the six typed tools in `muestra.ingest.tools`, or their registry names:
   `source.import`, `source.separate`, `source.analyze`, `source.find_chops`,
   `chops.choose`, `chops.list`. CLI/MCP routing belongs to WP7.

Recommended `muestra.toml` settings:

```toml
[separation]
backend = "local"
model = "htdemucs"
```

The scaffold's legacy backend `audio-separator` is accepted as a local
execution alias. `model` selects the algorithm: `htdemucs`, `htdemucs_ft`,
or `mel-roformer+htdemucs`. `modal` is a documented unimplemented backend
slot and returns an actionable `not_implemented` error; no remote request
is made. Set `MUESTRA_CPU_THREADS` to override the default two Torch threads.

`muestra.ingest.separation.separate(..., progress=callback)` and
`muestra.ingest.analysis.analyze(..., progress=callback)` accept
`callback(fraction: float, message: str)`. Separation progress is monotonic;
Demucs reports chunk progress. RoFormer currently reports stages, with no
per-chunk percentage from the wrapper. A server can adapt these callbacks;
callables are deliberately absent from the JSON tool parameter contracts.

## Imports and caches

WAV/FLAC/MP3/M4A originals are copied byte-for-byte to safe generated names
under `sources/`, never transcoded in place. Soundfile decodes first, with
ffmpeg as a fallback. Empty, nonfinite or multichannel audio is rejected.
SHA-256 detects duplicate content, including imports under different names.
Actual ffmpeg-backed codec smoke checks decoded one-second synthesized
WAV, FLAC, MP3 and M4A files successfully to finite 44.1 kHz mono audio.
Sources are round-trip registered in song.yaml when it exists. A durable
`sources/.muestra-sources.json` catalogue supports projects without a song;
it is source metadata, not a disposable cache.

Four stereo float WAV stems live at
`cache/stems/<source>/<cache-key>/{drums,bass,vocals,other}.wav`.
The key includes original content hash, model ID, implementation version
and processing settings. Model directories are immutable after successful
publication; switching models retains previous outputs. `active.json`
selects the paths used in analysis. A completion manifest is written last,
so interrupted operations do not become cache hits. Missing stems regenerate.
Analyses have a source/method/version sidecar; changed originals invalidate
the cache. Candidate sets have a fingerprint and reject stale choices after
source, stems or analysis changes. Originals must remain immutable for
reproducibility. Deliberate external edits to derived audio are not supported.

An exclusive project ingest lock prevents concurrent WP1 mutations. After
a crashed process, remove `.muestra-ingest.lock` only after verifying that
no ingest job is still running. It does not lock an editor or another WP.

## Package evaluation and licences

PyPI versions and actual uv installation/imports were checked on this machine:

| Package | Version | Outcome |
|---|---|---|
| audio-separator | 0.48.0, MIT | Installs; `[cpu]` supplies onnxruntime. Explicit audioread is also needed with current librosa. Actual Mel inference ran. |
| demucs | 4.1.0, MIT | Installs and loads on Windows, but Intel macOS NumPy<2 pins conflict with this repo's NumPy>=2.1 during universal locking. Not retained. |
| demucs-infer | 4.4.0, MIT | Retained. Uses official Demucs weights; installs/locks with the shared stack and runs CPU inference. |
| beat-this | 1.1.0, MIT | Retained; final0 model, CPU, minimal postprocessor (`dbn=False`). |
| pyloudnorm | 0.2.0, MIT | Retained for BS.1770 integrated loudness. |
| Essentia | latest 2.1b6.dev1438 | No Windows wheel. uv tried old dev234 source; build failed on removed `imp` and POSIX build commands. Not retained. |
| madmom | 0.16.1 | Build preparation failed: undeclared Cython dependency. Further legacy NumPy/Python compatibility work was not attempted. Not retained. |
| allin1 | 1.1.0 | Installs but import fails because madmom is unavailable. Not retained. |

Verified runtime additionally included Torch 2.15.0+cpu, torchaudio
2.11.1+cpu, NumPy 2.5.5, SciPy 1.18.1, soundfile 0.14.0, onnxruntime
1.32.0 and psutil 7.2.2. These are observed versions, not a committed lock;
the integrator owns uv.lock. All paths use pathlib and external programs
receive argument lists. No macOS machine was available for an actual run;
universal resolution succeeds, but macOS execution and Intel Mac wheel
availability are not verified.

Official models and their licences:

* **HTDemucs** (and selectable HTDemucs FT): Meta's MIT distribution.
  [Official licence](https://github.com/facebookresearch/demucs/blob/main/LICENSE).
  Default weights are `955717e8-8726e21a.th` from
  `https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/955717e8-8726e21a.th`.
  demucs-infer verifies SHA-256
  `8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4`.
* **KimberleyJSN Mel-Band RoFormer**: the author's
  [model card](https://huggingface.co/KimberleyJSN/melbandroformer) declares
  MIT. Download is from that author's `MelBandRoformer.ckpt`; configuration
  comes from the [author's repository](https://github.com/KimberleyJensen/Mel-Band-Roformer-Vocal-Model).
  The audio-separator wrapper verifies its bundled model checksums and
  substitutes its supported configuration where needed. This is a TWO-stem
  vocal/instrumental model. The explicitly named cascade separates the
  instrumental with HTDemucs, adding residual second-stage vocals to other
  before installing the RoFormer vocal stem. It never labels the whole
  instrumental as other while inventing drums/bass.
* **Beat This! final0**: [official MIT repository](https://github.com/CPJKU/beat_this)
  and official JKU-hosted checkpoint, downloaded by the package from
  `https://cloud.cp.jku.at/public.php/dav/files/7ik4RrBKTS273gp/final0.ckpt`.

No BS-RoFormer model was benchmarked. The licensed Mel model was the
RoFormer alternative evaluated. HTDemucs FT is selectable through the
same backend, but its larger model bag was not downloaded or benchmarked.

Downloaded model disk usage (not Python/Torch packages):

| Model | Bytes | Cache directory |
|---|---:|---|
| HTDemucs | 84,141,911 | `C:/Users/lauti/.cache/demucs-infer/` |
| Beat This final0 | 81,058,141 | `C:/Users/lauti/.cache/torch/hub/checkpoints/` |
| Mel RoFormer | 913,106,900 | `C:/Users/lauti/.cache/muestra/models/` |

Required current weights total **1,078,306,952 bytes (~1.00 GiB)**. Default
Demucs+Beat This is ~157.5 MiB. `MUESTRA_MODEL_DIR` changes the Mel cache;
Torch honours `TORCH_HOME` for Beat This. A separate ~84 MB Hugging Face
cache from the direct Demucs installation probe remains under
`C:/Users/lauti/.cache/huggingface/hub/models--adefossez--HTDemucs/`; the
chosen backend does not use it. Model files and test audio are outside git.

## Measurements and choice of default

Fresh worker process per benchmark; psutil sampled process-tree RSS at
100 ms intervals, including model load and Python runtime. Weights are
downloaded once; the four-minute measurement had weights already present.
RSS is a sampled peak, not an OS high-water counter. Times include model
load/decoding/writing but exclude the subsequent objective-proxy pass.

| Input | Model | Separation wall s | Peak RSS MiB | Result |
|---|---|---:|---:|---|
| Tiger Rag, first 30 s | HTDemucs | 48.83 | 1107.9 | Completed |
| Tiger Rag, first 4 s | HTDemucs | 14.19 | 991.4 | Completed; same excerpt as corrected Mel test |
| Tiger Rag repeated to 240 s, stereo | HTDemucs | 540.50 | 1952.1 | Completed in 9:00.5 |
| Livery Stable Blues, first 15 s, mono | HTDemucs | 29.49 | 1062.7 | Completed |
| The Syncopated Walk, first 15 s, stereo | HTDemucs | 33.79 | 1091.9 | Completed |
| A Friend of Mine, first 15 s, mono/vocal | HTDemucs | 37.16 | 1087.0 | Completed |
| Tiger Rag, first 30 s | Mel cascade | 869.26 | 2611.8 | Inference completed; initial wrapper failed mapping `other` to instrumental. No successful four-stem result. |
| Tiger Rag, first 4 s, corrected wrapper | Mel cascade | 105.35 | 2088.4 | Completed with four stems |

The 240-second file repeats the original 193.86-second CC0 recording; it
is a throughput workload, not a continuous four-minute band performance.
It meets the advisory ten-minute separation target with warm weights on
this loaded machine. A completely cold four-minute run including downloads
was not measured. No four-minute RoFormer run was attempted: the 30-second
measurement already exceeded ten minutes. No four-minute analysis+separation
combined SLA is claimed.

**Default: HTDemucs, shifts=0, overlap=0.25.** Random-shift ensembles are
disabled for deterministic, faster CPU iteration; Demucs documents their
potential SDR improvement as small. The RoFormer cascade remains selectable
for owner listening comparisons, but its CPU latency is unsuitable here.
There was no blind listening test; these measurements do not prove that
Demucs sounds better on Argentine rock or city pop.

On the directly comparable four-second excerpt, the Mel cascade takes
7.42 times the wall time and about 2.11 times the peak RSS of HTDemucs.

Objective proxies on the 30-second Tiger Rag HTDemucs output: reconstruction
residual **-31.10 dB** relative to the mix; energy ratios drums 0.000172,
bass 0.011374, vocals 0.000034, other 0.944271. This instrumental excerpt
has very little separated drum/vocal energy; it is a poor stand-in for
1980s rock. On the successful four-second Mel output, reconstruction was
**-36.23 dB**. These differently sized excerpts cannot rank model quality.
Pairwise stem correlation and energy leakage proxies need listening and,
ideally, reference multitracks; they are not SDR/SIR measurements.

Real 30-second end-to-end tool smoke run (saved outside repo): import
0.100 s, separation 64.456 s, repeated separation cache hit **0.046 s**,
analysis 6.720 s, scoring/rendering three previews **1.179 s**. Auto choice,
manual override with length adjustment and chops.list all succeeded.
The renderer remains WP2's stub in this checkout, so these previews used
the documented raw-excerpt fallback. A separate synthesized four-second
real-model test measured separation 16.672 s and cache hit **0.0275 s**.
Four-minute cache latency was not separately measured.

Final default suite: **124 passed, 2 skipped in 36.16 s**.
Slow run: **2 passed, 2 upstream deprecation warnings in
27.25 s**. Ruff check and Ruff format --check passed repository-wide
(80 files checked for formatting). Earlier all-tests timing
under peak concurrent load was 171.80 s, illustrating the machine contention.

## Analysis and beat evidence

The actual per-beat grid comes from Beat This, with no constant-BPM or
every-fourth-beat downbeat fallback. Beats must be finite, strictly
increasing and inside the file. Downbeats must match distinct beats within
50 ms and are snapped to that common grid. BPM is the median interval;
tempo_variable means the 90th-to-10th percentile interval spread exceeds
4% of its median. Interval outliers can still inflate that flag.

Because v0 only supports 4/4, analysis rejects an inferred grid when fewer
than 75% of bar intervals contain four beats. Chop discovery additionally
requires each traversed complete bar to have four beats and a detected
downbeat. These checks catch obvious meter/tracking failures, not a
consistent wrong bar phase or half/double tempo. A silence or unusable
grid gives an actionable error, never fabricated timestamps.

Independent synthesized ground truth: 64 drum beats drift gradually from
0.48 to 0.56 seconds per beat. With 70 ms one-to-one matching, the interior
56 beats yield precision **0.9298**, recall **0.9464**, F1 **0.9381**, median
matched absolute offset **5.16 ms**. Downbeat F1 over the complete fixture
is **0.8235**, with median matched offset **7.10 ms**. This is useful drift
evidence and also shows false/missed beats: neural downbeats are not perfect.

All four local files were re-verified against Archive.org item metadata,
and their MD5 values match the actual item files. Sources/licences are in
`C:/Users/lauti/Documents/MuestraLibrary/_test-audio/SOURCES.md` and
`WP1-LICENSE-VERIFICATION.json`. Tiger Rag is CC0; the three LoC transfers
carry Public Domain Mark. Full-recording model measurements:

| Recording | Duration s | Beat inference s | Beats/downbeats | Median BPM | Four-beat bar consistency | Nearest onset ≤70 ms |
|---|---:|---:|---:|---:|---:|---:|
| Tiger Rag (1928) | 193.86 | 54.86 | 786 / 197 | 250.00 | 98.98% | 93.51% |
| Livery Stable Blues (1917) | 171.49 | 41.83 | 394 / 162 | 166.67 | 30.43% | 93.91% |
| The Syncopated Walk (1915) | 176.40 | 46.93 | 413 / 108 | 176.47 | 72.90% | 92.98% |
| A Friend of Mine (1907) | 117.63 | 30.30 | 212 / 72 | 125.00 | 45.07% | 88.68% |

The last three fail the new 4/4 consistency gate. Measurements above expose
raw tracker diagnostics, not accepted production analyses. Onset agreement
is computed independently from RMS rises, but dense onsets can agree with
a wrong subdivision: **it is not beat/downbeat accuracy**. No human
annotations or auditory beat-accuracy assessment were available for these
recordings. That acceptance point remains unverified. Full beat JSON and
click-overlay WAVs are under `_test-audio/wp1-grid-evidence/`; downbeats
click at 1500 Hz and other beats at 1000 Hz for owner verification.

Key uses a downsampled STFT chroma profile, global tuning estimate and
Krumhansl-Schmuckler major/minor templates. Confidence combines correlation
and the runner-up margin; it is not a calibrated probability. Silence has
no key. Sections use spectral/chroma/energy novelty, snapped to downbeats
at least eight bars apart. Labels are intro, section_02, etc.; no learned
verse/chorus semantics are claimed. A short song may be one entire intro.
LUFS uses pyloudnorm's BS.1770 meter; silence/nonfinite loudness becomes
null, and clips shorter than 400 ms have no integrated loudness value.

## Chop ranking and persistence

Default search considers 4/8/16/32 beats (1/2/4/8 bars), or the exact
requested positive length. Starts use real downbeats, endpoints walk the
local beat grid, and every candidate fits completely inside one section.
No global-BPM timing extrapolation is used. Chops are limited to the
shared schema's 128-beat maximum.

The deterministic score is clipped to [0,1]:

```
0.35 * loop_seam + 0.25 * grid_stability + 0.15 * relative_energy
+ 0.15 * onset_density + 0.10 * tonal_content - 0.15 * possible_vocal_bleed
```

Loop seam compares magnitude-spectrum cosine similarity and RMS level in
80 ms contexts after the start/end. Stability measures interval jitter
after subtracting a linear tempo trend, so smooth drift is not mistaken
for irregular playing. Energy is relative to the full selected stem;
onset density counts RMS rises; tonal content is inverse spectral
flatness (a proxy, not harmonic recognition). Drums/vocals use density
instead of the tonal term. Other-stem vocal bleed is absolute correlation
against the available vocal stem. Silence is excluded. Scores include
numeric Spanish reasons; the auto pick gets a plain musical Spanish
justification. Returning fewer than n candidates is preferable to clones:
greedy diversity rejects overlap above 50% of the shorter loop.

Candidate sets persist under `cache/chops/<source>/csNNN.json`, with global
IDs `csNNN/candN`. IDs scan all sources and abandoned preview directories
to avoid reuse/collisions. Previews live in `cache/previews/<source>/<set>/`
and repeat the loop twice. WP2's documented `render_chop` seam receives
project tempo (or local tempo if there is no song). Only NotImplementedError
activates the fallback: a native-speed raw stereo excerpt with 5 ms edge
fades, repeated twice and exported at 24-bit PCM. The fallback is recorded
in score reasons; real renderer errors are not hidden.

find_chops records a recommended pick but does not implicitly edit the
arrangement. chops.choose writes the requested chop ID using the shared
round-trip helper, preserving comments and existing warp/fade settings.
Start and length adjustments are deltas along the real grid; invalid
bounds or a resulting song validation failure abort the edit. Manual
choices append chop_choice feedback **first** (WP13: before anything can
fail, so the agent always learns what the human picked); auto choices do
not. When the chosen length differs from the existing chop's, every clip
using it keeps its span: `repeat` is rescaled (8 × 4 beats → 4 × 8 beats,
reported in `repeat_changes`); when some clip's span is not a whole number
of new lengths, the choice is written as a NEW chop (`c1-2`, …), the old
chop and the arrangement stay untouched, and `agent_todo` says exactly
what to wire in. `message` is a Spanish sentence for the human (the UI
shows it); errors raised to the UI are Spanish too. Both modes update the
set's chosen record (`chosen.chop_id` = the id actually written).
chops.list reads the persisted sets.

## Owner-run benchmarks and verification

```powershell
uv run --extra ingest python -m muestra.ingest.benchmark C:/Music/record.wav --models htdemucs mel-roformer+htdemucs --report C:/Music/report.md
uv run --extra ingest python -m muestra.ingest.validation C:/Music/record.wav --output C:/Music/grid-check
$env:MUESTRA_RUN_SLOW='1'
uv run --extra ingest pytest tests/ingest/test_slow_models.py -q -s
```

Benchmark supports multiple input files, `--seconds N` for a fair same-excerpt
comparison and `--timeout SECONDS` per model (default 1800). Reports contain
wall time, sampled RAM, stem energy ratios, pairwise correlation and mix
reconstruction error. JSON beside Markdown preserves the numbers and any
failures; partial results survive later model failures. No audio is uploaded.
The validation script accepts independent `{beats_s, downbeats_s}` JSON via
`--annotations FILE` and computes one-to-one F1 at 70 ms. Without annotations
it writes diagnostics and audition clicks only.

## Integration flags and remaining limits

The user-authorized schema fix makes ChopCandidate.id match binding R5;
the example IDs and affected exported schemas were migrated. No new contract
shape was introduced. Ambiguities left for the integrator: next_set_id is
source-local despite global candidate IDs (WP1 compensates); Analysis.stems
is optional in the Python model despite the prose; ChopChoiceEvent has no
set_id field despite the prose (recoverable from candidate_id); start offsets
allow ±4096 in Python versus ±64 in prose. See decisions.md.

No actual 1980s Argentine record, Japanese city-pop/fusion recording, reference
multitrack or human beat annotation was available. Sound quality on those
sources and macOS execution are not verified. Old mono mixes, swing, 2/4 or
3/4 meter, noisy transfers, free-time intros, weak kicks, tape pitch drift,
modulation and vocal bleed remain difficult. The grid checks deliberately
reject uncertain material rather than manufacturing 4/4. Listen to the
loop point and bar phase before committing to an arrangement.

The renderer, UI and server integration remain other WPs' responsibilities.
No Modal implementation or learned semantic section model is included.
Choosing a chop updates three files sequentially (song, optional feedback,
set); each cache JSON replacement is atomic, but this is not a multi-file
crash transaction. An editor/another WP can still race song.yaml edits.
Cache-corruption checks do not authenticate externally modified stem samples.
