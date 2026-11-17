# The sample library (WP4)

Muestra's agent cannot hear. The library index is how it chooses a kick: every
sample is **measured** once, described in plain musical terms, and searchable
in English or Spanish. Code: `src/muestra/library/`. Contracts:
`docs/contracts.md` (*Library index*) and `schemas/library-index.schema.json`.
Why things are the way they are: `docs/decisions.md` (2026-11-17, WP4).

Tools (MCP names use `_`, CLI is `muestra library <action>`):

| tool | what it does |
|---|---|
| `library.index` | index a library root (incremental, parallel); adds a new root to `muestra.toml` |
| `library.search` | ranked search with reasons; compact by default |
| `library.kits` | curated starter drum kits; with `name`, a ready-to-paste `pattern:` |
| `library.info` | roots, index status, counts per type/kind/kit, licence limits |

Typical agent flow: `library.info` → `library.kits` (take a starter kit, or)
`library.search query="punchy 909 kick"` → paste the `library://` URI into a
`song.yaml` pattern lane → listen with the human → `library.search
similar_to=<uri>` for a sibling when the human says "less boomy".

## Where the library lives

The index lives **inside** each library root as `.muestra-index.json` (never in
the project); the starter-kit manifest is `starter-kits.json` next to it. The
expected layout is `kits/<kit>/<anything>/<file>.wav` (the kit is the folder
under `kits/`; for other layouts it is the top-level folder).
`library://kits/tr808-musicradar/...` is just the path under the root.

Roots are found in this order (`muestra.library.discovery`):

1. `[library] roots` in `muestra.toml` (relative to the project dir, `..` ok);
2. `MUESTRA_LIBRARY` — one or more directories separated by the OS path
   separator (`;` on Windows, `:` elsewhere);
3. `~/Documents/MuestraLibrary`, if it exists and `MUESTRA_LIBRARY` is unset.

Earlier roots win when two hold the same relative path. Opt out of 2 and 3 with
`roots = []` in `muestra.toml` or `MUESTRA_LIBRARY=none` (or empty). A missing
or un-indexed library answers with an error whose hint says what to do
(`no_library`, `library_not_indexed`: "run library.index").

## Indexing

`library.index` (or `muestra library index [--path DIR] [--force]`) walks the
root and measures every audio file (`.wav .flac .aif .aiff .ogg .mp3`).
Skipped: folders starting with `_` or `.` and a top-level `instruments/`
(instrument banks belong to the plugin host).

* **Incremental.** Unchanged files (same size and mtime) are reused untouched;
  a touched-but-identical file (same size and cheap hash of the first and last
  64 KiB) is reused too; only new/changed files are measured, in a process pool
  (`workers`, default `min(cpu, 8)`). Removed files drop out. `force=true`
  re-measures everything; a new `analyzer_version` does it automatically.
* **Never fatal.** A corrupt, empty or unreadable file is reported in the
  index's `problems` (and the tool result) and retried next run.
* **Progress.** `index_root(root, progress=fn)` calls `fn(done, total, rel)`;
  the tool function takes the same optional callback.
* **Measured on the owner's real library** (Windows 11 laptop, busy with other
  agents, 8 workers): **4,661 files from scratch in 36.6 s** (three full runs: 33–46 s; a re-run with nothing changed takes 1.2 s). A first, unoptimised draft took about 285 s on the same machine while it was under heavy load.

### What each entry contains

| field | meaning |
|---|---|
| `path`, `kit`, `size`, `mtime_ns`, `sig` | identity and change detection |
| `type` | `kick snare clap chat ohat hat tom cym rim perc drums fill bass keys synth guitar pad vox fx other` |
| `subtype` | `conga cowbell shaker tambourine crash ride sidestick snares-off roll glitch hand-drum …` |
| `kind` | `one-shot`, `loop` (repeats on a grid) or `phrase` (tempo-bound one-off: fills, rolls — warp it, never tile it) |
| `bpm`, `bpm_source`, `bpm_confidence` | loops/phrases only (see below) |
| `key`, `key_confidence` | tonal loops only, canonical `Am` / `F#` (`muestra.music.parse_key`) |
| `duration_s sample_rate bit_depth channels` | container facts |
| `peak_dbfs rms_dbfs loudness_lufs` | levels; LUFS is BS.1770 K-weighted (whole file when shorter than 400 ms) |
| `clipping` | ≥4 consecutive full-scale samples that are flat (within 2 LSB). A normalised kick peak is NOT clipping |
| `features` | the agent's ears — next section |
| `tags` | type, subtype, kind, machine/source tags, descriptive tags |
| `flags` | heads-ups, see *Flags* |

### Types: names first, audio verifies

`type` comes from the library's normalised names (`kick-…`, `ohat-…`, `misc-…-clhat-…`)
and folders (`drum-fills/`, `beats/`, `instruments/`, `world-perc-loops/`). Hat
openness comes from the words (`open`, `ophat`, `hho` → `ohat`; `closed`,
`clhat`, `hh` → `chat`; pedal → `hat/pedal`). Files under loop folders become
`kind: loop`, fills/rolls become `kind: phrase`, `type: fill`.

The measured spectrum then **verifies** the name. A clear contradiction (a
"kick" that is all hi-hat noise, a "hat" that is a 60 Hz thud, a "snare" that is
a kick) is **flagged, never silently retyped**: `type-mismatch:name=kick,audio=hat/cymbal-like`.
Files whose names say nothing get a conservative audio-based type flagged
`type-inferred:kick`. Snares with the snares off (`snroff`) are exempt: they
are legitimately low and tom-like (`subtype: snares-off`).

### BPM

Only loops and phrases carry a BPM. It is read from the name (`-120-`,
`140bpm`) and **cross-checked by detection** (onset-strength autocorrelation,
snapped to the tempo implied by the file length when that fits a whole number
of beats). Agreement (also at half/double time) gives `bpm_source:
name+detected` and confidence ≥ 0.7. Disagreement keeps the name with
confidence 0.4 and the flag `bpm-disagreement:name=95,detected=142.5`. A loop
with no BPM in its name gets the detected one only if confident
(`bpm-from-audio-only`). Samples with a BPM can be warped in `song.yaml`; those
without cannot (R4).

### Key

Krumhansl-Schmuckler on a chromagram, for tonal loops (`bass keys synth guitar
pad vox other`), canonical form via `muestra.music`. It is a **heuristic**:
reliable for sustained chords, shaky on arpeggios and bass lines. Confidence
below 0.45 adds `low-confidence-key`; below 0.2 no key is stored.

### Reference numbers: the owner's library

From the forced full run above (4,661 files, 11 kits, 0 unreadable): **kick 503,
snare 756, perc 929, fill 523, cym 362, tom 316, ohat 272, chat 264, clap 168,
hat 147, rim 134, drums 108, fx 88, bass 36, synth 28, pad 27**; by kind 3,668
one-shots, 523 phrases, 470 loops. 316 files carry flags: 241 `low-level` (mostly
the quiet acoustic kits), 48 `clipping` (the French-house beat loops are
limited by design, plus a few hot hits), 13 `low-confidence-key`, 6
`type-mismatch` (a vintage-drumbox "snare" that is hat noise, a TR-8 "snare 05"
that is a low thud, two Legowelt/ER-1 snares that are kick-like, two processed
cymbals/hats), 4 `bpm-disagreement` (95 bpm names that detect as 1.5×) and 1
`bpm-from-audio-only`. The Goldbaby kit carries `no-ai-training`, Legowelt
`no-resale`, every kit `no-redistribution`.

## Features (the agent's ears)

All measured on the mono mix. Times in ms, frequencies in Hz, band energies are
**shares of spectral energy** (0..1, sum ≈ 1).

| feature | definition | reads as |
|---|---|---|
| `fundamental_hz`, `note` | strongest narrow peak in 30–300 Hz of the 200 ms that start 15 ms after the envelope peak (past the click, past most of an 808's pitch sweep); only reported when that band holds ≥25 % of the segment's energy and the peak is prominent | kick/tom/bass tuning: "B1 (62 Hz)" — tune the bass to it |
| `attack_ms` | envelope rise from 10 % to 90 % of peak (peak-hold envelope, ~1 ms resolution; 0.8 = as sharp as measurable) | small = clicky/snappy; 20+ = a swell |
| `decay_ms` | time from the peak until the envelope is 30 dB down | tight vs ringing |
| `spectral_centroid_hz` | energy-weighted mean frequency | brightness |
| `sub low mid high air` | energy share in 20–120 / 120–400 / 400–2000 / 2000–8000 / 8000+ Hz | where the weight is |
| `tonality` | chance-corrected share of energy in the six strongest narrow spectral peaks: 0 = noise, 1 = pure tone | tonal vs noisy |

Attack, decay and pitch are only measured for files up to 6 s.

### Derived tags and their thresholds

| tag | rule |
|---|---|
| `short` / `long` | `decay_ms` below / above the per-type limits: kick 150/450, tom 200/700, snare & clap 120/450, rim 60/250, chat 70/200, ohat 200/700, hat 120/450, cym 600/2500, bass 200/900, perc 100/500, other 120/600 (one-shots only) |
| `punchy` | kick/tom/bass: attack ≤ 3 ms, decay ≤ 320 ms and sub+low ≥ 0.5. snare/clap/rim: attack ≤ 6 ms, decay ≤ 350 ms |
| `boomy` | kick/tom: decay > 400 ms and sub ≥ 0.4 |
| `subby` | kick/tom/bass/perc/other with sub ≥ 0.8 |
| `clicky` | kick/tom: attack ≤ 3 ms and mid+high+air ≥ 0.08 |
| `bright` / `dark` | centroid above / below per-type limits: kick 350/110 Hz, tom 800/200, bass 600/150, snare 3500/1500, clap 3000/1500, rim 4000/1500, chat 9500/5500, ohat 9000/5000, hat 9500/5500, cym 8000/4000, other 4000/700 |
| `tonal` / `noisy` | tonality ≥ 0.5 / ≤ 0.12 |
| `tuned` | kick/tom/bass with a measured fundamental |
| `stereo` / `wide` | side/mid RMS ratio ≥ 0.05 / ≥ 0.35 |
| `hot` / `quiet` | peak ≥ −0.3 dBFS / ≤ −12 dBFS (`quiet` also flags `low-level`) |
| `clean` / `clipped` | no flat full-scale runs / has them |
| `lofi` | sample rate ≤ 22.05 kHz |

Machine/source tags come from the path: `909 808 606 707 tr8 roland dmx
oberheim tape goldbaby sp1200 mpc60 korg alesis nord jomox vermona kurzweil
acoustic vinyl electronic foley chiptune 80s gated-reverb lofi legowelt
hand-percussion french-house …`. `tr8` is the Roland TR-8 (909/808-*modelled*
hardware): a search for `909` also finds it, ranked below real `909` tags.

Thresholds were calibrated on the real library's kicks (e.g. the TR-8 kick the
REPORT recommends reads `short punchy`, 62 Hz, 96 ms) but **nobody has
listened** — treat tags as descriptions of measurements, not taste.

### Flags

`clipping` · `low-level` (peak ≤ −12 dBFS) · `type-mismatch:name=…,audio=…` ·
`type-inferred:…` · `bpm-disagreement:…` · `bpm-from-audio-only` ·
`low-confidence-key` · `very-short` (< 20 ms) · `unusable-uri` (the file name has
characters `library://` cannot express — only `A-Za-z0-9._~-` and `/` are allowed —
so a song could not reference it: rename the file). Search penalises flagged files;
`clean_only=true` drops them.

## Search

`library.search` parameters: `query` (free text), `bpm`, `key`, `type`,
`subtype`, `kind`, `kit`, `tags`, `duration_min/max` (seconds), `similar_to`
(a `library://` URI), `bpm_tolerance` (default 0.04), `half_double` (default
true), `key_exact`, `clean_only`, `detail` (`compact` | `full`), `limit`
(default 10, max 100), `offset`. The result carries `total`, `interpreted` (how
the query was understood), `searched_roots`, `unindexed_roots` and a `hint`.

### Query language

English and Spanish words, accents ignored, mixed freely:

* **Instrument words** are hard filters (several of them mean "any of"):
  `kick bombo`, `snare caja redoblante tambor`, `clap palmas`, `hat hihat charles`,
  `open hat` / `charles abierto` → `ohat`, `closed hat` / `charles cerrado` → `chat`,
  `tom timbal`, `cymbal platillo`, `crash ride splash china gong`, `rim rimshot
  aro`, `perc percusion`, `conga cowbell cencerro shaker maracas pandereta
  güiro claves djembe cajón`, `fill relleno redoble`, `drums beat ritmo`, `bass
  bajo`, `keys teclado`, `synth`, `pad`, `guitar guitarra`, `vox voz`, `fx`.
* **Kind words**: `loop bucle`, `oneshot hit`, `phrase`.
* **Descriptors and machines are preferences**: `punchy punzante`, `boomy`,
  `subby grave`, `clicky`, `short corto seco`, `long largo`, `bright brillante`,
  `dark oscuro`, `noisy`, `tonal afinado`, `clean limpio`, `stereo`, `lofi`,
  `808 909 606 707 tr8 linn dmx tape cinta acoustic acústico vinyl 80s gated …`.
  They raise the score and appear in `reasons`; they never hide results.
* **Numbers and keys**: `128`, `128bpm` → BPM; `Am`, `F# minor`, `Bbmaj` → key.
* **Any other word** must appear in the path, kit or tags (so a kit name or a
  file-name fragment works). If nothing matches all such words, the search
  retries without them and says so in `hint`.

### Ranking and reasons

`score` ∈ 0..1 = 0.4·(preferences matched) + 0.3·quality + 0.3·BPM/key fit (or
0.5/0.5 when there is no BPM/key). **Quality** prefers: unclipped (+0.2 / −0.3),
a sane level (−0.1 at ≤ −12 dBFS, −0.3 at ≤ −20), 24-bit, ≥ 44.1 kHz, and no
flags (a name/audio mismatch costs −0.25). Near-ties are broken in favour of
**variety** across folders, so a vague query ("clap") shows several sources.
Every hit has `reasons` ("type kick", "tag punchy", "bpm 128 (target 128,
exact)", "bpm 120 = double-time of 60", "key Am compatible with C", "sound
distance 0.16 to the reference") and a one-line `summary` the agent can quote:
`kick | 0.26s | pitch B1 (62 Hz) | decay 96 ms | attack 0.8 ms | centroid 128 Hz | short punchy`.

* **BPM**: within `bpm_tolerance` of the target; half/double time also matches
  (reason says which). If nothing matches, the hint lists the tempos that do
  exist for the rest of the query ("available: 100, 130, 140").
* **Key**: same key 1.0, relative major/minor 0.9, a fifth away (same mode) 0.7,
  parallel 0.45; `key_exact` keeps only the same key. `bpm` and `key` only match
  loops/phrases — one-shots have neither.
* **Similar to**: weighted distance over standardised features (log pitch,
  decay, attack, centroid, the five band shares, tonality, log duration);
  restricted to the anchor's `type` unless you say otherwise.
* **Compact vs full**: compact results omit empty fields, the feature block
  and the licence (≈160 tokens a hit); a `licence:no-ai-training` or
  `licence:non-commercial` flag is always shown. `detail="full"` adds
  `features` and the kit's `license`.

### Examples

```text
library.search query="punchy 909 kick"
library.search query="bombo corto"                       # same engine, Spanish
library.search query="open hat" kit="tr8"
library.search query="conga loop" bpm=140
library.search type="fill" bpm=128                       # drum fills at 128 (also 64 / 256)
library.search query="pad" key="Am"                      # Am, C, Em, Dm ...
library.search similar_to="library://kits/tr8-hardware-machines/roland-tr-8/kick-tr-8-kick-02.wav"
library.search query="snare" clean_only=true detail="full" limit=5
```

## Starter kits

`library.kits` lists named drum kits (lane → sample); with `name=<kit>` it adds
the measured description of every sample, the kit's named patterns
(`patterns: {name: what it does}`) and one `pattern:` that drops straight under
a drum track in `song.yaml` — `pattern=<name>` picks it, default `basic`.
Every curated kit has (WP12):

| pattern | what |
|---|---|
| `basic` | kick `x...x...x...x...`, clap/snare `....x.......x...`, ohat `..x...x...x...x.`, soft closed hat on the beats `o...o...o...o...`; no crash, toms or percussion |
| `busy` | `basic` + closed hats in 16ths with ghosts (`xo.oxo.oxo.oxo.o`, never on an open-hat step) + light percussion of the kit (cowbell, conga, shaker, tambourine) |
| `fill` | the bar before a new part: kick/clap push at the end, hats stop on beat 4 (toms on city-pop-80s) |
| `phrase` | (kits with a crash) 4 bars of `basic` with ONE crash on the first downbeat |

The old single pattern put a crash and a cowbell on every bar. A kit without
named patterns falls back to `basic`-style steps for kick/clap/snare/rim/hats
only. Patterns are written at 16 steps per bar; `steps_per_bar` 32/48/64
spaces them out and 8 keeps every other step (musical time is preserved). Warnings call out a sample missing from disk, typed
wrongly for its lane, or flagged; `restrictions` carries the licence limits of
the packs the samples come from.

The manifest is `starter-kits.json` in the library root (schema
`schemas/starter-kits.schema.json`):

```json
{"schema_version": 0, "kits": {"my-kit": {
  "description": "...", "lanes": {"kick": "kits/pack/kick-01.wav", "clap": "..."},
  "patterns": {"basic": {"description": "...", "bars": 1,
    "steps": {"kick": "x...x...x...x...", "clap": "....x.......x..."}}}}}}
```

`patterns` is optional (WP12); each lane in a pattern must be one of the kit's
lanes and have 16 × `bars` steps.

If a root has none, the template bundled with the package is used
(`src/muestra/library/data/starter-kits.json`, URIs only — no audio); kits
whose files are missing show `available: false`. Curated for the owner's
library:

| kit | idea | members |
|---|---|---|
| `disco-01` | 909-style four-on-the-floor (REPORT.md §2) | TR-8 kick 02, 909 clap, TR-8 closed/open hat, 808 cowbell, TR-8 crash |
| `disco-808` | 808 flavour | short 808 kick (≈60 Hz, 185 ms), 808 clap, closed/open hat, cowbell, conga |
| `city-pop-80s` | 80s pop / LinnDrum-*era* (not actual LinnDrum) | deep kick, gated-reverb snare + clap, bright hats, tom, shaker |
| `acoustic-disco` | dry close-miked live kit | short kick, snare, crisp hats, tambourine, crash |

**Nobody has listened to these.** Members were chosen from measured features
(unclipped, sensible level, tight decays, tuned kicks) and from the source's
reputation, exactly like the REPORT's own pick. Audition before committing; the
manifest, the tool output and each kit's `notes` say so. Levels differ between
packs — set `gain_db` per lane.

## Adding your own samples

1. Put the files under the library root, ideally `kits/<your-pack>/…`. Names
   help classification: start with the type (`kick-`, `snare-`, `clap-`,
   `chat-`, `ohat-`, `tom-`, `crash-`, `perc-`, `fill-`) and put the tempo in
   loop names (`beat-120-01.wav`, `loop-128bpm.wav`); put loops under a folder
   called `loops/` or `beats/`.
2. Add a `LICENSE.txt` in the pack folder (its terms are summarised into the
   index; prohibitions on redistribution, AI/ML use or resale become
   `restrictions`).
3. `library.index` — only the new files are measured. A separate library works
   the same way: `library.index path=/some/dir` registers it in `muestra.toml`.
4. Check `problems` in the result for unreadable files, and `flags` for anything
   odd; adjust names if a type is wrong.
5. Optionally add your own kits to `starter-kits.json`.

## Licences

Every pack's `LICENSE.txt` is summarised per kit in the index
(`library.info`, `search detail=full`). All packs in the owner's library allow
use **in your music** (commercial and YouTube included) but **forbid
redistributing the raw samples**: never copy the WAVs into a repository or ship
them with the product (the repo holds only code, URIs and the kit template).
The Goldbaby packs additionally forbid any machine-learning / AI use
(`no-ai-training`); this system selects and plays them but does not train on
them — the flag is surfaced so nobody forgets. Legowelt's terms are informal
(no selling the samples). The index itself contains measurements and paths, not
audio.
