# Muestra instruments (WP3, Windows measurements 2026-11-17)

The primary sounds are Surge XT factory instruments and recordings of real
electric bass and Rhodes. No audition or listening judgment was possible in
this implementation. Selection used the instruments' provenance, factory
categories, spectra, tuning, headroom, release behavior and repeatability.
Human listening and a mix-level A/B remain essential.

## What is on this machine

Surge XT **1.3.4** was already installed when WP3 started; nothing was installed
or upgraded with winget. `winget list --name Surge` does not recognize this
installation. These files were found and actually rendered:

```text
C:/Users/lauti/AppData/Local/Programs/Common/VST3/Surge Synth Team/
  Surge XT.vst3/Contents/x86_64-win/Surge XT.vst3
  Surge XT Effects.vst3/Contents/x86_64-win/Surge XT Effects.vst3
C:/ProgramData/Surge XT/patches_factory/
```

The bundle directory itself fails Pedalboard's Windows scan; resolution selects
the binary inside `Contents/x86_64-win`. This workaround is Windows-only: macOS
uses its native bundle. Surge is GPL-3.0 software:
<https://surge-synthesizer.github.io/>. No GUI, audio driver, audio stream or
hardware device is opened by the host.

WP3 installed these sample libraries **outside the repository**:

| Directory under `C:/Users/lauti/Documents/MuestraLibrary/instruments/` | Source and revision | Licence and commercial music use |
|---|---|---|
| `swagbass/` (~166.2 MiB) | [Karoryfer Swagbass](https://github.com/sfzinstruments/karoryfer.swagbass), `9d10fcae71af1975988ddecd5af1c95d372c7355` | CC0 1.0; commercial music and redistribution allowed |
| `jrhodes/` (~18.4 MiB) | [Jeffrey Learman jRhodes3c](https://github.com/sfzinstruments/jlearman.jRhodes3c), `ba6000f633d0a1d405049d877851ff7cc43f108b` | Explicit permission to use as an instrument in artistic works, including commercial music. Sample redistribution has separate BY-NC-SA terms; do not bundle these samples in Muestra |

Each directory contains `LICENSE.txt` with source, revision, commercial-use
explanation and the complete upstream licence. The original `LICENSE` is also
preserved. jRhodes is locally installed personal instrument data, not a freely
redistributable commercial application asset. No third-party audio or patches
were committed. `uv sync` installed the existing project dependencies into this
worktree's `.venv`; no additional Python dependency is needed for WP3.

Reproduce the sample setup explicitly (rendering never downloads anything):

```powershell
uv run python -m muestra.plugins.install_samples
```

The installer downloads pinned upstream revisions, then creates two small maps:
`swagbass/muestra-finger.sfz` and `jrhodes/muestra-rhodes.sfz`. The first keeps
the recorded note zones, three velocity layers and four round robins, removes
release noises and unsupported controller/velocity-curve instructions, and adds
6 dB of headroom. The second preserves the mono samples, up to five layers,
envelopes and original FLAC-embedded RIFF `smpl` loop points, made explicit in
the map; it also adds 6 dB of headroom. Upstream maps remain untouched.

Undo WP3's installation by removing **only** the `swagbass/` and `jrhodes/`
directories above. The worktree's `.venv` is also disposable. Surge predates
WP3 and should be left installed. No other machine configuration was changed.

## Ready-to-use aliases

Aliases occupy `instrument.plugin` and need no `preset` field:

```yaml
- id: bass
  instrument: {plugin: finger-bass}
  notes:
    - {beat: 1, pitch: A2, length_beats: 0.65, velocity: 100}
    - {beat: 2, pitch: E3, length_beats: 0.4, velocity: 90}
```

| Alias | Actual sound | Reason |
|---|---|---|
| `finger-bass` | Karoryfer Swagbass sampled electric bass | Real flatwound Ibanez bass recorded direct, three dynamic layers, four takes; a restrained foundation for sampled loops |
| `slap-bass` | Surge `Basses/FM Slap` | Factory velocity-sensitive FM slap; the sampled bass above supplies the real electric-bass option |
| `synth-bass` | Surge `Basses/Square Bass` | Filtered square bass with a strong, stable fundamental and punchy envelope |
| `rhodes` | jRhodes3c mono, 1977 Rhodes Mark I Stage 73 | Real harp recordings, bell tone and harder-velocity bark; substantially more convincing than a basic synthesized EP |
| `brass-stab` | Surge `Brass/OB-8 Jump` | Oberheim-style unison brass; useful for short seventh/ninth chord stabs |
| `pad` | Surge `Pads/MKS-70 Warm Pad` | Roland-style warm sustained texture behind loops |

The Surge aliases use in-memory patch variants: oscillator retrigger enabled,
drift zeroed, LFO trigger mode set to key trigger. FM Slap's scene octave is 0;
Square Bass and OB-8 Jump use scene octave +1, correcting the measured factory
octave offset so A2 plays around 110 Hz. Their other oscillator intervals,
factory filters, envelopes and effects are retained. Native `global_volume` is
−18 dB for synth-bass and −9 dB for the other Surge aliases. Explicit
`instrument.params` override these defaults. Factory files are never modified.
Direct factory references retain the original sound and original tuning:

```yaml
instrument:
  plugin: surge-xt
  preset: Basses/FM Slap     # category/name, unique basename, or full .fxp path
  params: {global_volume: -12}
```

`finger-bass`'s original map covers MIDI 9–72; extreme low transpositions are
technically mapped but are not recommended. Normal electric-bass lines should
use approximately E1–G3. SFZ round-robin cycles start at a position selected by
`song.meta.seed`. A local `[plugins]` alias with the same name overrides a
curated alias, allowing a project to pin its own installed SFZ or plugin.

### Level-matched aliases (WP13)

"Try another bass" used to change the level by up to 15 LU for the same
notes. Every curated alias now gets a constant output trim
(`plugins.presets.CURATED_TRIM_DB`, applied after rendering, only with its
default patch) so it measures `ALIAS_REFERENCE_LUFS` = **−24 LUFS** on its
reference phrase (48 kHz, 120 BPM, velocity 100; basses: A1 C2 D2 E2 quarter
notes ×16; rhodes/brass/pad: C4-E4-G4 half-note chords ×8):

| alias | before (LUFS / RMS dBFS) | trim | after (LUFS / RMS / peak) |
|---|---|---|---|
| finger-bass | −34.3 / −36.6 | +10.3 dB | −24.0 / −26.3 / −7.5 |
| slap-bass | −19.2 / −20.6 | −4.8 dB | −24.0 / −25.4 / −14.3 |
| synth-bass | −31.7 / −32.2 | +7.7 dB | −24.0 / −24.5 / −1.3 |
| rhodes | −15.2 / −17.6 | −8.8 dB | −24.0 / −26.4 / −13.9 |
| brass-stab | −21.8 / −25.1 | −2.2 dB | −24.0 / −27.3 / −9.0 |
| pad | −23.5 / −25.6 | −0.5 dB | −24.0 / −26.1 / −11.9 |

The trim is one float multiply, so renders stay bit-exact across runs
(re-checked: two renders of each alias identical). Songs written before this
change hear their curated instruments at the new level (re-check `gain_db`).
`tests/plugins/test_alias_levels.py` re-measures every alias (±1 LU; skipped
where Surge XT / the SFZ samples are absent).

## Adding plugins, samples and effects

Aliases and search directories use the existing `muestra.toml` contract:

```toml
[plugins]
search_dirs = ["C:/MyVST3", "../instruments"]
surge-xt = {windows = "C:/Users/lauti/AppData/Local/Programs/Common/VST3/Surge Synth Team/Surge XT.vst3", macos = "/Library/Audio/Plug-Ins/VST3/Surge XT.vst3"}
my-bass = {windows = "C:/Music/Bass/bass.sfz", macos = "/Users/me/Music/Bass/bass.sfz"}
```

The equivalent `[plugins.aliases.<name>]` tables are supported by the existing
config model. Relative paths resolve against the project. An explicitly
configured but broken alias fails with a hint; it never silently substitutes
a different plugin. Ambiguous installed names/preset names fail similarly.
`instrument: {plugin: sfz, preset: library://instruments/my-bass.sfz}` also works
with the project's configured library roots.

Windows defaults include system Common Files/VST3, per-user
`AppData/Local/Programs/Common/VST3`, and `AppData/Local/VST3`.
`plugins.list` lists installed binaries, Surge factory patches, SFZ files in
instrument/library roots, standard VST3 preset directories and curated alias
availability. SFZ presets report subset compatibility; original full upstream
maps can legitimately report unsupported opcodes. Listing never loads native
code. `plugins.scan` loads each requested plugin in a subprocess with a deadline
(120 seconds by default), reports native parameter names, kind and latency, and
stores `cache/plugins/scan.json`. Request selected parameter names for native
range details, e.g. `parameters=["global_volume"]`; inspecting every parameter
would be unnecessarily expensive. Scan failures are structured records for each
candidate, so one broken plugin cannot terminate the inventory.

Generic effects use the existing `type: plugin` shape:

```yaml
fx:
  - id: delay
    type: plugin
    plugin: Surge XT Effects
    params: {output_mix: 0.3}
```

`build_plugin_fx` returns a persistent worker proxy with Pedalboard-compatible
`process(audio, sample_rate, buffer_size=8192, reset=False)`. Call
`set_parameters({...})` or assign native parameter attributes between blocks;
state/tails persist with `reset=False`. The renderer owns automation evaluation
and block boundaries. Call `close()` or use the proxy as a context manager.
This is a process-compatible object, not a C++ `Plugin` that can be placed
directly inside `pedalboard.Pedalboard([...])`.

Parameters use native units, not normalized 0–1 values. Numeric 0/1 works for
boolean switches. Label-only categorical controls that cannot accept a numeric
native value produce a structured parameter error; the current song schema
does not accept string plugin params. Generic presets are `.vstpreset` files;
only Surge's disk factory bank additionally supports `.fxp` and category names.

Both instrument rendering and FX processing are isolated, including loading,
preset application and parameter setting. Worker crashes and hangs become
`plugin_crashed` / `plugin_timeout` issues. Missing plugins/presets,
incompatible presets, bad parameters, wrong plugin kinds and malformed audio
also carry a stable code, path and fix hint. A probe is never a promise that
later processing is safe, so actual processing remains isolated too.

## SFZ fallback choice and supported subset

sfizz is the better full SFZ interpreter: it supports substantially more
controller, modulation, articulation and filter behavior. Its official
[1.2.3 release](https://github.com/sfztools/sfizz-ui/releases/tag/1.2.3) offers
Windows plugin installers and a macOS package. A VST3 deployment would still
need native-plugin discovery, state loading and process isolation; the official
release assets do not supply a standalone Windows render executable. Building
and shipping a native CLI or binding would add another platform-specific
installation dependency. sfizz was evaluated for integration/packaging but was
**not installed or audio-benchmarked** here.

For the required fallback with no VST, WP3 chooses a strict NumPy/SciPy SFZ
subset with recorded samples. Its constraints are small and testable, and the
chosen instruments do not need SFZ filters to reproduce their main timbre.
Unsupported opcodes are errors, never silently ignored. Use sfizz later when
an instrument's expressiveness depends on richer SFZ behavior.

Supported sections: `<control>` (`default_path`), `<global>`, `<group>`,
`<region>`. Supported opcodes:

```text
sample key lokey hikey lovel hivel pitch_keycenter pitch_keytrack
tune transpose volume pan amp_veltrack
ampeg_attack ampeg_hold ampeg_decay ampeg_sustain ampeg_release
loop_mode loop_start loop_end offset end seq_length seq_position lorand hirand
```

Paths with spaces and Windows SFZ separators are handled. Audio can be mono or
stereo WAV/FLAC supported by libsndfile. Note names use C4=60; numeric SFZ keys
avoid octave-naming conventions in older sample packs. Sustain is a percentage.
Loop end and sample end are inclusive. Modes: no_loop, one_shot (ignores note
off), loop_continuous, loop_sustain. Loops must have explicit points; the setup
script extracts the Rhodes' original embedded points. Pitch conversion uses
polyphase sinc resampling with a 64-taps-per-rate Kaiser-12 FIR and rational
rates bounded to denominator 4096. Tests check alias rejection, zones, envelopes, loops,
seeded round robin, stereo, tuning and release. A 1 ms attack is the default
when unspecified to suppress onset clicks; an explicit zero attack is honored.

Not supported: preprocessor/includes/macros, filters, keyswitches, CC envelopes,
release-trigger samples, pedal behavior, MPE, streaming huge libraries or SFZ
effects. These are errors when explicitly requested by a map. Recordings may
contain natural noise/tuning variation. There is no automatic normalization or
limiter inside the instrument; mixing/headroom remains the renderer's job.

## Timing, repeatability and measured quality

The seam is `render_instrument(project, instrument, notes, sample_rate,
n_frames, *, bpm)`. It returns finite float32 stereo `(n_frames, 2)` exactly.
Beat 1 is frame zero. Notes are frame-rounded at the explicitly passed BPM;
note-offs sort before note-ons at equal timestamps. The renderer converts MIDI
ticks/PPQ to these project beats and ignores MIDI tempo/meter events. WP3 does
not decode MIDI files, change track windows, or add the final 5 ms song fade.

The host renders the entire requested duration, including release tails, and
cuts only at `n_frames`. Pedalboard already compensates reported plugin latency:
do not trim it again. Surge and its Effects plugin report zero latency here;
an actual nonzero-latency third-party plugin has not been tested on this machine.

Measurements use two notes (A2/A4, velocity 100, one-second gates, 48 kHz,
20-second output), FFT analysis after attack, and comparisons in the same native
instance and two independent worker processes. Reproduce with:

```powershell
uv run python tests/plugins/measure.py
uv run pytest tests/plugins -m real_plugin
uv run pytest -m "not real_plugin"
```

The measurement script writes JSON to the OS temp directory. Frequency
resolution is approximately 0.183 Hz. EP/bass can have stronger upper harmonics
than fundamentals; the report includes both the expected fundamental band and
the strongest spectral peak. Brass deliberately has a spread of unison voices,
so its strongest peak need not lie exactly at the central tuning.

| Alias | A2 estimate (Hz) | A4 estimate (Hz) | Peak (dBFS) | Same native instance repeat error, RMS dBFS |
|---|---:|---:|---:|---:|
| Init Sine diagnostic | 110.046 | 440.002 | −18.07 | −221.4 |
| slap-bass | 110.046 | 440.002 | −15.95 | −183.1 |
| synth-bass | 110.046 | 440.002 | −9.91 | below −240 |
| brass-stab | 110.229 | 434.509 | −12.96 | −190.2 |
| pad | 110.046 | 439.453 | −11.92 | −295.0 |
| finger-bass | 110.229 | 440.918 | −18.39 | bit-exact |
| rhodes | 110.229 | 440.735 | −13.74 | bit-exact |

The brass peak at A4 is about −21.7 cents due to deliberate unison detuning;
it is not a sample-resampling error. Others are within 4 cents of 110/440 Hz.
No clipping occurred. The Cmaj7 chord test peaks were −5.41 dBFS for brass and
−13.41 dBFS for pad. The last second of the 20-second test is below −100 dBFS
for every alias; SFZ tails end at exact zero. No signal precedes the scheduled
first note. Detailed low/mid/high spectral energy fractions are in the report.

**Across fresh native workers every selected Surge patch was bit-exact.**
Public `render_instrument` creates a fresh worker each call and has a repeat
test that is bit-exact in the same calling process. Both sample instruments
also pass separate-process bit-exact tests. No seed/state is read from a GUI.

The aborted attempt's Init Sine phase explanation was not reproduced. That
factory patch already retriggers its sine. Reusing an instance produced a
residual around −221 dBFS RMS, not an audible phase change. In Surge **1.3.4**,
[`SurgeSynthProcessor::reset()`](https://github.com/surge-synthesizer/surge/blob/release_xt_1.3.4/src/surge-xt/SurgeSynthProcessor.cpp)
only sets `blockPos = 0`; it does not reconstruct DSP/envelope/filter state.
Fresh-process equality and this incomplete reset explain the residual. The
exact internal arithmetic producing each subnormal difference was not traced.
Fonk was rejected: even after retrigger/drift changes its same-instance error
was around −34 dBFS RMS, with audible-scale variation. Selected aliases avoid
it. Arbitrary raw presets/plugins are **not** guaranteed deterministic.

FXP loading preserves binary wavetable content and wraps Surge's `sub3`
component chunk in a Steinberg VST3 preset container. A silent callback applies
queued patch state before params are set. Otherwise the first audio callback
overwrites parameter overrides. A lazy parameter adapter avoids eagerly
measuring all 2,855 Surge parameter ranges. It uses a small internal Pedalboard
Python API, only in the child process; tests cover it against **0.9.26**.

16 bars, 120 BPM, 48 kHz, 64 bass notes, 1,536,000 stereo frames:

- Surge synth-bass: **4.09 s end to end**, including fresh process/plugin startup.
- Recorded finger-bass: **0.61 s end to end**, including map/sample reads.
- A warm native instance took about **0.50 s** for DSP and transfer alone.

These are wall times on the busy Windows laptop, not a hard performance limit.
Separate measurement runs varied with other agents' CPU activity.

Final verification on this Windows worktree: `uv run pytest` **133 passed**
(63.07 s), including 11 native-plugin tests and two installed-sample tests.
`uv run pytest -m "not real_plugin"`: **122 passed, 11 deselected** (46.11 s).
Ruff lint passes; `uv run ruff format --check` reports **81 files already
formatted**. The external-data-independent subset excludes both real_plugin
and real_samples: **120 passed, 13 deselected** (37.83 s). Missing installations
also skip those optional tests with
explicit setup instructions during an ordinary default test run.

## macOS and integration notes

The resolver searches `/Library/Audio/Plug-Ins/VST3` and
`~/Library/Audio/Plug-Ins/VST3`, plus system/user `Components` for optional AU.
Install a native macOS Surge build; Windows VST3 binaries cannot be copied over.
Surge data lookup uses `/Library/Application Support/Surge XT` and the user
equivalent. Samples default to `~/Documents/MuestraLibrary/instruments` or can
be relocated with project aliases/library roots. The code uses pathlib and
subprocesses with no Windows-only audio APIs. **macOS rendering and AU have not
been verified on hardware.** Cross-OS or cross-version bit identity is not
claimed.

The orchestrator explicitly approved the keyword-only BPM contract change;
it is recorded in contracts.md and decisions.md. Seed has no seam argument:
the SFZ path reads `project/song.yaml` for `meta.seed`. A renderer processing a
historical snapshot must ensure that project's song/seed matches the snapshot.
No signature was expanded beyond the approved BPM change.

WP3 exposes `TOOLS` in `muestra.plugins.tools`. The base registry's fixed
`_TOOL_PACKAGES` omitted `plugins` at this branch's starting commit. Ownership
rules prohibit WP3 editing `registry.py` or shared generated schemas. **The
integrator must add `plugins` to that tuple and regenerate the four new tool
schemas** before these tools appear in generated MCP/CLI/API interfaces. Their
Python functions and typed models work directly and are tested here.

## Later paid-versus-free listening evaluation

Nothing paid, trial, cracked or subscription-based was obtained. These are
planning prices, not verified current offers; confirm on the linked vendor
pages before preparing a later owner-approved evaluation:

| Candidate | Budget price (USD, before tax) | Hypothesis to test against these free sounds |
|---|---:|---|
| [IK MODO BASS 2](https://www.ikmultimedia.com/products/modobass2/) | about $300 | More consistent articulated slap/finger techniques, slides and controllable string/pickup character |
| [Arturia Stage-73 V](https://www.arturia.com/products/software-instruments/stage-73-v/overview) | about $149 | More expressive Rhodes response, modeled pickup/tine behavior and amp/effect variations |
| [NI Session Horns Pro](https://www.native-instruments.com/en/products/komplete/cinematic/session-horns-pro/) | about $299 | Real horn-section articulations and voicing, rather than a synth-brass character |

None was installed, heard, compared or verified with Pedalboard. A later WP10
test should match loudness and MIDI, hide labels, compare in the actual mix,
verify headless compatibility and commercial-output terms, then let the owner
decide whether the extra expression is worth paying for.
