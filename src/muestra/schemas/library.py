"""Sample-library contracts: ``.muestra-index.json``, search hits and
starter-kit manifests.

The index file lives INSIDE each library root as
``.muestra-index.json`` (never in the project). ``library.index``
writes it; ``muestra.library.core.lookup`` and the song-validation
sample-warp check read it.

A sample with a known ``bpm`` may be warped like a chop; a sample with
no ``bpm`` (one-shots, plain project paths) plays at native speed — and
asking for a warp on it is a validation error.

WP4 extension (see docs/decisions.md, 2026-11-17 WP4 entry): entries
carry measured audio features so an agent that cannot hear can still
pick a kick in musical terms — loud vs quiet, punchy vs boomy, bright vs
dark, tonal vs noisy. Every field added on top of the WP0 shape
(``path``/``bpm``/``key``/``type``/``tags``) is optional, so a minimal
hand-written index still validates. Field semantics and thresholds are
documented in docs/library.md.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, Field, model_serializer, model_validator
from pydantic_core import PydanticCustomError

from ._base import ContractModel, KeyStr, LaneName, RelPath

__all__ = [
    "ANALYZER_VERSION",
    "STARTER_KITS_FILE",
    "AudioFeatures",
    "IndexProblem",
    "KitLicense",
    "KitPattern",
    "LibraryHit",
    "LibraryIndex",
    "LibraryIndexEntry",
    "StarterKit",
    "StarterKits",
]

#: name of the starter-kit manifest inside a library root.
STARTER_KITS_FILE = "starter-kits.json"

#: bumped whenever the measuring/classifying algorithms change in a way
#: that makes older index entries stale; ``library.index`` re-probes
#: everything when the stored version differs.
ANALYZER_VERSION = 2


class AudioFeatures(ContractModel):
    """Descriptive audio features — the agent's ears.

    Frequencies in Hz, times in ms. ``sub``/``low``/``mid``/``high``/
    ``air`` are the SHARE (0..1) of the file's spectral energy in
    20-120 / 120-400 / 400-2000 / 2000-8000 / 8000+ Hz; they sum to ~1.
    ``tonality`` is the fraction of spectral energy concentrated in a few
    narrow peaks (1 = pure tone, ~0 = noise). Attack/decay are only
    measured for files up to a few seconds long.
    """

    fundamental_hz: float | None = Field(
        default=None,
        gt=0,
        description="Dominant low-frequency pitch of the body (30-300 Hz): kick/tom/bass tuning.",
    )
    note: str | None = Field(
        default=None, description="Nearest note name of fundamental_hz, e.g. 'F#1'."
    )
    attack_ms: float | None = Field(
        default=None, ge=0, description="Envelope rise time, 10% -> 90% of peak (small = clicky)."
    )
    decay_ms: float | None = Field(
        default=None, ge=0, description="Time from the peak until the envelope is 30 dB down."
    )
    spectral_centroid_hz: float | None = Field(
        default=None, ge=0, description="Brightness: energy-weighted mean frequency."
    )
    sub: float | None = Field(default=None, ge=0, le=1, description="Share of energy 20-120 Hz.")
    low: float | None = Field(default=None, ge=0, le=1, description="Share of energy 120-400 Hz.")
    mid: float | None = Field(default=None, ge=0, le=1, description="Share of energy 400-2000 Hz.")
    high: float | None = Field(default=None, ge=0, le=1, description="Share of energy 2-8 kHz.")
    air: float | None = Field(default=None, ge=0, le=1, description="Share of energy above 8 kHz.")
    tonality: float | None = Field(
        default=None, ge=0, le=1, description="0 = noise .. 1 = pure harmonic tone."
    )


class LibraryIndexEntry(ContractModel):
    """One indexed file inside a library root."""

    path: RelPath = Field(description="Path relative to the library root.")
    type: str | None = Field(
        default=None,
        description="Instrument class: kick|snare|clap|chat|ohat|hat|tom|cym|rim|perc|"
        "drums|fill|bass|keys|synth|guitar|pad|vox|fx|other. "
        "(Hand-written indexes may use any string.)",
    )
    subtype: str | None = Field(
        default=None, description="Finer class, e.g. conga|cowbell|crash|ride|roll."
    )
    kind: Literal["one-shot", "loop", "phrase"] | None = Field(
        default=None,
        description="one-shot = a single hit; loop = repeats seamlessly on a "
        "grid; phrase = a tempo-bound one-off figure (drum fill, roll).",
    )
    kit: str | None = Field(
        default=None,
        description="Kit (source pack): the folder under kits/, else the top-level folder.",
    )
    bpm: float | None = Field(
        default=None,
        gt=0,
        description="Detected/tagged source BPM; null = no warp possible.",
    )
    bpm_source: Literal["name", "detected", "name+detected"] | None = None
    bpm_confidence: float | None = Field(default=None, ge=0, le=1)
    key: KeyStr | None = None
    key_confidence: float | None = Field(default=None, ge=0, le=1)
    duration_s: float | None = Field(default=None, ge=0)
    sample_rate: int | None = Field(default=None, gt=0)
    bit_depth: int | None = Field(default=None, gt=0)
    channels: int | None = Field(default=None, gt=0)
    peak_dbfs: float | None = None
    rms_dbfs: float | None = None
    loudness_lufs: float | None = Field(
        default=None, description="Integrated loudness estimate (ITU-R BS.1770 K-weighted)."
    )
    clipping: bool = Field(
        default=False, description="Has runs of consecutive full-scale samples (hard clipping)."
    )
    features: AudioFeatures | None = None
    tags: list[str] = Field(default_factory=list)
    flags: list[str] = Field(
        default_factory=list,
        description="Heads-ups: 'clipping', 'low-level', 'type-mismatch:...', "
        "'bpm-disagreement:...', 'low-confidence-key', 'type-inferred', ...",
    )
    # Incremental-index bookkeeping.
    size: int | None = Field(default=None, ge=0)
    mtime_ns: int | None = None
    sig: str | None = Field(
        default=None, description="Cheap content hash (size + first/last 64 KiB)."
    )


class KitLicense(ContractModel):
    """Per-kit licence summary read from the kit's LICENSE.txt."""

    path: RelPath = Field(description="Kit directory relative to the library root.")
    summary: str = Field(default="", description="Condensed terms from LICENSE.txt.")
    restrictions: list[str] = Field(
        default_factory=list,
        description="Machine-readable limits: 'no-redistribution', 'no-ai-training', "
        "'no-resale', 'non-commercial'.",
    )
    source: str | None = Field(default=None, description="Source pack/page named in the licence.")


class IndexProblem(ContractModel):
    """A file that could not be indexed (reported, never fatal)."""

    path: str = Field(description="Path relative to the library root.")
    message: str


class LibraryIndex(ContractModel):
    """``.muestra-index.json`` inside a library root."""

    schema_version: Literal[0] = 0
    analyzer_version: int | None = Field(
        default=None,
        description="Version of the measuring/classifying code that wrote the entries.",
    )
    indexed_at: AwareDatetime | None = None
    generator: str | None = Field(default=None, description="e.g. 'muestra 0.0.0'.")
    kits: dict[str, KitLicense] = Field(
        default_factory=dict, description="Kit name -> licence info read from <kit>/LICENSE.txt."
    )
    problems: list[IndexProblem] = Field(default_factory=list)
    entries: list[LibraryIndexEntry] = Field(default_factory=list)


class LibraryHit(ContractModel):
    """A library lookup/search result."""

    uri: str = Field(description="library://<path>")
    root: str = Field(
        description="The library root this hit lives in, as configured (may be absolute)."
    )
    path: RelPath = Field(description="Path relative to its library root.")
    type: str | None = Field(default=None, description="e.g. kick/snare/clap/chat/ohat/drums.")
    subtype: str | None = None
    kind: Literal["one-shot", "loop", "phrase"] | None = None
    kit: str | None = None
    bpm: float | None = None
    key: KeyStr | None = None
    duration_s: float | None = None
    loudness_lufs: float | None = None
    clipping: bool = False
    tags: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    features: AudioFeatures | None = Field(
        default=None, description="Full measured features (search detail='full')."
    )
    license: KitLicense | None = None
    score: float | None = Field(default=None, description="Search rank score (0..1).")
    reasons: list[str] = Field(
        default_factory=list, description="Why this hit matched / ranked here."
    )
    summary: str | None = Field(
        default=None,
        description="One-line plain-language description of the sound "
        "(type, length, pitch, decay, brightness, character).",
    )

    @model_serializer(mode="wrap")
    def _omit_empty(self, handler: Any) -> dict[str, Any]:
        """Serialise without null/empty/false fields: search results go into an
        LLM context, so absent keys mean "default" (no bpm, no flags, not clipped)."""
        data = handler(self)
        keep = {"uri", "root", "path"}
        return {
            k: v for k, v in data.items() if k in keep or not (v is None or v is False or v == [])
        }


class KitPattern(ContractModel):
    """A named starter pattern of a kit (16 steps per bar, step chars as in song.yaml)."""

    description: str = ""
    bars: int = Field(default=1, ge=1, le=16)
    steps: dict[LaneName, str] = Field(
        min_length=1,
        description="Lane -> step string, 16 per bar (spaces ignored); lanes not listed "
        "are left out of this pattern.",
    )

    @model_validator(mode="after")
    def _lengths(self) -> KitPattern:
        for lane, steps in self.steps.items():
            n = len(steps.replace(" ", ""))
            if n != 16 * self.bars:
                raise PydanticCustomError(
                    "pattern_length",
                    "Pattern lane {lane}: {n} steps, expected {want} (16 per bar).",
                    {"lane": lane, "n": n, "want": 16 * self.bars, "hint": "Fix the step string."},
                )
        return self


class StarterKit(ContractModel):
    """One named drum kit: lane -> library-root-relative sample path."""

    description: str = ""
    lanes: dict[LaneName, RelPath] = Field(
        min_length=1, description="Lane name -> sample path relative to the library root."
    )
    notes: str | None = None
    patterns: dict[str, KitPattern] = Field(
        default_factory=dict,
        description="Named starter patterns (WP12): 'basic' (the default), 'busy', 'fill', "
        "'phrase'... Each lane must be one of the kit's lanes.",
    )

    @model_validator(mode="after")
    def _pattern_lanes(self) -> StarterKit:
        for name, pattern in self.patterns.items():
            unknown = set(pattern.steps) - set(self.lanes)
            if unknown:
                raise PydanticCustomError(
                    "ref_not_found",
                    "Pattern {name} uses lanes the kit does not have: {unknown}.",
                    {"name": name, "unknown": sorted(unknown), "hint": "Use the kit's lanes."},
                )
        return self


class StarterKits(ContractModel):
    """``starter-kits.json`` inside a library root."""

    schema_version: Literal[0] = 0
    note: str | None = Field(
        default=None, description="Free-text provenance/caveat for the whole manifest."
    )
    kits: dict[str, StarterKit] = Field(default_factory=dict)
