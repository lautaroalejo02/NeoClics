"""Performance pads (WP17): pad banks and recorded takes.

Files (beside the song, versioned with it, never rewound by a restore)::

    pads/banks.json            PadsFile: the pad banks of this project
    pads/takes/<take_id>.json  Take: one recorded performance
    pads/takes/deleted/        takes the human deleted (kept, never lost)

A pad is a sound mapped to a computer-keyboard key by PHYSICAL position
(``KeyboardEvent.code``: ``KeyZ`` is the bottom-left letter on QWERTY,
AZERTY and QWERTZ alike). A take is what was played, in beats relative to
the take's origin, AFTER latency compensation and BEFORE quantisation (the
quantise settings are stored beside the raw events and applied when the take
is read or committed, so they can change later).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, model_validator
from pydantic_core import PydanticCustomError

from ._base import ContractModel, FileRef, IdStr
from .song import LaneSlice, MidiPitch

__all__ = [
    "PAD_KEY_PATTERN",
    "PadKey",
    "PadNote",
    "Pad",
    "PadBank",
    "PadsFile",
    "QuantizeGrid",
    "TakeQuantize",
    "TakeEvent",
    "TakeCommit",
    "Take",
    "TAKE_ID_PATTERN",
]

#: physical key codes a pad may use (letters, digits and the punctuation keys
#: of the three letter rows). Digits are reserved by the UI for bank switching
#: but stay valid here (another client may map them).
PAD_KEY_PATTERN = (
    r"^(Key[A-Z]|Digit[0-9]|Comma|Period|Slash|Semicolon|Quote|BracketLeft|BracketRight"
    r"|Backslash|Minus|Equal|IntlBackslash)$"
)
PadKey = Annotated[str, Field(pattern=PAD_KEY_PATTERN)]

TAKE_ID_PATTERN = r"^t[0-9]{3,}$"
TakeId = Annotated[str, Field(pattern=TAKE_ID_PATTERN)]


class PadNote(ContractModel):
    """A pad that plays notes of an instrument track (one note or a chord)."""

    track: IdStr = Field(description="Instrument track whose instrument plays the notes.")
    pitches: list[MidiPitch] = Field(min_length=1, max_length=8)


class Pad(ContractModel):
    """One pad. The sound is exactly one of ``sample`` (a file), ``slice`` (a
    piece of a chop/source, like a slice lane) or ``note`` (instrument notes).

    ``mode``: ``oneshot`` plays the whole sound on a press; ``gate`` plays
    while the key/finger is held (the release is recorded as the hit's
    ``length_beats``). ``velocity`` is the level of a normal press and
    ``accent`` the level of an accented one (Shift held); computer keys have
    no velocity. ``choke``: pads of the same group (1-16) cut each other
    (open hat cut by the closed hat; a slice by the next one), 3 ms fade.
    ``pitch_semitones`` tunes like a sampler (higher = shorter), ``reverse``
    plays backwards. ``lane`` is the drum-lane name the pad's hits get when the
    take is committed (default: from ``name``).
    """

    key: PadKey
    name: str = Field(min_length=1, max_length=40)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    sample: FileRef | None = None
    slice: LaneSlice | None = None
    note: PadNote | None = None
    gain_db: float = Field(default=0.0, ge=-60.0, le=24.0)
    pan: float = Field(default=0.0, ge=-1.0, le=1.0)
    pitch_semitones: float = Field(default=0.0, ge=-24.0, le=24.0)
    reverse: bool = False
    choke: int | None = Field(default=None, ge=1, le=16)
    mode: Literal["oneshot", "gate"] = "oneshot"
    velocity: float = Field(default=0.8, gt=0.0, le=1.0)
    accent: float = Field(default=1.0, gt=0.0, le=1.0)
    lane: IdStr | None = None

    @model_validator(mode="after")
    def _one_sound(self) -> Pad:
        n = sum(x is not None for x in (self.sample, self.slice, self.note))
        if n != 1:
            raise PydanticCustomError(
                "pad_sound",
                "A pad needs exactly one of 'sample', 'slice' or 'note'.",
                {"hint": "Set sample: library://..., slice: {chop, length_beats} or note."},
            )
        return self


class PadBank(ContractModel):
    id: IdStr
    name: str = Field(min_length=1, max_length=40)
    pads: list[Pad] = Field(default_factory=list, max_length=48)

    @model_validator(mode="after")
    def _unique_keys(self) -> PadBank:
        seen: set[str] = set()
        for p in self.pads:
            if p.key in seen:
                raise PydanticCustomError(
                    "duplicate_id",
                    "Bank {bank} maps key {key} twice.",
                    {"bank": self.id, "key": p.key, "hint": "One pad per key in a bank."},
                )
            seen.add(p.key)
        return self


class PadsFile(ContractModel):
    """``pads/banks.json``."""

    schema_version: Literal[0] = 0
    active_bank: IdStr = "a"
    banks: list[PadBank] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def _banks(self) -> PadsFile:
        ids = [b.id for b in self.banks]
        if len(set(ids)) != len(ids):
            raise PydanticCustomError(
                "duplicate_id", "Bank ids must be unique.", {"hint": "Use a, b, c, d."}
            )
        if self.active_bank not in ids:
            raise PydanticCustomError(
                "ref_not_found",
                "active_bank {b} is not a bank id.",
                {"b": self.active_bank, "hint": "Point active_bank at one of the banks."},
            )
        return self


#: quantise grids: off, 1/16, 1/8, 1/8 triplets, 1/16 triplets
QuantizeGrid = Literal["off", "1/16", "1/8", "1/8t", "1/16t"]


class TakeQuantize(ContractModel):
    """Non-destructive quantise of a take (applied on read/commit).

    ``strength`` 0..1 moves each hit that fraction of the way to the nearest
    grid point; ``swing`` 0..1 delays the odd (off) points of a straight grid
    by ``swing * step / 3`` (the song's pattern swing; 1 = full triplet feel;
    triplet grids ignore it)."""

    grid: QuantizeGrid = "off"
    strength: float = Field(default=1.0, ge=0.0, le=1.0)
    swing: float = Field(default=0.0, ge=0.0, le=1.0)


class TakeEvent(ContractModel):
    """One press. ``beat`` is 0-based beats from the take origin as HEARD
    (latency compensated), unquantised; inside ``[0, loop_beats)`` for loop
    takes (a press a hair before the loop start is stored at the end of the
    cycle). ``cycle`` is the 1-based pass of the loop it was played in (the
    unit of "undo last pass"); linear takes use 1."""

    beat: float = Field(ge=-16.0, le=65536.0)
    pad: PadKey
    velocity: float = Field(default=0.8, gt=0.0, le=1.0)
    length_beats: float | None = Field(default=None, gt=0.0, le=64.0)
    cycle: int = Field(default=1, ge=1)


class TakeCommit(ContractModel):
    """A record of the take being turned into song material (history only)."""

    at: AwareDatetime
    track: IdStr
    at_bar: int = Field(ge=1)
    repeat: int = Field(ge=1)
    hits: int = Field(ge=0)
    notes: int = Field(default=0, ge=0)


class Take(ContractModel):
    """``pads/takes/<id>.json``: one recorded performance (WP17)."""

    schema_version: Literal[0] = 0
    id: TakeId
    name: str = Field(min_length=1, max_length=80)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    status: Literal["recording", "done"] = "done"
    bpm: float = Field(gt=0, lt=400, description="Song tempo when it was played.")
    origin_beat: float = Field(
        ge=0.0, description="0-based song beat that take beat 0 was played against."
    )
    loop_beats: float | None = Field(
        default=None, gt=0.0, le=4096.0, description="Cycle length of a loop (overdub) take."
    )
    length_beats: float = Field(gt=0.0, le=65536.0)
    bank: IdStr | None = None
    pads: dict[str, Pad] = Field(
        default_factory=dict,
        description="Snapshot of the pads played, keyed by key code: the take stays "
        "playable/committable if the bank changes later.",
    )
    events: list[TakeEvent] = Field(default_factory=list, max_length=20000)
    quantize: TakeQuantize = Field(default_factory=TakeQuantize)
    latency_ms: float = Field(
        default=0.0, ge=-1000.0, le=2000.0, description="Compensation applied (ms)."
    )
    commits: list[TakeCommit] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _pads_known(self) -> Take:
        missing = sorted({e.pad for e in self.events} - set(self.pads))
        if missing:
            raise PydanticCustomError(
                "ref_not_found",
                "Events use pads {missing} that the take's 'pads' snapshot lacks.",
                {"missing": ", ".join(missing), "hint": "Store every played pad in 'pads'."},
            )
        return self
