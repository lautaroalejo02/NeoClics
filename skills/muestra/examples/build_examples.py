# ruff: noqa: E501  (long lines are YAML text that must match the rendered examples byte for byte)
"""Build the skill's example songs (Tiger Rag on its corrected grid + a template).

Usage: uv run python skills/muestra/examples/build_examples.py skills/muestra/examples

Tiger Rag (1928): the beat tracker reads ~250 BPM (it counts eighth notes).
``source.analyze`` picks the octave inside 70-160 BPM and stores a corrected
grid (tempo_factor 0.5, 125 BPM), so every example is a NORMAL 4/4 grid:
16-step patterns, bars are musical bars, chop lengths are real beats.
"""

import sys
from pathlib import Path

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)

KICK = "library://kits/tr8-hardware-machines/roland-tr-8/kick-tr-8-kick-02.wav"
CLAP = "library://kits/essential-kits-musicradar/tr-909-kit/clap-tr-909-clap.wav"
CHAT = "library://kits/tr8-hardware-machines/roland-tr-8/chat-tr-8-closed-hat-01.wav"
OHAT = "library://kits/tr8-hardware-machines/roland-tr-8/ohat-tr-8-open-hat-01.wav"
COWB = "library://kits/tr808-musicradar/cowbell-cb/cowbell-e808-cb-02.wav"


def phrase(pid, bars, pat, comment=""):
    """pat: (beat inside the phrase, pitch, length in beats, velocity)."""
    lines = [
        f"- id: {pid}{'  # ' + comment if comment else ''}",
        f"  length_bars: {bars}",
        "  notes:",
    ]
    for b, p, ln, v in pat:
        lines.append(f"  - {{beat: {b:g}, pitch: {p}, length_beats: {ln:g}, velocity: {v}}}")
    return "\n".join(lines) + "\n"


HEAD = """schema_version: 0
# Tiger Rag (The Home Towners, 1928; CC0), primeros 30 s, fuente src1.
# El tracker la lee a 250 BPM (cuenta corcheas); source.analyze eligio la octava
# en 70-160 y la grilla quedo en 125 BPM (grid.tempo_factor 0.5). Grilla NORMAL:
# compases musicales, patrones de 16 pasos. Antes de armar el tema, el humano
# confirma la grilla de oido (source.preview_grid).
"""


def tiger(meta_bpm, bpm_comment, key, key_comment, title):
    return (
        HEAD
        + f"""meta:
  title: {title}
  bpm: {meta_bpm}  # {bpm_comment}
  key: {key}  # {key_comment}
  length_bars: 16
  seed: 0
sources:
- id: src1
  file: sources/src1.wav
"""
    )


MASTER = """master:
  fx:
  - {type: compressor, threshold_db: -18, ratio: 3}
  - {type: limiter, ceiling_db: -1.0}
  target_lufs: -12
"""

# ------------------------------------------------------------------ base (v004)
tiger_v1 = (
    tiger(
        "126.32",
        "123.08 (tempo local de c1) x 2^(0.45/12): el disco esta 45 cents bajo y +0.45 st lo afina",
        "G",
        "donde encaja el bajo con el sample (ears: bass_vs_sample)",
        "tiger-funk",
    )
    + """chops:
- id: c1
  source: src1
  stem: other  # sin la bateria ni el bajo del disco
  start_downbeat: 9  # cs001/cand1, el pick auto: mejor vuelta y pulso mas estable
  length_beats: 8  # 2 compases
  warp: {mode: resample, pitch_semitones: 0}
  fade_ms: 8
phrases:
"""
    + phrase(
        "bajo",
        1,
        [(1.5, "G1", 0.4, 100), (2.5, "G1", 0.4, 92), (3.5, "G2", 0.4, 100), (4.5, "G1", 0.4, 92)],
        "contratiempos en Sol: tonica, tonica, octava, tonica",
    )
    + f"""tracks:
- id: sample
  clips:
  - {{chop: c1, at_bar: 1, repeat: 8}}
  fx:
  - {{id: hp, type: highpass, cutoff_hz: 150}}  # deja 40-150 Hz para bombo y bajo
  - {{id: lp, type: lowpass, cutoff_hz: 18000}}  # automatizado abajo
  - {{id: sc, type: sidechain, key_track: kick, depth: 0.5, release_ms: 140}}
  gain_db: 9  # el disco es bajito (-27 LUFS)
- id: kick
  pattern:
    steps_per_bar: 16
    lanes:
      kick: {{sample: "{KICK}", steps: "x...x...x...x...", gain_db: -4}}
- id: drums
  at_bar: 5
  pattern:
    steps_per_bar: 16
    lanes:
      clap: {{sample: "{CLAP}", steps: "....x.......x...", gain_db: -5}}
      chat: {{sample: "{CHAT}", steps: "x.o.x.o.x.o.x.o.", gain_db: -12}}
      ohat: {{sample: "{OHAT}", steps: "..x...x...x...x.", gain_db: -10}}
- id: bass
  at_bar: 5
  instrument: {{plugin: finger-bass}}
  phrases:
  - {{phrase: bajo}}  # se repite hasta el final
  gain_db: 13
automation:
- {{target: tracks.sample.fx.lp.cutoff_hz, curve: exp, points: [[1, 500], [5, 18000]]}}  # intro filtrada: 4 compases
"""
    + MASTER
)

# ------------------------------------------------------------------ Macross 82-99
clips = []
for b in (1, 5, 9, 13):
    clips.append(f"  - {{chop: c1, at_bar: {b}, repeat: 3}}")
    clips.append(
        f"  - {{chop: c2, at_bar: {b + 3}, repeat: 4}}  # stutter: el primer tiempo 4 veces"
    )
macross = (
    tiger(
        "133.15",
        "122.45 (tempo local de c1, 4 beats) x 2^(1.45/12): +1.45 st, mas rapido y mas agudo",
        "G#",
        "un semitono arriba de la version base",
        "tiger-funk-macross",
    )
    + """chops:
- id: c1
  source: src1
  stem: other
  start_downbeat: 9
  length_beats: 4  # 1 compas, repetido: corte corto
  warp: {mode: resample, pitch_semitones: 0}
  fade_ms: 6
- id: c2
  source: src1
  stem: other
  start_downbeat: 9
  length_beats: 1  # una negra, para el stutter
  warp: {mode: resample, pitch_semitones: -0.35}  # su tempo local es 120.0: 12*log2(120/122.45) lo iguala a c1
  fade_ms: 6
phrases:
"""
    + phrase(
        "octavas",
        1,
        [
            (1.5, "G#1", 0.3, 110),
            (2.5, "G#2", 0.3, 96),
            (3.5, "G#1", 0.3, 110),
            (4.5, "G#2", 0.3, 96),
        ],
        "octavas cortas en contratiempo",
    )
    + """tracks:
- id: sample
  clips:
"""
    + "\n".join(clips)
    + f"""
  fx:
  - {{id: hp, type: highpass, cutoff_hz: 200}}
  - {{id: lp, type: lowpass, cutoff_hz: 18000}}
  - {{id: sat, type: saturation, drive_db: 8}}  # crudo
  - {{id: sc, type: sidechain, key_track: kick, depth: 0.7, release_ms: 170}}  # bombeo marcado
  gain_db: 8
- id: kick
  pattern:
    steps_per_bar: 16
    lanes:
      kick: {{sample: "{KICK}", steps: "x...x...x...x...", gain_db: -4}}
- id: drums
  at_bar: 5
  pattern:
    steps_per_bar: 16
    lanes:
      clap: {{sample: "{CLAP}", steps: "....x.......x...", gain_db: -4}}
      ohat: {{sample: "{OHAT}", steps: "..x...x...x...x.", gain_db: -9}}
- id: bass
  at_bar: 5
  instrument: {{plugin: synth-bass}}
  phrases:
  - {{phrase: octavas}}
  fx:
  - {{id: sc, type: sidechain, key_track: kick, depth: 0.5, release_ms: 90}}
  gain_db: 0
automation:
- {{target: tracks.sample.fx.lp.cutoff_hz, curve: exp, points: [[1, 400], [4.5, 2500], [5, 18000]]}}  # sube y abre de golpe en el drop
- {{target: tracks.sample.fx.hp.cutoff_hz, curve: exp, points: [[12, 200], [13, 900], [13, 200]]}}  # barrido high-pass antes de la ultima vuelta
"""
    + MASTER
)

# ------------------------------------------------------------------ Yung Bae
yungbae = (
    tiger(
        "126.32",
        "igual que la base: afinado",
        "G",
        "igual que la base",
        "tiger-funk-yungbae",
    )
    + """chops:
- id: c1
  source: src1
  stem: other
  start_downbeat: 9
  length_beats: 8
  warp: {mode: resample, pitch_semitones: 0}
  fade_ms: 8
phrases:
"""
    + phrase(
        "groove",
        1,
        [
            (1.5, "G1", 0.35, 100),
            (2.0, "G2", 0.25, 80),
            (2.5, "G1", 0.35, 96),
            (3.5, "D2", 0.25, 100),
            (3.75, "F2", 0.25, 84),
            (4.5, "G1", 0.35, 96),
        ],
        "octava y notas de paso (Sol, Re, Fa)",
    )
    + f"""tracks:
- id: sample
  clips:
  - {{chop: c1, at_bar: 1, repeat: 8}}
  fx:
  - {{id: hp, type: highpass, cutoff_hz: 160}}
  - {{id: lp, type: lowpass, cutoff_hz: 18000}}
  - {{id: chorus, type: chorus, rate_hz: 0.4, depth: 0.2, mix: 0.25}}  # brillo pulido
  - {{id: sc, type: sidechain, key_track: kick, depth: 0.45, release_ms: 120}}
  gain_db: 9
- id: kick
  until_bar: 13  # sale en el break (compases 13 y 14)
  pattern:
    steps_per_bar: 16
    lanes:
      kick: {{sample: "{KICK}", steps: "x...x...x...x...", gain_db: -4}}
- id: kick-back
  at_bar: 15  # vuelve en los ultimos 2 compases
  pattern:
    steps_per_bar: 16
    lanes:
      kick: {{sample: "{KICK}", steps: "x...x...x...x...", gain_db: -4}}
- id: drums
  at_bar: 5
  until_bar: 13
  pattern:
    steps_per_bar: 16
    swing: 0.2  # semicorcheas apenas arrastradas: groove
    lanes:
      clap: {{sample: "{CLAP}", steps: "....x.......x...", gain_db: -5}}
      chat: {{sample: "{CHAT}", steps: "xoxoxoxoxoxoxoxo", gain_db: -14}}
      ohat: {{sample: "{OHAT}", steps: "..x...x...x...x.", gain_db: -10}}
      perc: {{sample: "{COWB}", steps: "...........o..o.", gain_db: -16}}
- id: bass
  at_bar: 5
  instrument: {{plugin: finger-bass}}  # slap-bass tambien sirve; ver styles.md
  phrases:
  - {{phrase: groove, repeat: 8}}  # compases 5-12; calla en el break
  - {{phrase: groove, at_bar: 15}}  # vuelve con el bombo
  fx:
  - {{id: sc, type: sidechain, key_track: kick, depth: 0.5, release_ms: 90}}
  gain_db: 10
automation:
- {{target: tracks.sample.fx.lp.cutoff_hz, curve: exp, points: [[1, 600], [5, 18000], [13, 18000], [15, 900], [15, 18000]]}}  # intro y break filtrados
"""
    + MASTER
)

# ------------------------------------------------------------------ Night Tempo
nighttempo = (
    tiger(
        "126.32",
        "igual que la base: afinado",
        "G",
        "igual que la base",
        "tiger-funk-nighttempo",
    )
    + f"""chops:
- id: c1
  source: src1
  stem: mix  # el disco entero: su bateria y su bajo quedan
  start_downbeat: 9
  length_beats: 16  # 4 compases: la frase se reconoce
  warp: {{mode: stretch, pitch_semitones: 0.45}}  # 4 compases derivan (grid_drift): stretch alinea cada beat; +0.45 st afina el disco
  fade_ms: 10
tracks:
- id: sample
  clips:
  - {{chop: c1, at_bar: 1, repeat: 4}}
  fx:
  - {{id: hp, type: highpass, cutoff_hz: 70}}  # solo limpia rumble
  - {{id: sc, type: sidechain, key_track: kick, depth: 0.25, release_ms: 110}}  # respira apenas
  gain_db: 10
- id: kick
  at_bar: 5
  pattern:
    steps_per_bar: 16
    lanes:
      kick: {{sample: "{KICK}", steps: "x...x...x...x...", gain_db: -4}}
- id: drums
  at_bar: 9
  pattern:
    steps_per_bar: 16
    lanes:
      clap: {{sample: "{CLAP}", steps: "....x.......x...", gain_db: -9}}
      ohat: {{sample: "{OHAT}", steps: "..x...x...x...x.", gain_db: -14}}
automation: []
master:
  fx:
  - {{type: compressor, threshold_db: -16, ratio: 2}}
  - {{type: limiter, ceiling_db: -1.0}}
  target_lufs: -12
"""
)

# ------------------------------------------------------------------ template
template = (
    """schema_version: 0
# PLANTILLA future funk. Cambia: source y chop (source.find_chops + chops.choose),
# meta.bpm (calculo de resample en recipes/future-funk.md), meta.key, la tonica
# de la frase del bajo (A) y los gain_db. La grilla de la fuente ya esta en un
# tempo plausible (source.analyze / source.regrid) y el humano la confirmo de oido.
meta:
  title: borrador-01
  bpm: 124.0  # = tempo local del chop x 2^(semitonos/12)
  key: Am
  length_bars: 16
  seed: 0
sources:
- id: src1
  file: sources/src1.wav
chops:
- id: c1
  source: src1
  stem: other
  start_downbeat: 9
  length_beats: 8  # 2 compases
  warp: {mode: resample, pitch_semitones: 0}
  fade_ms: 8
phrases:
"""
    + phrase(
        "bajo",
        1,
        [(1.5, "A1", 0.4, 100), (2.5, "A1", 0.4, 92), (3.5, "A2", 0.4, 100), (4.5, "A1", 0.4, 92)],
        "contratiempos: tonica, tonica, octava, tonica",
    )
    + f"""tracks:
- id: sample
  clips:
  - {{chop: c1, at_bar: 1, repeat: 8}}
  fx:
  - {{id: hp, type: highpass, cutoff_hz: 150}}
  - {{id: lp, type: lowpass, cutoff_hz: 18000}}
  - {{id: sc, type: sidechain, key_track: kick, depth: 0.5, release_ms: 140}}
  gain_db: 0
- id: kick
  pattern:
    steps_per_bar: 16
    lanes:
      kick: {{sample: "{KICK}", steps: "x...x...x...x...", gain_db: -4}}
- id: drums
  at_bar: 5
  pattern:
    steps_per_bar: 16
    lanes:
      clap: {{sample: "{CLAP}", steps: "....x.......x...", gain_db: -5}}
      chat: {{sample: "{CHAT}", steps: "x.o.x.o.x.o.x.o.", gain_db: -12}}
      ohat: {{sample: "{OHAT}", steps: "..x...x...x...x.", gain_db: -10}}
- id: bass
  at_bar: 5
  instrument: {{plugin: finger-bass}}
  phrases:
  - {{phrase: bajo}}
  gain_db: 6
automation:
- {{target: tracks.sample.fx.lp.cutoff_hz, curve: exp, points: [[1, 500], [5, 18000]]}}
"""
    + MASTER
)

for name, text in {
    "tiger-rag.yaml": tiger_v1,
    "tiger-rag-macross.yaml": macross,
    "tiger-rag-yungbae.yaml": yungbae,
    "tiger-rag-nighttempo.yaml": nighttempo,
    "template.yaml": template,
}.items():
    (OUT / name).write_text(text, encoding="utf-8", newline="\n")
    print(name, len(text.splitlines()), "lines", len(text.encode()), "bytes")
