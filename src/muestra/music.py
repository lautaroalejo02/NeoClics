"""Shared music helpers: canonical keys and MIDI note names.

Used by the schemas (``KeyStr``, ``MidiNote``) and by WP4 (library) and
WP6 (ears) so every WP parses keys the same way.

Canonical key form: ``<A-G letter><optional #/b>`` + ``m`` for minor,
nothing for major — e.g. ``F#m`` (F-sharp minor), ``F#`` (F-sharp major),
``Bb`` (B-flat major). Accepted input forms: ``F#m``, ``F#min``,
``F# minor``, ``F#`` / ``F#M`` / ``F#maj`` / ``F# major``, and a bare
lowercase letter (``a`` = A minor, classical convention). Single-letter
mode suffixes are case-sensitive where it matters: ``M`` means MAJOR,
``m`` means minor; multi-letter suffixes are case-insensitive.

MIDI note names: scientific pitch notation with C4 = 60 (middle C), so
``F#2`` = 42. Accidentals ``#`` and ``b`` are handled enharmonically.
"""

from __future__ import annotations

import re
from typing import Literal

__all__ = [
    "KeyParseError",
    "Mode",
    "canonical_key",
    "parse_key",
    "note_name",
    "parse_note_name",
    "PITCH_CLASS_NAMES",
]

Mode = Literal["major", "minor"]

#: Pitch classes in semitones above C.
_LETTER_PC: dict[str, int] = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

#: Sharp-name spelling per pitch class (for note_name()).
PITCH_CLASS_NAMES: list[str] = [
    "C",
    "C#",
    "D",
    "D#",
    "E",
    "F",
    "F#",
    "G",
    "G#",
    "A",
    "A#",
    "B",
]

_KEY_RE = re.compile(r"^([A-Ga-g])([#b]?)\s*([A-Za-z]*)$")
_NOTE_RE = re.compile(r"^([A-Ga-g])([#b]?)(-?\d{1,2})$")


class KeyParseError(ValueError):
    """Raised when a key or note-name string can't be parsed."""


def parse_key(text: str) -> tuple[int, Mode]:
    """Parse a key string; returns ``(pitch_class 0..11, mode)``.

    Handles enharmonics via the pitch class: ``Bbm`` -> (10, "minor"),
    ``A#m`` -> (10, "minor") too. Raises :class:`KeyParseError` on
    anything unparseable.
    """
    m = _KEY_RE.match(text.strip())
    if not m:
        raise KeyParseError(f"Invalid key {text!r}.")
    letter, acc, suffix = m.group(1), m.group(2), m.group(3)
    pc = _LETTER_PC[letter.upper()] + {"#": 1, "b": -1, "": 0}[acc]
    pc %= 12
    sl = suffix.lower()
    mode: Mode
    if suffix == "":
        # bare letter: uppercase = major, lowercase = minor (classical).
        mode = "minor" if letter.islower() else "major"
    elif suffix == "M" or sl in ("maj", "major"):
        mode = "major"
    elif sl in ("m", "min", "minor"):
        mode = "minor"
    else:
        raise KeyParseError(f"Invalid key suffix {suffix!r} in {text!r}.")
    return pc, mode


def canonical_key(text: str) -> str:
    """Canonical spelling of ``text``: e.g. ``f#M`` -> ``F#``, ``amin`` -> ``Am``.

    The accidental is kept as written (``Bb`` is not respelled ``A#``);
    only the letter case and mode suffix are normalised.
    """
    m = _KEY_RE.match(text.strip())
    if not m:
        raise KeyParseError(f"Invalid key {text!r}.")
    letter, acc = m.group(1), m.group(2)
    _, mode = parse_key(text)
    return f"{letter.upper()}{acc}{'m' if mode == 'minor' else ''}"


def parse_note_name(text: str) -> int:
    """Parse a MIDI note name like ``F#2`` or ``Bb3`` into 0..127.

    Convention: C4 = 60 (middle C), C-1 = 0. Raises KeyParseError when
    malformed or out of MIDI range.
    """
    m = _NOTE_RE.match(text.strip())
    if not m:
        raise KeyParseError(f"Invalid note name {text!r}.")
    letter, acc, octave = m.group(1), m.group(2), int(m.group(3))
    pc = (_LETTER_PC[letter.upper()] + {"#": 1, "b": -1, "": 0}[acc]) % 12
    midi = pc + 12 * (octave + 1)
    if not 0 <= midi <= 127:
        raise KeyParseError(f"Note {text!r} = MIDI {midi} is outside 0..127.")
    return midi


def note_name(midi: int) -> str:
    """Inverse of :func:`parse_note_name` (sharp spelling): 42 -> ``F#2``."""
    if not 0 <= midi <= 127:
        raise KeyParseError(f"MIDI note {midi} is outside 0..127.")
    return f"{PITCH_CLASS_NAMES[midi % 12]}{midi // 12 - 1}"
