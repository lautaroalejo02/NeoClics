"""One readable line per feedback event, for the agent (WP16).

Selection-anchored requests ("Pedile al agente" in the editor) say exactly
what the human pointed at, where, and the song.edit op/paths that act on it.
"""

from __future__ import annotations

import json
import math

from ..schemas.feedback import CommentEvent

__all__ = ["event_line", "suggested_ops"]


def _bars(sel) -> str:
    if sel.start_beat is None or "compás" in sel.label or "compases" in sel.label:
        return ""
    a = math.floor((sel.start_beat - 1) / 4) + 1
    b = math.ceil(((sel.end_beat or sel.start_beat + 1) - 1) / 4)
    return f"compás {a}" if b <= a else f"compases {a} a {b}"


def suggested_ops(e: CommentEvent) -> list[dict]:
    """The song.edit op that REMOVES/targets the selection, as a starting point."""
    sel = e.selection
    if sel is None or sel.track is None:
        return []
    ops: list[dict] = []
    for h in sel.hits:
        if h.step is not None:
            ops.append(
                {
                    "op": "hit",
                    "track": sel.track,
                    "lane": h.lane,
                    "bar": h.bar,
                    "step": h.step,
                    "velocity": None,
                }
            )
        elif h.index is not None:
            ops.append({"op": "remove", "path": f"tracks.{sel.track}.pattern.hits[{h.index}]"})
    for c in sel.clips:
        if c.instance is not None:
            ops.append(
                {
                    "op": "set",
                    "path": f"tracks.{sel.track}.clips[{c.clip}].instances.{c.instance}",
                    "value": {"mute": True},
                }
            )
        else:
            ops.append({"op": "remove", "path": f"tracks.{sel.track}.clips[{c.clip}]"})
    for n in sorted(sel.notes, key=lambda n: -n.index):
        if n.phrase is None:
            ops.append({"op": "remove", "path": f"tracks.{sel.track}.notes[{n.index}]"})
    return ops


def event_line(e) -> str:
    ts = e.ts.strftime("%Y-%m-%d %H:%M:%S")
    if e.type == "rating":
        return f"{ts} rating {e.version}: {'me gusta' if e.value == 'like' else 'no me gusta'}"
    if e.type == "ab_choice":
        return f"{ts} A/B {e.a} vs {e.b}: eligió {e.winner}"
    if e.type == "chop_choice":
        return f"{ts} eligió el chop {e.candidate_id} (ya está en song.yaml)"
    where = []
    if e.version:
        where.append(e.version)
    if e.at_s is not None:
        where.append(f"{e.at_s:.1f} s")
    sel = e.selection
    if sel is None:
        if e.track:
            where.append(f"track {e.track}")
        return f'{ts} comentario [{" · ".join(where)}]: "{e.text}"'
    where.append(sel.label)
    if bars := _bars(sel):
        where.append(bars)
    line = f'{ts} PEDIDO SOBRE UNA SELECCIÓN [{" · ".join(where)}]: "{e.text}"'
    parts = []
    if sel.paths:
        parts.append("apunta a " + ", ".join(sel.paths[:8]) + (" …" if len(sel.paths) > 8 else ""))
    if e.song_revision:
        parts.append(f"canción en la revisión {e.song_revision} (song.state)")
    ops = suggested_ops(e)
    if ops:
        parts.append(
            "para sacar exactamente eso: song.edit ops="
            + json.dumps(ops[:6], ensure_ascii=False, separators=(",", ":"))
        )
    if sel.source_s:
        parts.append(f"fuente {sel.source} {sel.source_s[0]:.2f}-{sel.source_s[1]:.2f} s")
    if sel.take:
        parts.append(
            f"es la toma {sel.take} de los pads: leela con pads.take_get(take_id={sel.take}); "
            "para ponerla o limpiarla en la canción, pads.commit (quantize, cleanup) o song.edit "
            "sobre los golpes que ya puso"
        )
    if sel.pad:
        parts.append(
            f"es el pad {sel.pad.key} del banco {sel.pad.bank}"
            + (f" («{sel.pad.name}»)" if sel.pad.name else "")
            + ": pads.get lo muestra; cambialo con pads.save_banks"
        )
    return line + ("\n    " + "; ".join(parts) if parts else "")
