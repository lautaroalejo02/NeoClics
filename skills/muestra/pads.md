# Pads: lo que el humano toca con el teclado

En la vista Arreglo, el humano asigna sonidos a las teclas de la compu (tres filas: Z…,
A…, Q…; 4 bancos) y toca encima de la base, solo o sobre un loop. Cada vez que graba queda
una **toma** en `pads/takes/<id>.json`: lo que tocó, tal cual, en tiempos medidos contra lo
que escuchaba (la demora del audio ya está descontada). La cuantización se guarda aparte y
no destruye nada. Una toma **no está en la canción** hasta que alguien la pone
(`pads.commit`); entonces son golpes normales de `song.yaml` (los ves en el editor, se
renderizan, salen en el diff).

| Herramienta | Para qué |
|---|---|
| `pads.get` | bancos (`pads/banks.json`) y la lista de tomas, con cuántos golpes y qué pads |
| `pads.take_get(take_id, quantize?, cleanup?, at_bar?)` | una toma como eventos (`beat` 0-based desde el inicio de la toma, `name` del pad, `velocity`, `length_beats` si lo mantuvo apretado, `cycle` = vuelta del loop) + `text` corto para leer |
| `pads.commit(take_id, target, track?, at_bar, repeat? \| until_bar?, quantize?, cleanup?, dry_run?)` | ponerla en la canción con `song.edit` (deshacible) |
| `pads.slice(chop \| source+start_downbeat, length_beats, by=beat\|half\|transients)` | cortar un chop/región en pedazos para pads |
| `pads.from_track(track)`, `pads.save_banks` | armar pads con los sonidos de un track de batería; guardar bancos |

`target: new_track` crea un track `pads` (un lane por pad); `target: track` suma los golpes
a un track de batería existente (un pad cuyo sonido ya es un lane de ese track usa ese lane;
los otros agregan un lane). Los pads de notas (`note: {track, pitches}`) escriben `notes` del
instrumento. Lo que cae fuera (antes del inicio o después del final) se deja afuera y
`messages` lo dice.

## Cómo queda en `song.yaml`

Golpes sueltos (`pattern.hits`) en los tiempos tocados; los lanes pueden sonar un pedazo
de un chop (`slice`), afinarse como un sampler (`pitch_semitones`, más agudo = más corto),
sonar al revés (`reverse`), cortarse entre sí (`choke`: el hi-hat cerrado corta al abierto,
cada pedazo corta al anterior) y un golpe puede durar lo que se mantuvo apretado
(`length_beats`):

```yaml fragment
tracks:
- id: pads
  pattern:
    steps_per_bar: 16
    lanes:
      kick: {sample: "library://kits/tr8-hardware-machines/roland-tr-8/kick-tr-8-kick-02.wav"}
      chat: {sample: "library://kits/tr8-hardware-machines/roland-tr-8/chat-tr-8-closed-hat-01.wav", choke: 1}
      ohat: {sample: "library://kits/tr8-hardware-machines/roland-tr-8/ohat-tr-8-open-hat-01.wav", choke: 1}
      c1-3: {slice: {chop: c1, start_beats: 2, length_beats: 1}, choke: 2}
      c1-4: {slice: {chop: c1, start_beats: 3, length_beats: 1}, choke: 2, pitch_semitones: -2}
      voz: {slice: {source: src1, stem: other, start_downbeat: 3, start_beats: 0.5, length_beats: 0.5, speed: source}, reverse: true}
    hits:
    - {beat: 9, lane: kick}
    - {beat: 9.5, lane: c1-3, velocity: 0.8}
    - {beat: 10.02, lane: chat, velocity: 0.6}
    - {beat: 10.5, lane: ohat}
    - {beat: 11, lane: c1-4, length_beats: 0.75}
    - {beat: 12.25, lane: voz}
```

`slice` de un chop: `start_beats` cuenta tiempos adentro del chop y usa su stem y su warp.
De una fuente: `source` + `start_downbeat` (+ `start_beats` a lo largo de la grilla), con
`stem` y `warp` propios. `speed: project` (lo normal) lo estira al tempo del tema como un
chop de ese largo; `speed: source` lo deja a la velocidad del disco.

## Pedidos típicos

- **"Usá lo que grabé"** / "poné eso en el tema": `pads.get` → la toma más nueva (o la que
  nombró) → `pads.take_get` para ver qué es → `pads.commit(dry_run=true)` y contale dónde
  la vas a poner → `pads.commit`. Si grabó sobre un loop de 2 compases, repetila hasta
  donde tenga sentido (`until_bar`), no un solo compás. Respetá su cuantización guardada.
- **"Limpiame el ritmo que toqué"**: no reescribas el groove. `cleanup: {dedupe_ms: 30}`
  saca dobles golpes involuntarios; `quantize: {grid: "1/16", strength: 0.6}` acerca sin
  matar el feel (100% solo si lo pide "a la grilla"); `cleanup.velocity: even` empareja la
  dinámica sin borrar acentos (`fixed` la aplana). Si ya estaba en la canción, sacá esos
  golpes con `song.edit` (`remove` de `tracks.<t>.pattern.hits[i]`) y volvé a hacer
  `pads.commit` con la limpieza, o editá los `hits` directamente. Decí qué cambiaste.
- **"Armame una base para tocar encima"**: un loop corto y despejado (bombo, hats, bajo
  simple, 2-4 compases) que deje lugar; avisale que marque el loop (L) en el editor y abra
  los pads (K). Si quiere tocar con los sonidos de un track, `pads.from_track` + `pads.save_banks`.
- Un pedido con `selection.take` o `selection.pad` ("Pedile al agente" sobre una toma o un
  pad) se refiere a eso: leé la toma con `pads.take_get` antes de tocar nada.

Nunca borres tomas: son lo que tocó. `pads.take_delete` solo si lo pide (se mueven a
`pads/takes/deleted/`).
