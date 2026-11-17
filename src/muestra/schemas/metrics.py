"""renders/vNNN/metrics.json contract (spec section 10) — the agent's "ears".

One metrics file per render version. Covers every bullet of spec 10:
loudness/true-peak/clipping, per-band spectral balance (mix + tracks),
low-end masking, transient/grid alignment, tonal coherence, click
detection, and optional style similarity. ``alarms`` gives the agent a
ready-made list of things to look at; metrics are alarms, not ears.
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
    "SpectralReport",
    "LowEndMasking",
    "TrackAlignment",
    "AlignmentReport",
    "TonalReport",
    "ClickEvent",
    "ClickReport",
    "StyleScore",
    "StyleSimilarity",
    "Alarm",
    "BAND_EDGES_HZ",
]

#: fixed spectral bands used by SpectralBalance (Hz).
BAND_EDGES_HZ: dict[str, tuple[float, float]] = {
    "low": (20.0, 250.0),
    "mid": (250.0, 4000.0),
    "high": (4000.0, 20000.0),
}


class Loudness(ContractModel):
    integrated_lufs: float
    true_peak_dbtp: float
    clipping: bool
    clipped_samples: int = Field(default=0, ge=0)


class SpectralBalance(ContractModel):
    """Mean band energy in dB for the fixed bands (see BAND_EDGES_HZ)."""

    low_db: float = Field(description="20-250 Hz")
    mid_db: float = Field(description="250-4000 Hz")
    high_db: float = Field(description="4000-20000 Hz")


class SpectralReport(ContractModel):
    bands_hz: dict[str, tuple[float, float]] = Field(default_factory=lambda: dict(BAND_EDGES_HZ))
    mix: SpectralBalance
    tracks: dict[IdStr, SpectralBalance] = Field(default_factory=dict)


class LowEndMasking(ContractModel):
    """Who owns the low end (the "el bombo se come al sample" alarm).

    ``energy_db`` maps each track id to its mean energy (dB) inside
    ``band_hz``; ``dominant_track`` is the loudest there. Track roles
    (kick/bass/sample) are identified by track id/name.
    """

    band_hz: tuple[float, float] = (40.0, 150.0)
    energy_db: dict[IdStr, float] = Field(default_factory=dict)
    dominant_track: IdStr | None = None


class TrackAlignment(ContractModel):
    """How far this track's transients sit off the project grid."""

    mean_offset_ms: float
    max_abs_offset_ms: float = Field(ge=0.0)
    n_transients: int = Field(ge=0)


class AlignmentReport(ContractModel):
    tracks: dict[IdStr, TrackAlignment] = Field(default_factory=dict)
    max_abs_offset_ms: float = Field(ge=0.0)


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


class ClickEvent(ContractModel):
    """A suspected click/discontinuity at a chop or clip edge."""

    track: IdStr | None = None
    at_s: float = Field(ge=0.0)
    severity: float = Field(ge=0.0, le=1.0)


class ClickReport(ContractModel):
    count: int = Field(ge=0)
    events: list[ClickEvent] = Field(default_factory=list)


class StyleScore(ContractModel):
    reference: str = Field(description="Reference identifier, e.g. a path or name.")
    score: float = Field(ge=0.0, le=1.0)


class StyleSimilarity(ContractModel):
    """Optional (spec 10): CLAP/MERT similarity against reference tracks."""

    model: str = Field(min_length=1, description="e.g. 'laion-clap', 'mert'.")
    references: list[StyleScore] = Field(default_factory=list)
    mean_score: float | None = Field(default=None, ge=0.0, le=1.0)


class Alarm(ContractModel):
    """A computed heads-up for the agent ("metrics are alarms")."""

    code: str = Field(min_length=1, description="Stable machine code, e.g. 'clipping'.")
    severity: Literal["info", "warn", "error"]
    message: str = Field(min_length=1)
    track: IdStr | None = None
    at_s: float | None = Field(default=None, ge=0.0)


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
