# Audio renderer (WP2)

Install with `uv sync --extra render`. The public seams are
`muestra.render.core.render_chop` and `render_song`; the registered tools
are `render` and `preview`. No model calls, sound generators, or substitute
instruments are part of this renderer.

## Timing and arrangement

The renderer uses float32 stereo audio at the project sample rate. Every
placement is independently rounded from its absolute project beat:
`round(beat * 60 / bpm * sample_rate)`. Repeats never accumulate rounded
clip lengths. At awkward tempos, adjacent slots can differ by one frame;
the last frame is truncated or zero-padded to the exact slot. MIDI ticks
are PPQ project beats; embedded tempo and meter messages are ignored.
Type 0/1 MIDI files and inline notes are supported; incomplete notes and
type 2/SMPTE timelines produce structured errors.

Chops use `chop_times()` and the source analysis grid, including fractional
and negative offsets. `mix` reads the original file named by the analysis;
other stem names read its corresponding stem. A stem shorter than the
requested interval is an error, never silently stretched or padded.

Patterns tile from the track's `at_bar`, respect `until_bar`, parse the
shared velocity grammar, and delay odd steps by `swing * step / 3`.
One-shots play at native speed and overlap naturally. Sample clips truncate
or pad to their declared occupancy; only indexed samples with BPM can
request a warp. Track windows gate audio on the absolute song timeline,
including sustained notes. They do not move notes or clip positions.

Instrument tracks call the WP3 host with the approved keyword-only
`bpm=song.meta.bpm`. Missing hosting fails with an instrument path, plugin
value, and installation hint. Muted instruments need no host audio.

## Warp quality

Verified installed stack: Pedalboard **0.9.26**, NumPy **2.5.5**, SciPy
**1.18.1**, SoundFile **0.14.0**, CPython **3.12.16** on Windows 11.

- Sample-rate conversion uses polyphase sinc resampling with a Kaiser
  window (`beta=10`). Mono is duplicated to stereo; multichannel audio
  requires an explicit downmix. SoundFile decodes first, with ffmpeg as
  the fallback for codecs such as AAC.
- Vinyl mode uses one band-limited Fourier resampling ratio across the
  entire cut. Tempo changes pitch; an extra semitone shift preserves
  length. Raised-cosine input/output fades suppress circular-cut ringing.
- Stretch uses `pedalboard.time_stretch(high_quality=True)`. Inspection
  of the matching upstream `pedalboard/TimeStretch.h` confirms this selects
  Rubber Band **R3 / OptionEngineFiner**, offline processing, highest-quality
  pitch mode, stereo channels together, and no worker threads.
- Additional settings: `transient_mode="crisp"`,
  `transient_detector="compound"`, `retain_phase_continuity=True`,
  `use_long_fft_window=None`, `use_time_domain_smoothing=False`,
  `preserve_formants=True`. Automatic FFT size preserves transient clarity;
  forcing a long window or smoothing trades away attack definition.
- Each source beat interval is mapped to its exact destination interval.
  R3 receives up to 80 ms of surrounding source context, and adjacent
  intervals crossfade over 10 ms (5 ms either side). Pitch shift is combined
  with the interval stretch in the same high-quality pass, avoiding a second
  spectral transformation. Outer chop fades use the declared `fade_ms`.

The R2/fast engine was rejected on quality grounds. Rubber Band's
sample-varying ratio API changes no more frequently than 1024 samples and
selects realtime processing, so it was rejected for precise offline beat
mapping. Independent intervals can still colour sustained material at
tempo discontinuities; the context/crossfade reduces seams, but listening
is required to judge musical suitability.

## Effects, automation, and sidechain

Automation is right-continuous at duplicate positions, holds endpoint
values outside the point range, and replaces the static value. Faders,
pan, sidechain depth/release, and compressor curves evaluate per sample.
Other numeric parameters update every **32 frames**: 0.67 ms at 48 kHz,
1.45 ms at the fixture's 22.05 kHz. This keeps sweeps smooth without
recreating processors. `reset=True` is sent only for the first block;
subsequent calls preserve delay, reverb, chorus, phaser, and plugin state.
Explicit `step` curves intentionally permit abrupt changes.

Pedalboard supplies gain, reverb, delay, chorus, phaser, saturation
(`Distortion`), and brick-wall limiting. Plugin inserts call
`plugins.core.build_plugin_fx`; native parameter automation uses their
writable attributes. Plugin adapters must retain state across processing
calls and handle their declared latency; unknown third-party latency and
nondeterministic plugin behaviour have not been certified here.

Low/high-pass filters are true order 2/4/6/8 Butterworth SOS filters;
Pedalboard's named low/high-pass filters are only first order. Coefficients
update with state retained. The custom stereo-linked peak compressor
implements the contracted soft knee and makeup gain, which Pedalboard's
native compressor does not expose. Its attack/release direction is chosen
per 32-frame block; gain/detector evolution remains sample-continuous.
The validated SciPy C filtering kernels are called directly to avoid
repeating public wrapper shape/array-API checks tens of thousands of times.
These are private SciPy entry points, covered by response comparisons in
tests; an incompatible future SciPy release will require adaptation.

Event sidechain uses all key-track hits/notes, or only the requested
`key_lane`. It ramps the envelope toward one at a trigger and exponentially
releases, multiplying audio by `1 - depth * envelope`. Overlapping triggers
restart from the current envelope. Clip-track keys use a linked, absolute
peak follower clamped to 0..1. Muted keys produce no event ducking. Key
tracks are processed before dependents; song validation rejects cycles.

The pan law is equal power: left=`cos((pan+1)*pi/4)`, right=`sin(...)`.
Centre attenuates each stereo channel by 3 dB; there is no channel crossfeed.
Lane and track pan/faders are separate stages. This choice was unspecified
in the contract and is explicit here for mix-level predictability.

## Mastering and files

Track inserts precede fader/pan. Muted stems are silent. The pre-master mix
is the sum of track stems. Master inserts run in listed order, followed by
master gain, BS.1770 loudness gain toward `target_lufs` (pyloudnorm), then
a true-peak safety limiter. A limiter may prevent reaching a requested
LUFS value. Silence stays silent; previews shorter than the meter's 400 ms
block are zero-padded for measurement only.

Pedalboard's `Limiter` was rejected: it automatically adds makeup gain and
clips at zero rather than enforcing the requested ceiling. `BrickwallLimiter`
uses 5 ms lookahead and a 4x oversampled detector. Streaming latency is
measured by impulse, including detector latency: **300 samples at 48 kHz**.
Automated limiter processing flushes that many frames and removes them;
a single offline call uses Pedalboard's own latency compensation. Tests
check first and last impulses at 22.05/44.1/48 kHz in both paths.

Final safety uses the lowest explicitly listed static master limiter
ceiling, or -1 dBFS by default. A post-limit 4x reconstruction check applies
a small global attenuation if needed, reserving 2e-6 for PCM24 quantization.
This measures conventional 4x true peak, not an infinite-resolution analog
reconstruction guarantee. There is no destructive final hard clip.

`mix.wav` is stereo PCM24; track and pre-track-insert lane stems are stereo
FLOAT WAV. Output ignores `export_bit_depth` because R7 explicitly requires
PCM24 mix/FLOAT stems. `stems=False` omits track/lane metadata references.
Ranges are `[start, end)`, end exclusive. Processing
and loudness measurement run from song start over the whole song before
cropping, so a range matches the full render including effects history and
normalization. All outputs end with the required 5 ms raised-cosine fade.
This uses memory proportional to song length and is intended for loops;
very long arrangements are not yet streamed.

`render` validates before allocating/recording a version, calls WP5's
version seams, writes audio and the byte-preserved snapshot/meta, then
calls ears and writes metrics. An ears stub returns audio plus
`RenderResult.hint`, with no stale metrics. Optional history/diff stubs also
produce hints; normal version recording is required. When existing metadata
is present, re-renders preserve its request/agent. If it has been deleted,
the current history seam exposes no request/agent trailers: callers can
provide them again; otherwise the agent defaults to `muestra`.

Preview targets are `mix`, `chop:<id>`, `track:<id>`, and
`clip:<track>:<0-based index>`. They write only `cache/previews/` and create
no version. Track/clip previews include their sidechain dependencies but
exclude unrelated instruments. Track previews start at the track window;
clip previews cover its placements/repeats. `bars` limits duration. Target
IDs come from song.yaml; before a song exists, WP1 calls `render_chop`
directly with a candidate definition and its source-local BPM.

## Cache and determinism

Disposable `.npy` caches live in `cache/render/{decoded,chops}/`. Keys hash
source **bytes**, the full analysis/chop, project rate/tempo, renderer ABI,
and audio dependency versions. File size/mtime are never trusted as content
identity. Atomic temporary-file replacement prevents half-written entries;
bad entries are regenerated. Decoded audio/chops are reused inside each
arrangement; cache invalidation tests modify audio while retaining both
size and mtime. Removing caches and recomputing R3 gives identical arrays
on this installed stack.

There is no random DSP in the renderer. Test noise uses local seeded RNGs;
the product never changes global RNG state. Any future stochastic renderer
feature must derive from `meta.seed`. External instruments/plugins must
provide their own deterministic implementation; no seed parameter exists
in the current hosting contract. Cross-platform byte identity is not
claimed. The fixture regression uses numeric tolerances for that reason.

## Measurements (2026-11-17)

Machine: **AMD Ryzen 5 7530U**, Windows 11 build 26200. Run
`uv run python -m tests.render.benchmark`. The case is 16 bars / 32 seconds,
120 BPM / 48 kHz, pitch-shifted chop, three drum lanes, fixture sample-bass,
automated low-pass, event sidechain, compressor/limiter master, LUFS target,
PCM24 mix, track and lane stems. No hosted instrument fake is used in this
benchmark. Final measured renders took **10.650, 7.593, 6.477 seconds**;
first includes decoding/warping and later runs use disk cache. All are below
15 seconds, including WAV writes. Their WAV SHA256 was identical
(`a5f27d0bc3af509400d72299eabb8b02c02d985e64b7eda0a0a129a93b90d0fe`).
Output measured **-10.000002 LUFS**, RMS **0.289848**, 4x true peak
**0.807338**, exactly **1,536,000 frames**. An earlier optimization run
measured 3.37–3.62 seconds; this shared laptop's load varies substantially.
The reported final figures retain that variation rather than selecting
only the fastest run.

An additional sanity render read the owner's TR-8 kick and TR-909 clap
directly from the external library: **6.439 seconds**, 1,536,000 frames,
**-10.000002 LUFS**, **0.841473** 4x true peak. Source samples were neither
copied into the repository nor committed. Output lived only in a disposable
temporary directory. Hosted-instrument performance remains unverified.

Measurements verified: exact frame counts/placements; no accumulated repeat
drift; bounded chop-edge jumps relative to local level; sidechain dips and
recovery; kick-only key selection; filter-response/order and sweep band
energy; retained effect state; pre-master stem sums; true peak; range/full
equivalence; version/ears ordering; and determinism. For an alternating
0.3–0.7-second source beat grid, detected transient offsets after stretch
were **0.54–1.73 ms**. R3 transient locations are approximate even though
the interval buffers are placed sample-exactly.

The shared fixture analysis describes 5.7 seconds, while committed stems
are 1.2-second placeholders. Tests/benchmark extend only disposable copies
with repeated fixture audio; the originals are never modified. Instrument
unit/integration tests use a fixture-audio host fake because WP3 is still
a stub. WP5 version operations and WP6 analysis are faked where required;
their real integration, real VST3 instrument quality, macOS execution, and
listening quality have **not** been verified. The renderer cannot hear.

Final validation: `uv run pytest -q`: **164 passed in 78.60 s** (69 renderer
tests); `uv run ruff check`: **0 findings**; `uv run ruff format --check`:
**74 files already formatted**. The example-song golden tolerance regression
uses the extended fixture stems and fixture-audio instrument fake, while
independent R3 computations and repeated PCM24 exports are compared exactly.
