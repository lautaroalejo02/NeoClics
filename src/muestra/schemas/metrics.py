"""renders/vNNN/metrics.json contract (spec section 10) — the agent's "ears".

One metrics file per render version. Covers every bullet of spec 10:
loudness/true-peak/clipping, per-band spectral balance (mix + tracks),
low-end masking, transient/grid alignment, tonal coherence, click
detection, and optional style similarity. ``alarms`` gives the agent a
ready-made list of things to look at; metrics are alarms, not ears.

WP6 extended the contract ADDITIVELY (docs/decisions.md, 2026-11-17 WP6):
every pre-existing field keeps its name, type and meaning; new fields
all have defaults, so older metrics.json files still validate. Methods,
thresholds and detection limits are documented in ``docs/ears.md``.

Severity vocabulary: ``info`` < ``warn`` < ``error``. ``error`` is the
"problem" level (something is very probably audibly wrong).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ._base import ContractModel, IdStr, KeyStr, VersionStr
from .analysis import KeyEstimate

__all__ = [
    "Metrics",
    "Loudness",
    "SpectralBalance",
    "SpectralReference",
    "SpectralReport",
    "LowEndMasking",
    "LowEndCollision",
    "LowEndPair",
    "SidechainCheck",
    "TrackAlignment",
    "AlignmentEvent",
    "AlignmentReport",
    "ClipOffset",
    "TonalReport",
    "TrackTonal",
    "DetuneCheck",
    "BassVsSample",
    "ClickEvent",
    "ClickReport",
    "TrackLevel",
    "TechnicalReport",
    "StyleScore",
    "StyleSimilarity",
    "Alarm",
    "AlarmLocation",
    "MetricsDelta",
    "AlarmRef",
    "BAND_EDGES_HZ",
    "DETAIL_BAND_EDGES_HZ",
]

#: fixed spectral bands used by SpectralBalance (Hz).
BAND_EDGES_HZ: dict[str, tuple[float, float]] = {
    "low": (20.0, 250.0),
    "mid": (250.0, 4000.0),
    "high": (4000.0, 20000.0),
}

#: finer bands (WP6) — they nest exactly inside BAND_EDGES_HZ:
#: low = sub + bass, mid = low_mid + mid + high_mid, high = presence + air.
DETAIL_BAND_EDGES_HZ: dict[str, tuple[float, float]] = {
    "sub": (20.0, 60.0),
    "bass": (60.0, 250.0),
    "low_mid": (250.0, 500.0),
    "mid": (500.0, 2000.0),
    "high_mid": (2000.0, 4000.0),
    "presence": (4000.0, 8000.0),
    "air": (8000.0, 20000.0),
}

Severity = Literal["info", "warn", "error"]


# ---------------------------------------------------------------------------
# loudness
# ---------------------------------------------------------------------------


class Loudness(ContractModel):
    integrated_lufs: float = Field(description="ITU-R BS.1770-4 gated integrated loudness.")
    true_peak_dbtp: float = Field(description="4x-oversampled true peak (BS.1770-4 Annex 2).")
    clipping: bool = Field(description="True when the mix has flat-topped full-scale runs.")
    clipped_samples: int = Field(default=0, ge=0, description="Samples at |x| >= 0.9999.")
    # --- WP6 additions ---
    sample_peak_dbfs: float | None = None
    short_term_max_lufs: float | None = Field(
        default=None, description="Max short-term (3 s) loudness; None if render < 3 s."
    )
    lra_lu: float | None = Field(
        default=None,
        description="Loudness range (EBU Tech 3342) in LU; None if render < 3 s. "
        "Loops legitimately have a small LRA: reported, never alarmed.",
    )
    target_lufs: float | None = Field(default=None, description="master.target_lufs, when set.")
    target_delta_lu: float | None = Field(
        default=None, description="integrated_lufs - target_lufs."
    )
    plr_db: float | None = Field(
        default=None,
        description="Peak-to-loudness ratio: true_peak_dbtp - integrated_lufs. "
        "Low values mean an over-compressed (brick-walled) master.",
    )
    crest_factor_db: float | None = Field(
        default=None, description="20*log10(sample peak / RMS) of the mix."
    )


# ---------------------------------------------------------------------------
# spectral
# ---------------------------------------------------------------------------


class SpectralBalance(ContractModel):
    """Mean band energy in dB for the fixed bands (see BAND_EDGES_HZ)."""

    low_db: float = Field(description="20-250 Hz")
    mid_db: float = Field(description="250-4000 Hz")
    high_db: float = Field(description="4000-20000 Hz")


class SpectralReference(ContractModel):
    """Reference spectral tilt the mix is compared against (NOT flat).

    ``levels_db`` gives the expected level of each detail band relative
    to the others (any common offset; it is normalised to shares of the
    total power before comparing). Configurable per project in
    ``ears.toml`` (docs/ears.md).
    """

    name: str = Field(min_length=1)
    levels_db: dict[str, float] = Field(description="Detail band -> relative expected level, dB.")
    tolerance_db: dict[str, float] = Field(
        description="Detail band -> allowed |deviation| before a warn, dB."
    )


class SpectralReport(ContractModel):
    bands_hz: dict[str, tuple[float, float]] = Field(default_factory=lambda: dict(BAND_EDGES_HZ))
    mix: SpectralBalance
    tracks: dict[IdStr, SpectralBalance] = Field(default_factory=dict)
    # --- WP6 additions ---
    detail_bands_hz: dict[str, tuple[float, float]] = Field(
        default_factory=lambda: dict(DETAIL_BAND_EDGES_HZ)
    )
    mix_detail_db: dict[str, float] = Field(
        default_factory=dict,
        description="Detail band -> share of the mix's total power, dB (<= 0).",
    )
    tracks_detail_db: dict[IdStr, dict[str, float]] = Field(
        default_factory=dict,
        description="Per track stem: detail band -> band power, dB (absolute, "
        "comparable across tracks).",
    )
    reference: SpectralReference | None = None
    deviation_db: dict[str, float] = Field(
        default_factory=dict,
        description="Detail band -> mix share minus reference share, dB "
        "(positive = more energy than the reference).",
    )


# ---------------------------------------------------------------------------
# low end
# ---------------------------------------------------------------------------


class LowEndCollision(ContractModel):
    """A kick hit where another track is about as loud as the kick in 40-150 Hz."""

    at_s: float = Field(ge=0.0)
    bar: int = Field(ge=1)
    beat: float = Field(ge=1.0, description="1-indexed beat inside the bar.")
    track: IdStr = Field(description="Track whose low band collides with the kick.")
    kick_db: float = Field(description="Kick level in the band during the hit window.")
    track_db: float = Field(description="Other track's level in the same window.")
    margin_db: float = Field(description="track_db - kick_db (>= -collision margin).")


class LowEndPair(ContractModel):
    """Per-track summary of the kick-vs-track low-band comparison."""

    track: IdStr
    kick_hits: int = Field(ge=0)
    collisions: int = Field(ge=0)
    median_margin_db: float = Field(description="Median (track - kick) dB over all kick hits.")


class SidechainCheck(ContractModel):
    """Measured ducking of a sidechained track stem around its key triggers."""

    track: IdStr
    fx_id: IdStr | None = None
    key_track: IdStr
    key_lane: IdStr | None = None
    depth: float = Field(ge=0.0, le=1.0, description="Configured fx depth.")
    release_ms: float = Field(gt=0.0, description="Configured release_ms.")
    expected_dip_db: float = Field(ge=0.0, description="-20*log10(1 - depth), capped at 40.")
    measured_dip_db: float = Field(
        description="Depth of the trigger-averaged gain dip on the stem, dB."
    )
    recovery_ms: float | None = Field(
        default=None,
        description="Time after the trigger until the averaged level is back "
        "within 1 dB of the pre-trigger level.",
    )
    n_triggers: int = Field(ge=0)
    ducking: bool = Field(description="True when the dip is clearly audible.")


class LowEndMasking(ContractModel):
    """Who owns the low end (the "el bombo se come al sample" alarm).

    ``energy_db`` maps each track id to its mean energy (dB) inside
    ``band_hz``; ``dominant_track`` is the loudest there. Track roles
    (kick/bass/sample) are identified by track id/name.
    """

    band_hz: tuple[float, float] = (40.0, 150.0)
    energy_db: dict[IdStr, float] = Field(default_factory=dict)
    dominant_track: IdStr | None = None
    # --- WP6 additions ---
    kick: str | None = Field(
        default=None, description="Kick source used, e.g. 'drums.kick' (track.lane)."
    )
    pairs: list[LowEndPair] = Field(default_factory=list)
    collisions: list[LowEndCollision] = Field(
        default_factory=list, description="Colliding kick hits (capped list, worst first)."
    )
    sidechain: list[SidechainCheck] = Field(
        default_factory=list, description="Measured ducking per sidechain fx in the song."
    )


# ---------------------------------------------------------------------------
# alignment
# ---------------------------------------------------------------------------


class AlignmentEvent(ContractModel):
    """A transient notably off the grid."""

    at_s: float = Field(ge=0.0)
    bar: int = Field(ge=1)
    beat: float = Field(ge=1.0)
    offset_ms: float = Field(description="Detected onset minus grid/expected time, ms.")
    clip: str | None = Field(default=None, description="Clip ref, e.g. 'tracks.sample.clips[0]'.")


class ClipOffset(ContractModel):
    """Median onset offset of one clip instance (clip tracks)."""

    clip: str = Field(description="Clip ref, e.g. 'tracks.sample.clips[0]'.")
    repeat_index: int = Field(ge=0, description="0-based repeat instance.")
    chop: IdStr | None = None
    at_s: float = Field(ge=0.0)
    bar: int = Field(ge=1)
    offset_ms: float
    n_onsets: int = Field(ge=0)


class TrackAlignment(ContractModel):
    """How far this track's transients sit off the project grid."""

    mean_offset_ms: float
    max_abs_offset_ms: float = Field(ge=0.0)
    n_transients: int = Field(ge=0)
    # --- WP6 additions ---
    basis: Literal["events", "beats"] = Field(
        default="beats",
        description="'events' = matched to the song's own hits/notes; 'beats' = "
        "onsets within 1/8 beat of a quarter note (clip tracks).",
    )
    median_offset_ms: float = 0.0
    mad_ms: float = Field(default=0.0, ge=0.0, description="Median absolute deviation, ms.")
    p90_abs_offset_ms: float = Field(default=0.0, ge=0.0)
    drift_ms: float = Field(
        default=0.0,
        description="Fitted offset change across one clip instance (or the render), ms.",
    )
    pulse_share: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Fraction of quarter notes that carry an onset (clip tracks). "
        "Low = no clear pulse: offsets are not trusted.",
    )
    offbeat_offset_ms: float | None = Field(
        default=None,
        description="Median offset of onsets near the off-beat 16ths (swing), ms.",
    )
    histogram_ms: dict[str, int] = Field(
        default_factory=dict,
        description="Offset distribution vs the nearest 16th: bin label -> count.",
    )
    flagged: list[AlignmentEvent] = Field(default_factory=list)
    clips: list[ClipOffset] = Field(default_factory=list)


class AlignmentReport(ContractModel):
    tracks: dict[IdStr, TrackAlignment] = Field(default_factory=dict)
    max_abs_offset_ms: float = Field(ge=0.0)
    # --- WP6 additions ---
    lanes: dict[str, TrackAlignment] = Field(
        default_factory=dict, description="Per drum lane, keyed '<track>.<lane>'."
    )


# ---------------------------------------------------------------------------
# tonal
# ---------------------------------------------------------------------------


class TrackTonal(ContractModel):
    key: KeyEstimate | None = None
    tuning_cents: float | None = Field(
        default=None, description="Deviation of the track's partials from A440 12-TET, cents."
    )
    out_of_key_share: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Share of chroma energy outside the target key's scale.",
    )
    best_shift_semitones: int | None = Field(
        default=None,
        description="Transposition (-6..+6) that best fits the target key; 0 = in key.",
    )


class DetuneCheck(ContractModel):
    """Pitch of a resampled chop vs the 12-TET grid.

    ``expected_shift_semitones`` = 12*log2(project_bpm/local_bpm) +
    warp.pitch_semitones (from the song snapshot + analysis.json);
    ``expected_detune_cents`` is its distance to the nearest semitone.
    ``measured_cents`` is measured on the rendered stem relative to
    ``reference_cents`` (the tuning of the other tonal tracks, 0 = A440).
    """

    track: IdStr
    chop: IdStr
    mode: Literal["resample", "stretch"]
    local_bpm: float | None = None
    expected_shift_semitones: float | None = None
    expected_detune_cents: float | None = None
    measured_cents: float | None = None
    reference_cents: float = 0.0
    relative_cents: float | None = Field(
        default=None, description="measured_cents - reference_cents (what is heard)."
    )


class BassVsSample(ContractModel):
    """Bass notes (from the song) against the sample's measured pitch content."""

    bass_track: IdStr
    sample_track: IdStr
    fit: float = Field(description="Correlation of bass pitch classes with the sample chroma.")
    best_shift_semitones: int = Field(description="Transposition of the bass that fits best.")
    best_fit: float
    clash_share: float = Field(
        ge=0.0,
        le=1.0,
        description="Share of bass note time on pitch classes a semitone from a "
        "strong sample pitch class and absent from it.",
    )


class TonalReport(ContractModel):
    estimated: KeyEstimate | None = None
    target_key: KeyStr | None = Field(
        default=None, description="meta.key from song.yaml, normalized."
    )
    tonic_match: bool | None = None
    mode_match: bool | None = None
    in_key: bool | None = Field(
        default=None,
        description="True iff estimated tonic and mode both match target_key.",
    )
    # --- WP6 additions ---
    compatible: bool | None = Field(
        default=None,
        description="True when the estimate shares the target's scale "
        "(same key or its relative major/minor).",
    )
    source: Literal["tonal_stems", "mix"] | None = Field(
        default=None, description="What 'estimated' was computed from."
    )
    tracks: dict[IdStr, TrackTonal] = Field(default_factory=dict)
    detune: list[DetuneCheck] = Field(default_factory=list)
    bass_vs_sample: list[BassVsSample] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# clicks
# ---------------------------------------------------------------------------


class ClickEvent(ContractModel):
    """A suspected click/discontinuity at a chop or clip edge."""

    track: IdStr | None = None
    at_s: float = Field(ge=0.0)
    severity: float = Field(ge=0.0, le=1.0)
    # --- WP6 additions ---
    bar: int | None = Field(default=None, ge=1)
    beat: float | None = Field(default=None, ge=1.0)
    edge: Literal["clip_start", "clip_end", "loop_seam", "other"] | None = None
    clip: str | None = Field(default=None, description="Clip ref, e.g. 'tracks.sample.clips[0]'.")
    chop: IdStr | None = None
    ratio: float | None = Field(
        default=None, description="Discontinuity size vs local signal activity."
    )
    level_db: float | None = Field(default=None, description="Size of the jump, dBFS.")


class ClickReport(ContractModel):
    count: int = Field(ge=0)
    events: list[ClickEvent] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# technical extras (WP6)
# ---------------------------------------------------------------------------


class TrackLevel(ContractModel):
    peak_dbfs: float
    rms_dbfs: float
    dc_offset_db: float
    audible_share: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Fraction of the track's active frames where it is within "
        "the audibility margin of the rest of the mix in some band.",
    )
    muted: bool = False


class TechnicalReport(ContractModel):
    """Mono compatibility, DC offset, silent/inaudible tracks."""

    dc_offset_db: float = Field(description="Worst-channel DC offset of the mix, dBFS.")
    stereo_correlation: float = Field(ge=-1.0, le=1.0)
    low_band_correlation: float = Field(
        ge=-1.0, le=1.0, description="L/R correlation below 150 Hz (mono compatibility)."
    )
    low_band_mono_loss_db: float = Field(
        description="Level lost below 150 Hz when summed to mono (0 = none)."
    )
    tracks: dict[IdStr, TrackLevel] = Field(default_factory=dict)
    silent_tracks: list[IdStr] = Field(default_factory=list)
    inaudible_tracks: list[IdStr] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# style (hook only in v1)
# ---------------------------------------------------------------------------


class StyleScore(ContractModel):
    reference: str = Field(description="Reference identifier, e.g. a path or name.")
    score: float = Field(ge=0.0, le=1.0)


class StyleSimilarity(ContractModel):
    """Optional (spec 10): CLAP/MERT similarity against reference tracks."""

    model: str = Field(min_length=1, description="e.g. 'laion-clap', 'mert'.")
    references: list[StyleScore] = Field(default_factory=list)
    mean_score: float | None = Field(default=None, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# alarms
# ---------------------------------------------------------------------------


class AlarmLocation(ContractModel):
    at_s: float = Field(ge=0.0)
    bar: int = Field(ge=1, description="1-indexed song bar.")
    beat: float = Field(ge=1.0, description="1-indexed beat inside the bar.")


class Alarm(ContractModel):
    """A computed heads-up for the agent ("metrics are alarms").

    ``message`` is a one-line explanation in Spanish, in musical terms;
    ``fix`` a concrete suggested song.yaml change. ``error`` = problem.
    """

    code: str = Field(min_length=1, description="Stable machine code, e.g. 'clipping'.")
    severity: Severity
    message: str = Field(min_length=1)
    track: IdStr | None = None
    at_s: float | None = Field(default=None, ge=0.0)
    # --- WP6 additions ---
    bar: int | None = Field(default=None, ge=1, description="1-indexed song bar of at_s.")
    beat: float | None = Field(default=None, ge=1.0, description="1-indexed beat in the bar.")
    lane: IdStr | None = None
    clip: str | None = Field(default=None, description="Clip ref, e.g. 'tracks.sample.clips[0]'.")
    chop: IdStr | None = None
    value: float | None = Field(default=None, description="Measured value.")
    threshold: float | None = Field(default=None, description="Threshold that was crossed.")
    unit: str | None = Field(default=None, description="Unit of value/threshold, e.g. 'ms'.")
    count: int | None = Field(default=None, ge=1, description="Occurrences, when repeated.")
    where: list[AlarmLocation] = Field(
        default_factory=list, description="Some occurrences (capped), earliest first."
    )
    fix: str | None = Field(default=None, description="Suggested song.yaml change.")


class Metrics(ContractModel):
    schema_version: Literal[0] = 0
    version: VersionStr
    loudness: Loudness
    spectral: SpectralReport
    low_end_masking: LowEndMasking
    alignment: AlignmentReport
    tonal: TonalReport
    clicks: ClickReport
    style_similarity: StyleSimilarity | None = None
    alarms: list[Alarm] = Field(default_factory=list)
    # --- WP6 additions ---
    technical: TechnicalReport | None = None
    summary: str = Field(
        default="",
        description="Compact Spanish summary (a handful of lines) for inline "
        "display in the render tool result; details live in the other fields.",
    )
    skipped: list[str] = Field(
        default_factory=list,
        description="Checks that could not run on this render, and why.",
    )
    analysis_time_s: float | None = Field(default=None, ge=0.0)
    ears_version: str | None = Field(default=None, description="Analyzer version string.")


# ---------------------------------------------------------------------------
# compare (ears.compare)
# ---------------------------------------------------------------------------


class AlarmRef(ContractModel):
    code: str
    severity: Severity
    track: IdStr | None = None
    message: str


class MetricsDelta(ContractModel):
    """Difference between two versions' metrics (b relative to a)."""

    version_a: VersionStr
    version_b: VersionStr
    lufs_delta: float | None = Field(default=None, description="b - a, integrated LUFS.")
    true_peak_delta_db: float | None = None
    plr_delta_db: float | None = None
    loudness: Literal["louder", "quieter", "same"] | None = None
    low_delta_db: float | None = Field(
        default=None, description="Change of the 20-250 Hz share of the mix, dB."
    )
    high_delta_db: float | None = Field(
        default=None, description="Change of the 4-20 kHz share of the mix, dB."
    )
    brightness: Literal["brighter", "darker", "same"] | None = None
    low_end: Literal["heavier", "lighter", "same"] | None = None
    track_level_delta_db: dict[IdStr, float] = Field(
        default_factory=dict, description="Per track stem RMS change, dB."
    )
    alarms_fixed: list[AlarmRef] = Field(default_factory=list)
    alarms_introduced: list[AlarmRef] = Field(default_factory=list)
    alarms_kept: list[AlarmRef] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list, description="Short Spanish notes.")
