"""WP18: mix balance — the relative loudness of the roles in the mix.

Method under test: each role is measured **while it sounds** — K-weighted
power inside the pattern's own hit windows for drum lanes, self-gated
active blocks for sustained tracks — on the pre-master stems, so a
sparse kick and a continuous sample are comparable (whole-file RMS is
not: it charges the kick for its silence).
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from muestra.ears.balance import measure_balance
from muestra.ears.context import load_context
from muestra.ears.core import analyze, compare
from muestra.ears.corpus import CorpusSpec, Defects, generate
from muestra.project import open_project

from .conftest import codes

FAST = {"sr": 24000, "bars": 8}


def _balance_codes(m) -> set[str]:
    return {a.code for a in m.alarms if a.code.startswith("balance") and a.severity != "info"}


def test_clean_mix_reports_balance_and_stays_quiet(render):
    _, m = render()
    assert m.balance is not None
    assert set(m.balance.roles) == {"kick", "drums", "bass", "sample", "other"}
    readout = next(a for a in m.alarms if a.code == "balance")
    assert readout.severity == "info" and readout.message.startswith("balance:")
    assert _balance_codes(m) == set()
    # the readout is inlined in every summary, alarms or not
    assert "balance:" in m.summary


def test_sample_dominating_15_db(render):
    _, m = render(sample_gain_db=15)
    rel = m.balance.levels_db
    assert m.balance.loudest == "sample"
    assert "balance_buried" in codes(m)
    assert all(a.severity == "error" for a in m.alarms if a.code == "balance_buried")
    assert "balance_dominant" in codes(m)
    assert rel["kick"] <= -15 and rel["drums"] <= -15


def test_sample_dominating_30_db(render):
    _, m = render(sample_gain_db=30)
    assert "balance_buried" in codes(m)
    assert m.balance.levels_db["kick"] <= -30


def test_sample_mildly_dominant_warns(render):
    """~12 dB of sample over the rhythm: warn, not error."""
    _, m = render(sample_gain_db=8)
    assert {"balance_kick_back", "balance_bass_back"} & _balance_codes(m)
    assert all(
        a.severity == "warn"
        for a in m.alarms
        if a.code in ("balance_kick_back", "balance_bass_back")
    )


def test_bass_dominating(render):
    _, m = render(bass_gain_db=18)
    assert m.balance.loudest == "bass"
    assert "balance_buried" in codes(m)  # kick + rest of kit under the bass


def test_bass_over_the_kick_warns(render):
    """Bass louder than the kick but nothing buried: warn only."""
    _, m = render(bass_gain_db=11)
    assert "balance_bass_front" in _balance_codes(m)
    assert "balance_buried" not in _balance_codes(m)


def test_drums_too_loud(render):
    _, m = render(drums_gain_db=18)
    assert m.balance.loudest in ("kick", "drums")
    # the loop ends up inaudible next to the kit
    assert {"balance_sample_back", "balance_buried"} & _balance_codes(m)


def test_sparse_kick_is_not_penalised(render):
    """Duty-cycle invariance: one hit/bar measures like four hits/bar.

    Equal-level construction: the same one-shot at the same gain —
    only the hit count changes. Whole-file RMS would call the sparse
    kick ~6 dB quieter (4x less energy); the hit-window measure must
    stay within ~1 dB.
    """
    _, dense = render()
    _, sparse = render(sparse_kick=True)
    assert abs(
        sparse.balance.roles["kick"].level_db - dense.balance.roles["kick"].level_db
    ) <= 1.5


def test_sparse_kick_vs_continuous_pad_equal_level(tmp_path):
    """A sparse kick and a continuous pad at equal perceived level.

    Construction: the pad stem is the kick lane's own one-shot looped
    back-to-back — literally the same sound, never silent. "Equal
    perceived level" = equal loudness while sounding. The readout must
    put the two roles within ~2 dB, even though their whole-file RMS
    differs by ~9 dB (the kick is silent 85% of the time at 1 hit/bar).
    """
    root = tmp_path / "p"
    generate(
        CorpusSpec(sr=24000, bars=8, defects=Defects(sparse_kick=True)), root
    )
    vdir = root / "renders" / "v001" / "stems"
    kick_st, sr = sf.read(vdir / "drums.kick.wav", dtype=np.float64)
    one_shot = kick_st[: int(0.35 * sr)]  # the 0.3 s one-shot + a margin
    pad = np.tile(one_shot, (int(np.ceil(len(kick_st) / len(one_shot))), 1))[: len(kick_st)]
    sf.write(vdir / "pad.wav", pad.astype(np.float32), sr, subtype="FLOAT")
    m = analyze(open_project(root), "v001")

    kick = m.balance.roles["kick"].level_db
    pad_lvl = m.balance.roles["other"].level_db
    # whole-file RMS is NOT fair: it differs by the duty cycle
    rms = lambda x: 10 * np.log10(np.mean(x**2))
    assert abs(rms(kick_st) - rms(pad)) > 6.0
    # the active measure is: same sound while sounding, same level
    assert abs(kick - pad_lvl) <= 2.0


def test_thresholds_overridable_in_ears_toml(tmp_path):
    root = tmp_path / "p"
    generate(CorpusSpec(defects=Defects(sample_gain_db=15), **FAST), root)
    (root / "ears.toml").write_text(
        "[thresholds]\nbalance_buried_db = 40\nbalance_dominant_db = 40\n",
        encoding="utf-8",
    )
    m = analyze(open_project(root), "v001")
    assert "balance_buried" not in codes(m)
    assert "balance_dominant" not in codes(m)
    # and absurdly strict thresholds fire on the clean mix
    (root / "ears.toml").write_text(
        "[thresholds]\nbalance_back_db = 2\n", encoding="utf-8"
    )
    m = analyze(open_project(root), "v001")
    assert _balance_codes(m)


def test_compare_reports_balance_change(tmp_path):
    root = tmp_path / "p"
    generate(CorpusSpec(sr=24000, bars=8), root)
    generate(
        CorpusSpec(sr=24000, bars=8, defects=Defects(sample_gain_db=15)),
        root,
        version="v002",
    )
    project = open_project(root)
    d = compare(project, "v001", "v002")
    assert d.balance_delta_db["sample"] > 10
    assert any("sample" in n and "respecto" in n for n in d.notes)


def test_balance_check_is_cheap(tmp_path):
    """The added check must cost well under 0.3 s on a 16-bar render."""
    root = tmp_path / "p"
    generate(CorpusSpec(sr=48000, bars=16), root)
    project = open_project(root)
    ctx = load_context(project, "v001")
    measure_balance(ctx)  # warm-up
    t0 = time.perf_counter()
    measure_balance(ctx)
    elapsed = time.perf_counter() - t0
    print(f"\nbalance check: {elapsed * 1000:.0f} ms on 16 bars @ 48 kHz")
    assert elapsed < 0.3
