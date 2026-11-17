"""Every fixture in fixtures/invalid/ must produce its expected error
(path + code) with a fix hint, alongside whatever else is wrong."""

from __future__ import annotations

import shutil

import pytest

from muestra.project import create_project, open_project, validate_song

from ..conftest import INVALID_DIR

# filename -> (expected issue path, expected code)
EXPECTED = {
    "unknown-field.yaml": ("meta.tempo", "unknown_field"),
    "bad-ref-chop.yaml": ("tracks[0].clips[0].chop", "ref_not_found"),
    "bad-library-uri.yaml": ("tracks[0].pattern.lanes.kick.sample", "invalid_uri"),
    "duplicate-ids.yaml": ("tracks[1].id", "duplicate_id"),
    "clip-overrun.yaml": ("tracks[0].clips[0].repeat", "clip_overrun"),
    "bad-automation-target.yaml": ("automation[0].target", "ref_not_found"),
    "bad-pattern-steps.yaml": ("tracks[0].pattern.lanes.kick.steps", "pattern_length"),
    "track-kind-conflict.yaml": ("tracks[0]", "track_kind"),
    "instrument-no-midi.yaml": ("tracks[0]", "track_kind"),
    "bad-downbeat.yaml": ("chops[0].start_downbeat", "too_small"),
    "bad-sidechain.yaml": ("tracks[0].fx[0].key_track", "ref_not_found"),
    "non-numeric-automation.yaml": ("automation[0].target", "automation_target"),
    "exp-curve-sign.yaml": ("automation[0].points", "automation_target"),
}


def test_every_invalid_fixture_is_covered():
    files = {p.name for p in INVALID_DIR.glob("*.yaml")}
    assert files == set(EXPECTED), (
        f"fixtures/invalid and EXPECTED are out of sync: "
        f"missing expectations for {files - set(EXPECTED)}, "
        f"missing files for {set(EXPECTED) - files}"
    )


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_invalid_fixture(tmp_path, name):
    dst = tmp_path / "proj"
    create_project(dst)
    shutil.copy(INVALID_DIR / name, dst / "song.yaml")
    issues = validate_song(open_project(dst), check_files=False)
    exp_path, exp_code = EXPECTED[name]
    matches = [i for i in issues if i.path == exp_path and i.code == exp_code]
    assert matches, (
        f"{name}: expected issue ({exp_path}, {exp_code}); got {[(i.path, i.code) for i in issues]}"
    )
    issue = matches[0]
    assert issue.hint, f"{name}: issue has no fix hint"
    assert issue.message, f"{name}: issue has no message"
