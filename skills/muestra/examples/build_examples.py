# ruff: noqa: E501  (long lines are YAML text that must match the rendered examples byte for byte)
"""Build the skill's example songs (Tiger Rag double grid + normal-grid template).

Usage: uv run python skills/muestra/examples/build_examples.py skills/muestra/examples
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


def notes(first, last, grid, pat, indent="  "):
    """pat: (musical beat in bar 1..4.x, pitch, musical length, velocity); bars are musical."""
    out = []
    for bar in range(first, last + 1):
        for b, p, ln, v in pat:
            mb = (bar - 1) * 4 + b
            pb = (mb - 1) * grid + 1
            out.append(
                f"{indent}- {{beat: {pb:g}, pitch: {p}, length_beats: {ln * grid:g}, velocity: {v}}}"
            )
    return "\n".join(out)


HEAD = """schema_version: 0
# Tiger Rag (The Home Towners, 1928; CC0), primeros 30 s, fuente src1.
# El tracker la mide a ~250 BPM: cuenta corcheas. DOBLE GRILLA:
#   1 compas musical = 2 compases del proyecto, 1 beat del proyecto = 1 corchea,
#   patrones con steps_per_bar: 8 y bars: 2 (16 caracteres = 1 compas musical).
# Ver recipes/future-funk.md, "Fuente a doble tempo".
"""


def tiger(meta_bpm, bpm_comment, key, key_comment, title):
    return (
        HEAD
        + f"""meta:
  title: {title}
  bpm: {meta_bpm}  # {bpm_comment}
  key: {key}  # {key_comment}
  length_bars: 32  # 16 compases musicales
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
base_bass = notes(
    5,
    16,
    2,
    [(1.5, "G1", 0.4, 100), (2.5, "G1", 0.4, 92), (3.5, "G2", 0.4, 100), (4.5, "G1", 0.4, 92)],
)
tiger_v1 = (
    tiger(
        "252.64",
        "246.15 (tempo local de c1) x 2^(0.45/12): el disco esta 45 cents bajo y +0.45 st lo afina; sensacion 126.3",
        "G",
        "donde encaja el bajo con el sample (ears: bass_vs_sample)",
        "tiger-funk",
    )
    + f"""chops:
- id: c1
  source: src1
  stem: other  # sin la bateria ni el bajo del disco
  start_downbeat: 17  # cs001/cand1, el pick auto: mejor vuelta y pulso mas estable
  length_beats: 16  # 16 beats de la fuente = 2 compases musicales
  warp: {{mode: resample, pitch_semitones: 0}}
  fade_ms: 8
tracks:
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
    steps_per_bar: 8
    bars: 2
    lanes:
      kick: {{sample: "{KICK}", steps: "x...x...x...x...", gain_db: -4}}
- id: drums
  at_bar: 9  # compas musical 5
  pattern:
    steps_per_bar: 8
    bars: 2
    lanes:
      clap: {{sample: "{CLAP}", steps: "....x.......x...", gain_db: -5}}
      chat: {{sample: "{CHAT}", steps: "x.o.x.o.x.o.x.o.", gain_db: -12}}
      ohat: {{sample: "{OHAT}", steps: "..x...x...x...x.", gain_db: -10}}
- id: bass
  at_bar: 9
  instrument: {{plugin: finger-bass}}
  notes:  # contratiempos en Sol, 4 por compas musical (generadas con un loop)
{base_bass}
  gain_db: 13
automation:
- {{target: tracks.sample.fx.lp.cutoff_hz, curve: exp, points: [[1, 500], [9, 18000]]}}  # intro filtrada: 4 compases musicales
"""
    + MASTER
)

# ------------------------------------------------------------------ Macross 82-99
mac_bass = notes(
    5,
    16,
    2,
    [(1.5, "G#1", 0.3, 110), (2.5, "G#2", 0.3, 96), (3.5, "G#1", 0.3, 110), (4.5, "G#2", 0.3, 96)],
)
clips = []
for b in (1, 9, 17, 25):
    clips.append(f"  - {{chop: c1, at_bar: {b}, repeat: 3}}")
    clips.append(
        f"  - {{chop: c2, at_bar: {b + 6}, repeat: 4}}  # stutter: el primer tiempo 4 veces"
    )
macross = (
    tiger(
        "266.29",
        "244.90 (tempo local de c1, 8 beats) x 2^(1.45/12): +1.45 st, mas rapido y mas agudo; sensacion 133.1",
        "G#",
        "un semitono arriba de la version base",
        "tiger-funk-macross",
    )
    + """chops:
- id: c1
  source: src1
  stem: other
  start_downbeat: 17
  length_beats: 8  # 1 compas musical, repetido: corte corto
  warp: {mode: resample, pitch_semitones: 0}
  fade_ms: 6
- id: c2
  source: src1
  stem: other
  start_downbeat: 17
  length_beats: 2  # una negra musical, para el stutter
  warp: {mode: resample, pitch_semitones: -0.35}  # su tempo local es 240.0: 12*log2(240/244.9) lo iguala a c1
  fade_ms: 6
tracks:
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
    steps_per_bar: 8
    bars: 2
    lanes:
      kick: {{sample: "{KICK}", steps: "x...x...x...x...", gain_db: -4}}
- id: drums
  at_bar: 9
  pattern:
    steps_per_bar: 8
    bars: 2
    lanes:
      clap: {{sample: "{CLAP}", steps: "....x.......x...", gain_db: -4}}
      ohat: {{sample: "{OHAT}", steps: "..x...x...x...x.", gain_db: -9}}
- id: bass
  at_bar: 9
  instrument: {{plugin: synth-bass}}
  notes:  # octavas cortas en contratiempo
{mac_bass}
  fx:
  - {{id: sc, type: sidechain, key_track: kick, depth: 0.5, release_ms: 90}}
  gain_db: 0
automation:
- {{target: tracks.sample.fx.lp.cutoff_hz, curve: exp, points: [[1, 400], [8, 2500], [9, 18000]]}}  # sube y abre de golpe en el drop
- {{target: tracks.sample.fx.hp.cutoff_hz, curve: exp, points: [[23, 200], [25, 900], [25, 200]]}}  # barrido high-pass antes de la ultima vuelta
"""
    + MASTER
)

# ------------------------------------------------------------------ Yung Bae
yb_pat = [
    (1.5, "G1", 0.35, 100),
    (2.0, "G2", 0.25, 80),
    (2.5, "G1", 0.35, 96),
    (3.5, "D2", 0.25, 100),
    (3.75, "F2", 0.25, 84),
    (4.5, "G1", 0.35, 96),
]
yb_bass = notes(5, 12, 2, yb_pat) + "\n" + notes(15, 16, 2, yb_pat)
yungbae = (
    tiger(
        "252.64",
        "igual que la base: sensacion 126.3, afinado",
        "G",
        "igual que la base",
        "tiger-funk-yungbae",
    )
    + f"""chops:
- id: c1
  source: src1
  stem: other
  start_downbeat: 17
  length_beats: 16
  warp: {{mode: resample, pitch_semitones: 0}}
  fade_ms: 8
tracks:
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
  until_bar: 25  # sale en el break (compases musicales 13 y 14)
  pattern:
    steps_per_bar: 8
    bars: 2
    lanes:
      kick: {{sample: "{KICK}", steps: "x...x...x...x...", gain_db: -4}}
- id: kick-back
  at_bar: 29  # vuelve en los ultimos 2 compases musicales
  pattern:
    steps_per_bar: 8
    bars: 2
    lanes:
      kick: {{sample: "{KICK}", steps: "x...x...x...x...", gain_db: -4}}
- id: drums
  at_bar: 9
  until_bar: 25
  pattern:
    steps_per_bar: 8
    bars: 2
    swing: 0.2  # semicorcheas apenas arrastradas: groove
    lanes:
      clap: {{sample: "{CLAP}", steps: "....x.......x...", gain_db: -5}}
      chat: {{sample: "{CHAT}", steps: "xoxoxoxoxoxoxoxo", gain_db: -14}}
      ohat: {{sample: "{OHAT}", steps: "..x...x...x...x.", gain_db: -10}}
      perc: {{sample: "{COWB}", steps: "...........o..o.", gain_db: -16}}
- id: bass
  at_bar: 9
  instrument: {{plugin: finger-bass}}  # slap-bass da avisos `click` en estas notas cortas (ver styles.md)
  notes:  # linea con octava y notas de paso (Sol, Re, Fa); calla en el break
{yb_bass}
  fx:
  - {{id: sc, type: sidechain, key_track: kick, depth: 0.5, release_ms: 90}}
  gain_db: 10
automation:
- {{target: tracks.sample.fx.lp.cutoff_hz, curve: exp, points: [[1, 600], [9, 18000], [25, 18000], [29, 900], [29, 18000]]}}  # intro y break filtrados
"""
    + MASTER
)

# ------------------------------------------------------------------ Night Tempo
nighttempo = (
    tiger(
        "252.64",
        "igual que la base: sensacion 126.3, afinado",
        "G",
        "igual que la base",
        "tiger-funk-nighttempo",
    )
    + f"""chops:
- id: c1
  source: src1
  stem: mix  # el disco entero: su bateria y su bajo quedan
  start_downbeat: 17
  length_beats: 32  # 4 compases musicales: la frase se reconoce
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
  at_bar: 9
  pattern:
    steps_per_bar: 8
    bars: 2
    lanes:
      kick: {{sample: "{KICK}", steps: "x...x...x...x...", gain_db: -4}}
- id: drums
  at_bar: 17
  pattern:
    steps_per_bar: 8
    bars: 2
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

# ------------------------------------------------------------------ normal-grid template
tmpl_bass = notes(
    5,
    16,
    1,
    [(1.5, "A1", 0.4, 100), (2.5, "A1", 0.4, 92), (3.5, "A2", 0.4, 100), (4.5, "A1", 0.4, 92)],
)
template = (
    f"""schema_version: 0
# PLANTILLA future funk, grilla normal: un disco que el tracker mide entre ~95 y ~140 BPM.
# Cambia: source y chop (source.find_chops + chops.choose), meta.bpm (calculo de
# resample en recipes/future-funk.md), meta.key, la tonica del bajo (A) y los gain_db.
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
  start_downbeat: 17
  length_beats: 8  # 2 compases
  warp: {{mode: resample, pitch_semitones: 0}}
  fade_ms: 8
tracks:
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
  notes:  # contratiempos: tonica, tonica, octava, tonica
{tmpl_bass}
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
    print(name, len(text.splitlines()))
