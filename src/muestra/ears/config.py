"""Ears configuration: thresholds, spectral reference profiles, ears.toml.

Every threshold the analyzer uses lives in :class:`Thresholds`, with the
rationale in ``docs/ears.md``. A project may override any of them, and
pick or define the spectral reference, in an optional ``ears.toml`` at
the project root (git-versioned like muestra.toml)::

    [spectral]
    profile = "dance"            # built-in: "dance" (default) | "pink"
    # or a custom reference (relative levels per detail band, dB):
    # levels_db = {sub = 6, bass = 5, low_mid = -1, mid = 0,
    #              high_mid = -5, presence = -7, air = -8}
    # tolerance_db = {sub = 8, air = 8}   # per band; others use the default
    # default_tolerance_db = 6

    [thresholds]
    timing_warn_ms = 15.0        # any Thresholds field

Unknown keys are errors (same rule as every other contract file).
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

from pydantic import Field, ValidationError

from ..errors import MuestraError, issues_from_validation_error
from ..schemas._base import ContractModel
from ..schemas.metrics import DETAIL_BAND_EDGES_HZ, SpectralReference

__all__ = [
    "EARS_CONFIG_FILE",
    "Thresholds",
    "EarsConfig",
    "PROFILES",
    "slope_profile",
    "load_config",
]

EARS_CONFIG_FILE = "ears.toml"


@dataclass(frozen=True)
class Thresholds:
    """Alarm thresholds. Units are in the field names."""

    # loudness
    target_warn_lu: float = 1.5  # |integrated - target| beyond this: warn
    target_error_lu: float = 4.0
    true_peak_warn_dbtp: float = -0.5  # above this the safety limiter was not effective
    plr_warn_db: float = 6.0  # peak-to-loudness ratio below this: brick-walled master
    plr_error_db: float = 4.0
    clip_run_error: int = 1  # >= this many flat-topped clipped samples: error
    # spectral
    spectral_default_tolerance_db: float = 6.0
    # low end (40-150 Hz)
    collision_margin_db: float = 6.0  # other within this of the kick in its window: collision
    collision_warn_share: float = 0.25  # share of kick hits colliding: warn
    collision_error_share: float = 0.6  # ...and median margin >= 0 dB: error
    low_end_min_level_db: float = -30.0  # relative to the mix's own low band; quieter = ignore
    # sidechain
    duck_min_db: float = 1.5  # measured dip below this: "missing"
    duck_fraction: float = 0.4  # measured < fraction * expected: "weak"
    duck_min_triggers: int = 4
    # timing
    timing_warn_ms: float = 15.0  # systematic (median) offset of a tight cluster
    timing_error_ms: float = 30.0
    timing_max_mad_ms: float = 8.0  # cluster must be at least this tight to trust it
    timing_min_onsets: int = 8
    timing_min_pulse_share: float = 0.3
    timing_drift_warn_ms: float = 20.0
    timing_clip_warn_ms: float = 20.0  # one clip instance deviates from the track median
    # WP12: a clip-track offset is only alarmed when the estimator, on this very
    # material, measures a known ±probe shift correctly; otherwise an info note says
    # the timing could not be verified.
    timing_probe_ms: float = 20.0  # the shift the self-test applies (± this many ms)
    timing_max_window_spread_ms: float = 5.0  # allowed miss on that known shift
    timing_dense_onsets_per_beat: float = 3.0  # reported in the note; not a gate by itself
    swing_info_ms: float = 12.0  # sample off-beat 16ths late vs drum pattern swing
    # tonal
    key_min_confidence: float = 0.25
    detune_warn_cents: float = 20.0
    detune_error_cents: float = 35.0
    tuning_min_strength: float = 0.15
    bass_clash_warn_share: float = 0.3
    out_of_key_warn_share: float = 0.35
    # clicks
    click_ratio: float = 8.0  # edge prediction error vs the 5 ms before the edge
    click_min_db: float = -54.0  # absolute size of the jump (2nd difference), dBFS
    click_edge_window_ms: float = 0.06  # search +- around the expected edge (~3 samples)
    click_error_db: float = -30.0
    # an instrument note that rises this much above what was sounding is a new
    # attack (the preset's own transient, like a drum hit), not a click; measured
    # on Surge 'FM Slap' onsets out of a release tail: +17.3..+17.6 dB (WP11).
    click_onset_rise_db: float = 12.0
    # WP12: a note start over a sounding note is the preset's attack when its jump is
    # within this many dB of the track's own attacks out of silence (median).
    click_attack_margin_db: float = 6.0
    # technical
    dc_warn_db: float = -45.0
    low_corr_warn: float = 0.5
    mono_loss_warn_db: float = 3.0
    silent_peak_db: float = -70.0
    inaudible_margin_db: float = 20.0  # track this far below the rest in every band
    inaudible_max_share: float = 0.05  # ...in all but this share of its active frames


def slope_profile(
    psd_slope_db_per_oct: float, lifts_db: dict[str, float] | None = None
) -> dict[str, float]:
    """Reference levels (dB, relative to 'mid') for a PSD falling
    ``psd_slope`` dB/octave, plus optional per-band lifts.

    Band power = integral of f**-alpha over each detail band
    (alpha = -slope / 3.0103; -3.01 dB/oct = pink = equal power per octave).
    """
    alpha = -psd_slope_db_per_oct / (10.0 * math.log10(2.0))
    levels: dict[str, float] = {}
    for band, (lo, hi) in DETAIL_BAND_EDGES_HZ.items():
        if abs(alpha - 1.0) < 1e-9:
            p = math.log(hi / lo)
        else:
            p = (hi ** (1.0 - alpha) - lo ** (1.0 - alpha)) / (1.0 - alpha)
        levels[band] = 10.0 * math.log10(p)
    ref = levels["mid"]
    lifts = lifts_db or {}
    return {b: round(v - ref + lifts.get(b, 0.0), 1) for b, v in levels.items()}


def _tol(default: float, **overrides: float) -> dict[str, float]:
    return {b: overrides.get(b, default) for b in DETAIL_BAND_EDGES_HZ}


#: Built-in references (docs/ears.md explains the choice; NOT validated
#: against commercial masters — tune per project in ears.toml):
#:
#: * "dance" (default): pink (equal power per octave) through the mids,
#:   the usual long-term balance of mixed music, with +4 dB in sub/bass
#:   (four-on-the-floor kick + bass own the low end) and -3 dB of air
#:   (masters roll off above ~8 kHz). Tolerance 6 dB, 8 dB at the
#:   extremes (sub, presence, air), which vary most between good mixes.
#: * "pink": plain pink noise, a neutral sanity reference.
PROFILES: dict[str, SpectralReference] = {
    "dance": SpectralReference(
        name="dance",
        levels_db=slope_profile(-3.0103, {"sub": 4.0, "bass": 4.0, "air": -3.0}),
        tolerance_db=_tol(6.0, sub=8.0, presence=8.0, air=8.0),
    ),
    "pink": SpectralReference(
        name="pink",
        levels_db=slope_profile(-3.0103),
        tolerance_db=_tol(6.0, sub=8.0, presence=8.0, air=8.0),
    ),
}


class _SpectralSection(ContractModel):
    profile: str | None = None
    levels_db: dict[str, float] | None = None
    tolerance_db: dict[str, float] = Field(default_factory=dict)
    default_tolerance_db: float | None = Field(default=None, gt=0.0)
    name: str | None = None


class _EarsToml(ContractModel):
    spectral: _SpectralSection = Field(default_factory=_SpectralSection)
    thresholds: dict[str, float] = Field(default_factory=dict)


@dataclass(frozen=True)
class EarsConfig:
    reference: SpectralReference = field(default_factory=lambda: PROFILES["dance"])
    thresholds: Thresholds = field(default_factory=Thresholds)


def _fail(message: str, hint: str) -> MuestraError:
    return MuestraError.one("invalid_value", message, path=EARS_CONFIG_FILE, hint=hint)


def load_config(project_root: Path) -> EarsConfig:
    """Load ``ears.toml`` when present; defaults otherwise."""
    path = project_root / EARS_CONFIG_FILE
    if not path.is_file():
        return EarsConfig()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise MuestraError.one(
            "parse_error",
            f"Invalid TOML in {EARS_CONFIG_FILE}: {e}",
            path=EARS_CONFIG_FILE,
            hint="Fix the TOML syntax.",
        ) from e
    try:
        doc = _EarsToml.model_validate(raw)
    except ValidationError as e:
        raise MuestraError(issues_from_validation_error(e, path_prefix=EARS_CONFIG_FILE)) from e

    sp = doc.spectral
    bands = set(DETAIL_BAND_EDGES_HZ)
    if sp.levels_db is not None:
        if set(sp.levels_db) != bands:
            raise _fail(
                "[spectral].levels_db must give every detail band.",
                "Bands: " + ", ".join(DETAIL_BAND_EDGES_HZ),
            )
        default_tol = sp.default_tolerance_db or Thresholds.spectral_default_tolerance_db
        reference = SpectralReference(
            name=sp.name or "custom",
            levels_db=dict(sp.levels_db),
            tolerance_db=_tol(default_tol),
        )
    else:
        pname = sp.profile or "dance"
        if pname not in PROFILES:
            raise _fail(
                f"Unknown spectral profile {pname!r}.",
                "Use one of: " + ", ".join(PROFILES) + ", or give levels_db.",
            )
        reference = PROFILES[pname]
        if sp.default_tolerance_db is not None:
            reference = reference.model_copy(update={"tolerance_db": _tol(sp.default_tolerance_db)})
    unknown = set(sp.tolerance_db) - bands
    if unknown:
        raise _fail(
            f"Unknown band(s) in [spectral].tolerance_db: {sorted(unknown)}.",
            "Bands: " + ", ".join(DETAIL_BAND_EDGES_HZ),
        )
    if sp.tolerance_db:
        reference = reference.model_copy(
            update={"tolerance_db": {**reference.tolerance_db, **sp.tolerance_db}}
        )

    valid = {f.name: f.type for f in fields(Thresholds)}
    bad = sorted(set(doc.thresholds) - set(valid))
    if bad:
        raise _fail(
            f"Unknown threshold(s) in [thresholds]: {bad}.",
            "See muestra.ears.config.Thresholds for the names.",
        )
    coerced = {
        k: (int(v) if valid[k] in ("int", int) else float(v)) for k, v in doc.thresholds.items()
    }
    return EarsConfig(reference=reference, thresholds=replace(Thresholds(), **coerced))
